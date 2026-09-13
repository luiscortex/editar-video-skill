"""Caption A Clean — face-aware subtitle burn-in.

Renderiza legendas palavra-por-bloco sobre o vídeo usando PIL para texto
e cv2 para leitura de frames. Frames BGR são escritos diretamente no stdin
do ffmpeg via pipe (sem PNG intermediário em disco), o que elimina I/O de
arquivo e melhora performance ~5x.

Dependências: Pillow, opencv-python (já no venv). Sem dependências novas.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FONT_SIZE = 48          # pixels (referência 1080×1920)
MARGIN_V_DEFAULT = 320  # px do rodapé — respeita safe zone IG (~280px de UI no fundo: caption+reactions+loading bar) + buffer
MARGIN_V_TOP_DEFAULT = 200  # px do topo (~15% do frame 1280) — usado quando caption_position="top"
OUTLINE_WIDTH = 3       # px do contorno preto
TEXT_COLOR = (255, 255, 255)  # branco RGB
OUTLINE_COLOR = (0, 0, 0)    # preto RGB


# ---------------------------------------------------------------------------
# Layout → caption position derivation (v2.2.0)
# ---------------------------------------------------------------------------

# Layouts em que insert (mock/UI) cobre topo/centro/inteiro do frame → legenda vai pro TOP
CAPTION_TOP_LAYOUTS = {
    "overlay-fullframe",
    "overlay-middle-card",
    "overlay-bottom-card",
    "full-frame-puro",
    # aliases v2.1:
    "fullframe",
}

# Layouts em que insert fica no topo (split-* ou overlay-top-card) → legenda fica no BOTTOM (safe zone IG)
CAPTION_BOTTOM_LAYOUTS = {
    "overlay-top-card",
    "split-60-40",
    "split-50-50",
    "split-70-30",
    "split-80-20",
    # aliases v2.1:
    "60/40",
    "50/50",
    "70/30",
    "80/20",
}


def derive_caption_position(style_config: dict) -> str:
    """Deriva 'top' ou 'bottom' do layout_default em style_config.

    Regras v2.2.0:
    - Se user setou caption_position explicitamente → respeita override.
    - Senão deriva do layout_default conforme tabela:
      * overlay-fullframe / overlay-middle-card / overlay-bottom-card / full-frame-puro → TOP
      * overlay-top-card / split-* → BOTTOM (safe zone IG)
    - Default seguro pra layout desconhecido = BOTTOM (comportamento legado).
    """
    explicit = style_config.get("caption_position")
    if explicit in ("top", "bottom"):
        return explicit
    layout = style_config.get("layout_default") or style_config.get("split") or "full-frame-puro"
    if layout in CAPTION_TOP_LAYOUTS:
        return "top"
    if layout in CAPTION_BOTTOM_LAYOUTS:
        return "bottom"
    return "bottom"


# ---------------------------------------------------------------------------
# Font detection
# ---------------------------------------------------------------------------

def _detect_font() -> tuple[str, Optional[str]]:
    """Retorna (font_path, fontsdir_or_None) para uso no PIL.

    Tenta Plus Jakarta Sans (variable font) primeiro; fallback Helvetica Neue Bold.
    """
    # Plus Jakarta Sans variable font (instalada em ~/Library/Fonts/)
    pjs_variable = Path.home() / "Library" / "Fonts" / "PlusJakartaSans[wght].ttf"
    if pjs_variable.exists():
        return str(pjs_variable), str(pjs_variable.parent)

    # Candidatos estáticos Plus Jakarta Sans
    pjs_candidates = [
        Path.home() / "Library" / "Fonts" / "PlusJakartaSans-ExtraBold.ttf",
        Path.home() / "Library" / "Fonts" / "PlusJakartaSans-Bold.ttf",
        Path("/Library/Fonts/PlusJakartaSans-ExtraBold.ttf"),
        Path("/Library/Fonts/PlusJakartaSans-Bold.ttf"),
    ]
    for p in pjs_candidates:
        if p.exists():
            return str(p), str(p.parent)

    # Fallback: Helvetica Neue Bold (index=1 na ttc do macOS)
    helvetica_ttc = Path("/System/Library/Fonts/HelveticaNeue.ttc")
    if helvetica_ttc.exists():
        print(
            "WARN: Plus Jakarta Sans não encontrada — usando Helvetica Neue Bold como fallback. "
            "Pra match exato, instala Plus Jakarta Sans.",
            file=sys.stderr,
        )
        return str(helvetica_ttc), None

    # Último recurso: fonte padrão do PIL
    print(
        "WARN: Nenhuma fonte preferida encontrada — usando fonte padrão do PIL.",
        file=sys.stderr,
    )
    return "", None


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Carrega a fonte com o tamanho dado.

    Para Plus Jakarta Sans variable font, ativa weight 800 (ExtraBold).
    Para HelveticaNeue.ttc, usa index=1 (Bold).
    """
    font_path, _ = _detect_font()
    if not font_path:
        return ImageFont.load_default()
    try:
        if font_path.endswith(".ttc"):
            return ImageFont.truetype(font_path, size, index=1)
        font = ImageFont.truetype(font_path, size)
        # Variable font: ativa ExtraBold (wght=800)
        if "[wght]" in font_path:
            try:
                font.set_variation_by_name("ExtraBold")
            except Exception:
                try:
                    font.set_variation_by_axes([800])
                except Exception:
                    pass  # fica no peso padrão
        return font
    except Exception as e:
        print(f"WARN: Erro ao carregar fonte {font_path}: {e} — usando padrão", file=sys.stderr)
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Transcript → caption blocks
# ---------------------------------------------------------------------------

