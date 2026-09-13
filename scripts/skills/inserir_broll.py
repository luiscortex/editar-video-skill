"""Insere B-roll/imagem na timeline com Ken Burns opcional via ffmpeg overlay.

FIX P0 #4: detecta se broll é imagem ou vídeo. -loop 1 SÓ pra imagem (vídeo travaria).
FIX P0 #5: Ken Burns simplificado e correto via scale + crop dinâmico (não zoompan).

Fase 2: suporte a --shot-list JSON com múltiplos overlays em UM único ffmpeg call.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


# ─── Single-shot (modo legado) ─────────────────────────────────────────────────

def inserir_broll(
    input_video: Path,
    broll_path: Path,
    at_s: float,
    duration_s: float,
    output_path: Path,
    ken_burns: bool = False,
    width: int = 1080,
    height: int = 1920,
) -> None:
    """Overlay do broll a partir de `at_s` por `duration_s` segundos.

    Pra IMAGEM: usa -loop 1 + duração explícita.
    Pra VÍDEO: trim do broll pra duration_s (sem -loop).
    Ken Burns: scale levemente maior que viewport + crop com offset crescente.
    """
    is_img = _is_image(broll_path)

    # Pré-input: imagem precisa de loop+duration; vídeo NÃO
    if is_img:
        broll_input = ["-loop", "1", "-t", str(duration_s), "-i", str(broll_path)]
    else:
        broll_input = ["-t", str(duration_s), "-i", str(broll_path)]

    # Filter pro broll
    if ken_burns:
        # Ken Burns: scale 10% maior que viewport, crop com offset linear no tempo.
        scaled_w = int(width * 1.10)
        scaled_h = int(height * 1.10)
        max_off_x = scaled_w - width
        max_off_y = scaled_h - height
        broll_filter = (
            f"[1:v]scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:"
            f"x='({max_off_x}/2)+({max_off_x}/2)*(t/{duration_s})':"
            f"y='({max_off_y}/2)+({max_off_y}/2)*(t/{duration_s})',"
            f"setsar=1,format=yuv420p[broll]"
        )
    else:
        broll_filter = (
            f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,format=yuv420p[broll]"
        )

    end_s = at_s + duration_s
    overlay_filter = (
        f"[broll]setpts=PTS-STARTPTS+{at_s}/TB[broll_pts];"
        f"[0:v][broll_pts]overlay=enable='between(t,{at_s},{end_s})'[v]"
    )
    filter_complex = f"{broll_filter};{overlay_filter}"

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_video),
        *broll_input,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


# ─── Multi-overlay (modo Fase 2) ──────────────────────────────────────────────

def _broll_filter_for(insert: dict, idx: int, width: int, height: int) -> str:
    """Gera a cadeia de filtros para um único insert (scale + opcional Ken Burns).

    idx é o índice do input (começa em 1, já que [0] é o vídeo principal).
    Retorna string de filtro sem o tag de saída — quem chama adiciona [ovN].
    """
    at_s = insert["at_s"]
    duration_s = insert["duration_s"]
    ken_burns = insert.get("ken_burns", False)
    stream = f"[{idx}:v]"

    if ken_burns:
        scaled_w = int(width * 1.10)
        scaled_h = int(height * 1.10)
        max_off_x = scaled_w - width
        max_off_y = scaled_h - height
        scale_crop = (
            f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:"
            f"x='({max_off_x}/2)+({max_off_x}/2)*(t/{duration_s})':"
            f"y='({max_off_y}/2)+({max_off_y}/2)*(t/{duration_s})',"
        )
    else:
        scale_crop = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
        )

    return (
        f"{stream}{scale_crop}"
        f"setsar=1,format=yuv420p,"
        f"setpts=PTS-STARTPTS+{at_s}/TB"
    )


def _mascote_filter_for(insert: dict, idx: int, width: int, height: int) -> str:
    """Gera filtro para mascote Clawd: scale 25% + manter alpha (yuva420p) + posição canto.

    ProRes 4444 com alpha yuva444p12le → force yuva420p para compatibilidade com overlay.
    Retorna string de filtro sem o tag de saída — quem chama adiciona [mascoteN].
    """
    at_s = insert["at_s"]
    scale = insert.get("mascote_scale", 0.25)
    asset_w = int(width * scale)
    asset_h = asset_w  # Clawd é 600×600 — quadrado

    stream = f"[{idx}:v]"
    return (
        f"{stream}scale={asset_w}:{asset_h},"
        f"format=yuva420p,"
        f"setpts=PTS-STARTPTS+{at_s}/TB"
    )


MASCOTE_MARGIN_PX = 30
MASCOTE_DEFAULT_SCALE = 0.25


def inserir_broll_multi(
    input_video: Path,
    inserts: list[dict],
    output_path: Path,
    width: int = 1080,
    height: int = 1920,
) -> None:
    """Aplica lista de inserts em UM único ffmpeg call.

    Edge cases:
    - lista vazia: copia input pra output (sem re-encode), avisa em stderr
    - asset_path inexistente: pula com warning, processa o restante
    - se todos falharem: copia input

    Z-order: mascotes sempre como ÚLTIMOS overlays (z-index superior),
    independente do at_s — evita ser oculto por B-roll/titulo fullscreen
    que ative no mesmo intervalo de tempo.
    """
    # Filtra inserts com assets válidos (pula tipo=titulo com asset_path=None)
    valid_inserts = []
    for ins in inserts:
        asset = ins.get("asset_path")
        if asset is None:
            # Títulos têm asset_path=None (preenchido pelo orchestrator antes desta etapa)
            print(
                f"  [info] asset_path=None, pulando insert tipo={ins.get('tipo', '?')} @ {ins.get('at_s', '?')}s",
                file=sys.stderr,
            )
            continue
        p = Path(asset)
        if p.exists():
            valid_inserts.append(ins)
        else:
            print(
                f"  [warning] asset não encontrado, pulando: {asset}",
                file=sys.stderr,
            )

    # Z-order: mascotes por último pra ficar em cima de B-roll/titulo fullscreen
    valid_inserts.sort(key=lambda x: (x.get("tipo") == "mascote", x.get("at_s", 0.0)))

    if not valid_inserts:
        if inserts:
            print("  [warning] nenhum insert válido — copiando input sem overlay", file=sys.stderr)
        else:
            print("  [info] inserts: [] — vídeo passa direto (sem overlay)", file=sys.stderr)
        shutil.copy2(str(input_video), str(output_path))
        return

    # Monta cmd: [main] + [broll1/mascote1] + [broll2/mascote2] + ...
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_video)]

    for ins in valid_inserts:
        p = Path(ins["asset_path"])
        duration_s = ins["duration_s"]
        if _is_image(p):
            cmd += ["-loop", "1", "-t", str(duration_s), "-i", str(p)]
        else:
            cmd += ["-t", str(duration_s), "-i", str(p)]

    # Constrói filter_complex
    # Fase 1: normaliza cada insert → tag intermediário
    filter_parts = []
    for i, ins in enumerate(valid_inserts, start=1):
        is_mascote = ins.get("tipo") == "mascote"
        if is_mascote:
            filt = _mascote_filter_for(ins, i, width, height)
            filter_parts.append(f"{filt}[mascote{i}]")
        else:
            broll_filt = _broll_filter_for(ins, i, width, height)
            filter_parts.append(f"{broll_filt}[ov{i}]")

    # Fase 2: encadeia overlays
    # B-roll: [prev][ovN]overlay=enable='between(t,...)'[vN]
    # Mascote: [prev][mascoteN]overlay=x=W-w-30:y=H-h-30:enable='between(t,...)'[vN]
    prev_stream = "0:v"
    for i, ins in enumerate(valid_inserts, start=1):
        at_s = ins["at_s"]
        end_s = at_s + ins["duration_s"]
        out_stream = f"v{i}"
        is_mascote = ins.get("tipo") == "mascote"
        if is_mascote:
            filter_parts.append(
                f"[{prev_stream}][mascote{i}]overlay="
                f"x=W-w-{MASCOTE_MARGIN_PX}:y=H-h-{MASCOTE_MARGIN_PX}:"
                f"enable='between(t,{at_s},{end_s})'"
                f"[{out_stream}]"
            )
        else:
            filter_parts.append(
                f"[{prev_stream}][ov{i}]overlay=enable='between(t,{at_s},{end_s})'[{out_stream}]"
            )
        prev_stream = out_stream

    filter_complex = ";".join(filter_parts)
    final_video_stream = f"[{prev_stream}]"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", final_video_stream,
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ]

    subprocess.run(cmd, check=True)
    n_mascote = sum(1 for ins in valid_inserts if ins.get("tipo") == "mascote")
    n_broll = len(valid_inserts) - n_mascote
    print(
        f"OK: {len(valid_inserts)} overlay(s) inserido(s) "
        f"({n_broll} B-roll, {n_mascote} mascote) — 1 re-encode",
        file=sys.stdout,
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="Vídeo principal")
    p.add_argument("--output", required=True)
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--config", help="style_config.json (opcional)")

    # Modo single-shot (legado)
    p.add_argument("--broll", help="B-roll (imagem ou vídeo) — modo single-shot")
    p.add_argument("--at", type=float, help="Segundo onde inserir — modo single-shot")
    p.add_argument("--duration", type=float, help="Duração do overlay (s) — modo single-shot")
    p.add_argument("--ken-burns", action="store_true", help="Aplica zoom Ken Burns 1.0→1.10")

    # Modo multi-overlay (Fase 2)
    p.add_argument("--shot-list", dest="shot_list", help="shot_list.json com lista de inserts")

    args = p.parse_args(argv)

    if args.shot_list:
        # Modo multi-overlay
        shot_list_path = Path(args.shot_list)
        if not shot_list_path.exists():
            print(f"Erro: --shot-list não existe: {shot_list_path}", file=sys.stderr)
            return 1
        data = json.loads(shot_list_path.read_text())
        inserts = data.get("inserts", [])
        inserir_broll_multi(
            Path(args.input), inserts, Path(args.output),
            width=args.width, height=args.height,
        )
    else:
        # Modo single-shot (legado)
        if not args.broll or args.at is None or args.duration is None:
            print("Erro: modo single-shot requer --broll, --at e --duration", file=sys.stderr)
            return 1
        inserir_broll(
            Path(args.input), Path(args.broll), args.at, args.duration,
            Path(args.output), ken_burns=args.ken_burns, width=args.width, height=args.height,
        )
        print(f"OK: B-roll inserido aos {args.at}s por {args.duration}s ({'image' if _is_image(Path(args.broll)) else 'video'})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