def _load_words(transcript_path: Path) -> list[SimpleNamespace]:
    data = json.loads(transcript_path.read_text())
    words = []
    for w in data.get("words", []):
        words.append(SimpleNamespace(
            word=w["word"],
            start=float(w["start"]),
            end=float(w["end"]),
        ))
    return words


def _make_blocks(words: list[SimpleNamespace], words_per_block: int) -> list[SimpleNamespace]:
    """Agrupa words em blocos de N palavras."""
    blocks = []
    for i in range(0, len(words), words_per_block):
        chunk = words[i : i + words_per_block]
        blocks.append(SimpleNamespace(
            words=chunk,
            start=chunk[0].start,
            end=chunk[-1].end,
            text=" ".join(w.word for w in chunk),
        ))
    return blocks


def smooth_block_ends(blocks: list[SimpleNamespace]) -> list[SimpleNamespace]:
    """Elimina gap entre blocos consecutivos colando block[i].end = block[i+1].start.

    Evita flicker (frame sem legenda) entre blocos.
    """
    for i in range(len(blocks) - 1):
        blocks[i].end = blocks[i + 1].start
    return blocks


# ---------------------------------------------------------------------------
# Face-aware MarginV
# ---------------------------------------------------------------------------

def compute_margin_v(
    block: SimpleNamespace,
    face_zones: list[dict],
    video_height: int,
    font: ImageFont.FreeTypeFont,
    text: str,
) -> int:
    """Calcula MarginV (pixels do BOTTOM do frame) para um bloco de legenda.

    Usa text_h REAL medido do font+texto (não estimativa constante), garantindo
    respiro exato de 40px acima do rosto.

    Lógica:
    - MarginV é a distância entre o BOTTOM do frame e o BOTTOM do texto
      (comportamento ASS Alignment=2 bottom-center).
    - Para não cobrir o rosto, queremos que o TOPO do texto fique ABAIXO de
      face_bottom_px (coordenada Y do rodapé da bbox do rosto).
    - Relação:
        topo_do_texto = video_height - MarginV - text_h_real
        Condição: topo_do_texto >= face_bottom_px + 40
        → MarginV <= video_height - face_bottom_px - 40 - text_h_real
    - Clamp: 60 <= MarginV <= video_height // 2
    """
    # Mede text_h real para este bloco/fonte
    try:
        bbox = font.getbbox(text)
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_h = 60  # fallback conservador

    # Filtra zones que se sobrepõem ao intervalo do bloco
    relevant = [
        z for z in face_zones
        if z.get("face") is not None
        and block.start <= z["t"] <= block.end
    ]

    if not relevant:
        return MARGIN_V_DEFAULT

    # Pega o maior face_bottom normalizado na janela (zona mais baixa = mais crítica)
    max_face_bottom_norm = max(
        z["face"]["y"] + z["face"]["h"]
        for z in relevant
    )
    face_bottom_px = max_face_bottom_norm * video_height
    safe_margin_from_top = face_bottom_px + 40  # 40px de respiro acima do texto

    # MarginV = video_height - safe_margin_from_top - text_h_real
    margin_v = int(video_height - safe_margin_from_top - text_h)

    # Clamp: MARGIN_V_DEFAULT (safe zone IG 280+buffer) é o MÍNIMO absoluto.
    # Mesmo que face-aware queira colocar mais baixo, respeita a safe zone IG —
    # melhor cobrir um pedaço de rosto do que ter legenda escondida pela UI do app.
    margin_v = max(MARGIN_V_DEFAULT, min(margin_v, video_height // 2))
    return margin_v


def compute_margin_v_top(
    block: SimpleNamespace,
    face_zones: list[dict],
    video_height: int,
    font: ImageFont.FreeTypeFont,
    text: str,
) -> int:
    """Calcula MarginV-from-TOP (pixels do TOPO do frame até o TOPO da 1ª linha).

    Usada quando caption_position="top" (layouts overlay-fullframe/middle-card/
    bottom-card/full-frame-puro — onde insert cobre o centro/inteiro do frame
    e legenda precisa ficar no topo pra não competir com o conteúdo visual).

    Lógica face-aware:
    - Default: MARGIN_V_TOP_DEFAULT (200px = ~15% do frame 1280).
    - Se rosto detectado tem TOPO acima do default (face_top_px < default+text_h),
      empurra legenda pra baixo do rosto+40px de respiro? NÃO — no caption-top
      o rosto geralmente fica embaixo (talking head). Em vez disso, garantimos
      que o BOTTOM do texto não invada a zona do rosto:
        bottom_do_texto = MarginV + text_h_real
        Condição: bottom_do_texto <= face_top_px - 40
        → MarginV <= face_top_px - 40 - text_h_real
    - Clamp: 40 <= MarginV <= face_top_px - 40 - text_h (mas nunca empurra
      além do TOPO real do frame).
    """
    try:
        bbox = font.getbbox(text)
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_h = 60

    relevant = [
        z for z in face_zones
        if z.get("face") is not None
        and block.start <= z["t"] <= block.end
    ]

    if not relevant:
        return MARGIN_V_TOP_DEFAULT

    # Menor face_top normalizado na janela (rosto mais alto = mais crítico p/ top caption)
    min_face_top_norm = min(
        z["face"]["y"]
        for z in relevant
    )
    face_top_px = min_face_top_norm * video_height
    max_allowed = int(face_top_px - 40 - text_h)

    margin_v = MARGIN_V_TOP_DEFAULT
    if max_allowed < margin_v:
        # Rosto está alto demais — empurra legenda pra cima respeitando margem mínima 40px
        margin_v = max(40, max_allowed)
    return margin_v


# ---------------------------------------------------------------------------
# PIL text rendering helpers
# ---------------------------------------------------------------------------

SIDE_MARGIN = 60  # px de margem lateral — força wrap em textos > w-120px (720-120=600 disponíveis)
LINE_GAP = 6      # px de gap entre linhas em legenda multi-linha


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Quebra texto em linhas que cabem em max_width pixels.

    Algoritmo greedy: vai juntando palavras até estourar, aí quebra.
    Garante que cada linha individual cabe em max_width (mesmo 1 palavra
    super longa fica numa linha só — não tenta hífen).
    """
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current: list[str] = []
    for w in words:
        candidate = " ".join(current + [w]) if current else w
        bbox = font.getbbox(candidate)
        cand_w = bbox[2] - bbox[0]
        if cand_w > max_width and current:
            # Fecha linha atual e começa nova com a palavra que estourou
            lines.append(" ".join(current))
            current = [w]
        else:
            current.append(w)
    if current:
        lines.append(" ".join(current))
    return lines


def _render_caption_frame(
    frame_bgr: np.ndarray,
    text: str,
    margin_v: int,
    font: ImageFont.FreeTypeFont,
    position: str = "bottom",
) -> np.ndarray:
    """Renderiza caption text sobre frame BGR do cv2. Retorna frame BGR com texto.

    Word-wrap automático: se texto estoura w - 2*SIDE_MARGIN, quebra em N linhas
    empilhadas.

    position="bottom" (default, comportamento legado):
        Ancora pelo BOTTOM da última linha. margin_v = pixels do BOTTOM do frame
        ao BOTTOM do texto. y_first_line_top = h - margin_v - total_h.

    position="top" (v2.2.0, derivado de layout_default):
        Ancora pelo TOPO da primeira linha. margin_v = pixels do TOPO do frame
        ao TOPO do texto. y_first_line_top = margin_v.

    Otimizações:
    1. stroke_width nativo do PIL (C) em vez de loop Python 8-direções — ~1.7x mais rápido.
    2. Strip rendering: PIL processa apenas a faixa vertical do texto multi-linha
       em vez do frame full — ~8x mais rápido no total.
    """
    h, w = frame_bgr.shape[:2]
    max_text_width = w - 2 * SIDE_MARGIN  # 720-80 = 640px disponíveis pra texto

    # Quebra em linhas (1 linha se cabe, N linhas se estoura)
    lines = _wrap_text(text, font, max_text_width)

    # Mede altura de uma linha (todas têm mesma altura — mesma font)
    line_bbox = font.getbbox("Ágpqj")  # chars com descender pra altura full
    line_h = line_bbox[3] - line_bbox[1]
    total_h = len(lines) * line_h + (len(lines) - 1) * LINE_GAP

    # Posição Y: depende de position (top vs bottom anchoring)
    if position == "top":
        # Top-anchored: primeira linha tem TOPO em margin_v
        y_first_line_top = margin_v
    else:
        # Bottom-anchored: última linha tem BOTTOM em (h - margin_v)
        y_first_line_top = h - margin_v - total_h

    # Strip: faixa vertical cobrindo TODAS as linhas
    pad = OUTLINE_WIDTH + 2
    y_strip = max(0, y_first_line_top - pad)
    y_strip_end = min(h, y_first_line_top + total_h + pad)

    strip_bgr = frame_bgr[y_strip:y_strip_end, :]
    img = Image.fromarray(strip_bgr[:, :, ::-1])
    draw = ImageDraw.Draw(img)

    # Desenha cada linha centralizada
    for i, line in enumerate(lines):
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        x = (w - line_w) // 2  # centralizada
        y_line = y_first_line_top + i * (line_h + LINE_GAP) - y_strip
        draw.text(
            (x, y_line),
            line,
            font=font,
            fill=TEXT_COLOR,
            stroke_width=OUTLINE_WIDTH,
            stroke_fill=OUTLINE_COLOR,
        )

    frame_bgr[y_strip:y_strip_end, :] = np.array(img)[:, :, ::-1]
    return frame_bgr


# ---------------------------------------------------------------------------
# Video probe
# ---------------------------------------------------------------------------

def _probe_video(video_path: Path) -> dict:
    """Retorna dict com width, height, fps, has_audio."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams",
        "-of", "json",
        str(video_path),
    ]
    out = subprocess.check_output(cmd, text=True)
    data = json.loads(out)
    streams = data.get("streams", [])

    result = {"width": 1080, "height": 1920, "fps": 25.0, "has_audio": False}
    for s in streams:
        if s.get("codec_type") == "video":
            result["width"] = s.get("width", 1080)
            result["height"] = s.get("height", 1920)
            fps_str = s.get("r_frame_rate", "25/1")
            try:
                num, den = fps_str.split("/")
                result["fps"] = float(num) / float(den)
            except Exception:
                result["fps"] = 25.0
        if s.get("codec_type") == "audio":
            result["has_audio"] = True
    return result


# ---------------------------------------------------------------------------
# Main pipeline — pipe stdin pro ffmpeg (P1 #1)
# ---------------------------------------------------------------------------

def _parse_suppress_intervals(raw: str) -> list[tuple[float, float]]:
    """Parseia string "0-3.5,50-54" em lista de (start, end) tuples."""
    if not raw or not raw.strip():
        return []
    intervals = []
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            intervals.append((float(a), float(b)))
    return intervals


def _in_suppress(t: float, intervals: list[tuple[float, float]]) -> bool:
    """Retorna True se timestamp t está dentro de algum intervalo de supressão."""
    for start, end in intervals:
        if start <= t < end:
            return True
    return False


def burn_captions(
    input_path: Path,
    transcript_path: Path,
    face_zones_path: Path,
    output_path: Path,
    words_per_block: int = 2,  # 2 palavras/bloco = legenda sempre cabe em 1 linha em frame 720, evita word-wrap feio
    suppress_intervals: list[tuple[float, float]] | None = None,
    position: str = "bottom",  # "top" ou "bottom" — derivado do layout_default (v2.2.0)
) -> int:
    """Pipeline principal de burn-in de legendas via pipe stdin → ffmpeg.

    Pipeline:
    cv2.VideoCapture lê frame BGR →
    PIL desenha texto (in-place no array via axis swap) →
    escreve raw BGR no stdin do ffmpeg →
    ffmpeg encoda H.264 com x264 nativo + mux com áudio do input original.

    Returns 0 em sucesso, 1 em erro.
    """
    # 1. Carrega inputs
    words = _load_words(transcript_path)
    face_zones = json.loads(face_zones_path.read_text())
    info = _probe_video(input_path)
    video_height = info["height"]
    video_width = info["width"]
    fps = info["fps"]

    # Edge case: transcript vazio → copia input para output
    if not words:
        print("WARN: transcript vazio (0 palavras) — copiando input sem legenda.", file=sys.stderr)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(input_path, output_path)
        return 0

    # 2. Agrupa em blocos e suaviza gaps (P2 #4)
    blocks = _make_blocks(words, words_per_block)
    blocks = smooth_block_ends(blocks)

    # 3. Carrega fonte (Plus Jakarta Sans ExtraBold se disponível)
    font = _load_font(FONT_SIZE)

    # 4. Pré-calcula MarginV por bloco usando text_h real (P1 #2)
    # v2.2.0: usa compute_margin_v_top se position="top"
    if position == "top":
        for block in blocks:
            block.margin_v = compute_margin_v_top(
                block, face_zones, video_height, font, block.text
            )
        print(f"  [position] caption=TOP, MarginV-from-top default={MARGIN_V_TOP_DEFAULT}px", file=sys.stderr)
    else:
        for block in blocks:
            block.margin_v = compute_margin_v(
                block, face_zones, video_height, font, block.text
            )

    # 5. Monta comando ffmpeg com pipe stdin (P1 #1)
    # -map 1:a? → áudio é opcional (não falha se input não tiver áudio)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        # Stream de vídeo raw do pipe
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{video_width}x{video_height}",
        "-r", str(fps),
        "-i", "pipe:0",
        # Áudio do input original
        "-i", str(input_path),
        # Mapeia: stream 0 = vídeo do pipe, stream 1 = áudio do input (opcional)
        "-map", "0:v",
        "-map", "1:a?",
        # Encoda
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]

    # 6. Processa frames com cv2 + pipe stdin pro ffmpeg
    cap = cv2.VideoCapture(str(input_path))
    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            t = frame_idx / fps

            # Encontra bloco ativo neste timestamp
            active_block = None
            for block in blocks:
                if block.start <= t < block.end:
                    active_block = block
                    break

            if active_block is not None and not _in_suppress(t, suppress_intervals or []):
                frame = _render_caption_frame(
                    frame,
                    active_block.text,
                    active_block.margin_v,
                    font,
                    position=position,
                )

            proc.stdin.write(frame.tobytes())
            frame_idx += 1

    finally:
        cap.release()
        proc.stdin.close()

    proc.wait()
    stderr_output = proc.stderr.read().decode("utf-8", errors="replace")

    if proc.returncode != 0:
        print(f"Erro ffmpeg (rc={proc.returncode}):\n{stderr_output}", file=sys.stderr)
        return 1

    if frame_idx == 0:
        print("Erro: nenhum frame lido do vídeo", file=sys.stderr)
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Caption A Clean — face-aware subtitle burn-in")
    p.add_argument("--input", required=True, help="Vídeo de entrada (.mp4)")
    p.add_argument("--transcript", required=True, help="transcript.json (word timestamps)")
    p.add_argument("--face-zones", required=True, help="face_zones.json")
    p.add_argument("--output", required=True, help="Vídeo de saída (.mp4)")
    p.add_argument("--config", help="style_config.json (opcional)")
    p.add_argument("--words-per-block", type=int, default=2, help="Palavras por bloco de legenda (default 2 = sempre cabe em 1 linha em frame 720, sem wrap feio)")
    p.add_argument("--suppress-intervals", default="", help="Intervalos onde legenda é MUTADA (formato: '0-3.5,50-54'). Usado pra suprimir caption quando título/CTA visível.")
    args = p.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erro: input não existe: {input_path}", file=sys.stderr)
        return 1

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"Erro: transcript não existe: {transcript_path}", file=sys.stderr)
        return 1

    face_zones_path = Path(args.face_zones)
    if not face_zones_path.exists():
        print(f"Erro: face_zones não existe: {face_zones_path}", file=sys.stderr)
        return 1

    # Verifica se transcript tem conteúdo antes de validar caption_style.
    # Se vazio → copia input direto (sem legenda), independente do estilo.
    words = _load_words(transcript_path)
    if not words:
        print("WARN: transcript vazio (0 palavras) — copiando input sem legenda.", file=sys.stderr)
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(input_path, out)
        print(f"OK: legenda adicionada → {args.output}", file=sys.stderr)
        return 0

    # Valida caption_style — Caption B/C ainda não implementadas (v1.2+).
    # Em vez de falhar, faz fallback automático pra A-clean com warning,
    # pra não derrubar o pipeline em estilos como 03-split/04-premium que
    # mapeiam pra C-serif.
    # v2.2.0: também deriva caption_position do layout_default em style_config.json
    position = "bottom"  # default seguro
    if args.config:
        cfg_path = Path(args.config)
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            caption_style = cfg.get("caption_style", "A-clean")
            if caption_style != "A-clean":
                print(
                    f"⚠ caption_style='{caption_style}' ainda não implementado "
                    f"(v1.2+) — usando A-clean como fallback.",
                    file=sys.stderr,
                )
            position = derive_caption_position(cfg)
            layout = cfg.get("layout_default") or cfg.get("split") or "full-frame-puro"
            print(
                f"  [layout→position] layout_default='{layout}' → caption_position='{position}'",
                file=sys.stderr,
            )

    suppress = _parse_suppress_intervals(args.suppress_intervals)
    if suppress:
        print(f"  [suppress] Legenda mutada em {len(suppress)} intervalo(s): {suppress}", file=sys.stderr)

    rc = burn_captions(
        input_path=input_path,
        transcript_path=transcript_path,
        face_zones_path=face_zones_path,
        output_path=Path(args.output),
        words_per_block=args.words_per_block,
        suppress_intervals=suppress,
        position=position,
    )
    if rc == 0:
        print(f"OK: legenda adicionada → {args.output}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
