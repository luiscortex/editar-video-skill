"""Orquestrador `/editar-video` — entry CLI.

Workflow v2.1 — ordem dos 5 gates (cravada 2026-05-12):
  Gate 1 — transcript_revisao   (revisão Whisper antes de qualquer texto)
  Gate 2 — plano_inserts        (Claude contextual a partir do transcript corrigido)
  Gate 3 — master               (composição: cortado + inserts ui-mock → master sem legenda)
  Gate 4 — legenda              (título overlay + caption burned-in face-aware)
  Gate 5 — acelerar             (fator PPM final)

Modos:
  - discovery          : lê pasta de input, retorna inventário JSON
  - analyze            : roda análise (transcribe + scene + face) standalone
  - edit               : entry point principal — roda até gerar transcript-revisao.md e pára no Gate 1
  - propor-inserts     : roda propor_inserts standalone (mecânico + manuais)
  - compor-master      : aplica plano-inserts no cortado.mp4 → master.mp4
  - aplicar-legenda    : aplica título overlay + caption burned-in → master-legendado.mp4
  - aprovar-transcript : valida revisao.md, parseia → corrigido.json (Gate 1)
  - aprovar-plano      : valida plano-inserts (Gate 2) → dispara compor-master
  - aprovar-master     : valida master.mp4 (Gate 3) → dispara aplicar-legenda
  - aprovar-legenda    : valida legendado (Gate 4)
  - acelerar           : aplica fator final → final.mp4 (Gate 5)
  - desfaz / versao    : utilitários
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Padrões de path que sugerem vídeo JÁ EDITADO (DaVinci/Premiere/CapCut export).
# Quando bruto cai num desses, skill pula Etapa 1 (cortar_silencios) automaticamente.
# Match é por SEGMENTO de path (case insensitive) — não substring, pra evitar
# falso-positivo em arquivos tipo `cena-final.mp4`.
PRE_EDITED_PATH_PATTERNS = (
    "davinci",
    "premiere",
    "finalizados",
    "edited",
    "final",
)


def _detect_pre_edited(path: Path) -> str | None:
    """Retorna o nome do padrão que matchou no path (case insensitive), ou None."""
    parts_lower = [p.lower() for p in path.parts]
    for pattern in PRE_EDITED_PATH_PATTERNS:
        if pattern in parts_lower:
            return pattern
    return None


ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
SKILLS = SCRIPTS / "skills"
STUBS = SCRIPTS / "stubs"
# Default: mesmo interpretador (venv) que está rodando o orchestrator.
# Override: EDITAR_VIDEO_PYTHON aponta pra outro python3 (ex: venv externo).
PYTHON = os.environ.get("EDITAR_VIDEO_PYTHON", sys.executable)

# Garante imports dos helpers locais (workflow_state, edit_log, input_discoverer)
sys.path.insert(0, str(SCRIPTS))


def _probe_duration_s(video_path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)
    ], text=True).strip()
    return float(out)


def _run_skill(script: Path, args: list[str]) -> dict:
    """Roda script CLI streamando stderr em tempo real (FIX P0 #6)."""
    cmd = [PYTHON, str(script), *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    stderr_lines = []
    for line in proc.stderr:
        sys.stderr.write(line)
        sys.stderr.flush()
        stderr_lines.append(line)
    stdout, _ = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Skill falhou: {script.name}\n{''.join(stderr_lines)}")
    return {"stdout": stdout, "stderr": "".join(stderr_lines)}


# Helpers de state machine (workflow v2)
from workflow_state import load_state, save_state, init_state, advance_stage, rollback_stage  # noqa: E402
from edit_log import log_decision, log_quality_check  # noqa: E402
import quality_gates  # noqa: E402


# ─── Placeholder PNG generator (usado APENAS pra título overlay no Gate 4) ──
# REGRA CRAVADA: NUNCA usar placeholder pra ui-mock no Gate 3 master.
# Mocks reais agora rodam via skills/render_mock.py (Playwright). Esta função
# segue existindo só pro fallback de título quando animar_titulo.py falha.

def _gen_placeholder_png(out_path: Path, label: str, width: int = 720, height: int = 512,
                        bg: tuple[int, int, int] = (31, 53, 86),
                        fg: tuple[int, int, int] = (218, 119, 86)) -> None:
    """Gera PNG sólido com label centralizada. APENAS fallback de título (Gate 4)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    font = None
    for path in [
        "/Library/Fonts/Plus Jakarta Sans Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, 48)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((width - tw) / 2, (height - th) / 2), label, fill=fg, font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


# ─── Tabela de live URLs por subtipo ──────────────────────────────────────────
# Fonte: regra cravada — mocks ui devem refletir o produto real sempre que possível.
# Pra subtipos com template HTML local, deixa fora dessa tabela e o render_mock
# usa o template (~/Documents/luiscortex/video-design-system/ui-mocks/<subtipo>/template.html).

LIVE_URL_MAP: dict[str, str] = {
    "napkin-ai-home": "https://www.napkin.ai/",
    "raphael-app-home": "https://raphael.app/",
}


# ─── Stillframe: extrai 1 frame + áudio ───────────────────────────────────────

def _build_stillframe_video(input_video: Path, output: Path,
                            extract_at_s: float = 2.0) -> None:
    """Cria vídeo stillframe = 1 frame congelado + áudio do input.

    Útil pro talking_head_mode='stillframe': elimina movimento da pessoa gravada
    e libera os 40% top pros mocks reais (preserva voz integral)."""
    # 1) Probe duration + face zone pra escolher frame com rosto se possível
    duration = _probe_duration_s(input_video)
    extract_at = min(extract_at_s, max(0.5, duration - 0.5))

    # 2) Extrai PNG
    frame_png = output.with_suffix(".frame.png")
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(extract_at), "-i", str(input_video),
        "-frames:v", "1", "-q:v", "2",
        str(frame_png),
    ], check=True)

    # 3) Monta vídeo = imagem loop + áudio original
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", str(frame_png),
        "-i", str(input_video),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output),
    ], check=True)
    frame_png.unlink(missing_ok=True)


# ─── Compose helpers ──────────────────────────────────────────────────────────

def _render_animated_mock_for_insert(ins: dict, mocks_dir: Path,
                                     insert_height: int = 512,
                                     insert_width: int = 720) -> Path:
    """Renderiza mock ANIMADO como MP4 via Playwright video recording.

    CSS animations do template HTML rodam ao vivo — Playwright grava tudo
    como vídeo, converte pra MP4. Resultado: clip animado pronto pro compose.

    insert_height/_width = dimensões alvo (Playwright viewport):
      - split-*  → 720×512 (top do vstack 60/40)
      - fullframe → 720×1280 (live URL) ou 720×512 (template, será letterboxed)

    Raises:
        RuntimeError: se nem live_url nem template existem para esse subtipo
    """
    subtipo = ins.get("subtipo") or "unknown"
    label = (
        ins.get("conteudo", {}).get("label")
        or ins.get("label")
        or subtipo
    )
    duration_s = float(ins.get("duration_s", 3.0))
    safe_subtipo = "".join(c if c.isalnum() or c in "-_" else "_" for c in subtipo)
    asset_path = mocks_dir / f"mock-{ins.get('id', 0)}-{safe_subtipo}.mp4"
    live_url = LIVE_URL_MAP.get(subtipo)

    if live_url:
        w, h = insert_width, insert_height
    else:
        w, h = 720, 512

    cmd_args = [
        "--subtipo", subtipo,
        "--label", str(label),
        "--output", str(asset_path),
        "--duration", str(duration_s),
        "--width", str(w),
        "--height", str(h),
    ]
    if live_url:
        cmd_args.extend(["--live-url", live_url])

    _run_skill(SKILLS / "animate_mock.py", cmd_args)
    return asset_path


# ─── Layout vocabulary (v2.2.0) ───────────────────────────────────────────────
# Cada insert do plano pode especificar próprio layout. Quando não especifica,
# herda layout_default de style_config.json. Aliases v2.1 → v2.2 mantidos pra
# retro-compat (edições antigas continuam funcionando sem migration).
LAYOUT_ALIASES: dict[str, str] = {
    # v2.1 → v2.2 alias map
    "fullframe": "full-frame-puro",
    "60/40": "split-60-40",
    "50/50": "split-50-50",
    "70/30": "split-70-30",
    "80/20": "split-80-20",
}


def _normalize_layout(layout: str | None) -> str:
    """Normaliza string de layout v2.1 → v2.2."""
    if not layout:
        return "full-frame-puro"
    return LAYOUT_ALIASES.get(layout, layout)


# Card overlay positioning (v2.2.0) — fração do frame height
# Specs cravadas em regras-edicao-video-2026.md → "9 layouts de insert"
CARD_OVERLAY_POSITIONS: dict[str, dict] = {
    # name: {y_pct: posição vertical em fração do height, max_w_pct: max-width em fração do width}
    "overlay-top-card":    {"y_pct": 0.12, "max_w_pct": 0.85},
    "overlay-middle-card": {"y_pct": 0.40, "max_w_pct": 0.85},
    "overlay-bottom-card": {"y_pct": 0.68, "max_w_pct": 0.85},  # bottom: 22% → top: 100-22-10 ≈ 68%
}

# Card visual specs (em frame 720×1280; escalado proporcionalmente em outros frames)
CARD_BORDER_RADIUS = 20   # px em frame 720 (= 30px em frame 1080)
CARD_SHADOW_OPACITY = 0.18


def _compose_master_with_splits(
    base_video: Path,
    inserts_with_assets: list[dict],
    output: Path,
    width: int = 720,
    height: int = 1280,
    head_pct: int = 60,
) -> None:
    """Compõe master.mp4 aplicando inserts ui-mock com 9 layouts (v2.2.0).

    Layouts suportados:
      - `overlay-fullframe`        → SKIP visual (talking head 100% durante o insert)
      - `overlay-top-card`         → card rounded white BG no topo (y_pct=0.12)
      - `overlay-middle-card`      → card rounded white BG no meio (y_pct=0.40)
      - `overlay-bottom-card`      → card rounded white BG no bottom (y_pct=0.68)
      - `full-frame-puro`          → mock ocupa frame inteiro (letterboxed)
      - `split-60-40` / `50-50` / `70-30` / `80-20` → vstack top mock + bottom talking head

    Aliases v2.1 (retro-compat):
      - `fullframe` → `full-frame-puro`
      - `60/40`, `50/50`, `70/30`, `80/20` → `split-*`

    Cada insert é "ativado" pelo enable='between(t,at_s,end_s)' do ffmpeg.
    Fora dos intervalos, mostra base_video full-frame (talking head live).

    Cards overlay usam bordas arredondadas via geq filter (alpha mask piecewise),
    sem precisar de mock com alpha channel. Specs cravadas em regras-edicao-video-2026.md.
    """
    if not inserts_with_assets:
        # Sem inserts ui-mock — copia base como master
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(base_video),
            "-c", "copy", str(output),
        ], check=True)
        return

    insert_h = int(round(height * (100 - head_pct) / 100.0))  # ex: 512
    head_h = height - insert_h                                # ex: 768

    # Filtra `overlay-fullframe` ANTES de montar comando ffmpeg — esses inserts
    # não têm visual, só marcam momento (talking head fica 100% naturalmente).
    visual_inserts = [
        ins for ins in inserts_with_assets
        if _normalize_layout(ins.get("layout")) != "overlay-fullframe"
    ]

    if not visual_inserts:
        # Todos os inserts são overlay-fullframe (sem visual) → talking head puro
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(base_video),
            "-c", "copy", str(output),
        ], check=True)
        return

    # Build ffmpeg command: base_video + 1 input por mock asset (só inserts visuais)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(base_video)]
    for ins in visual_inserts:
        asset = str(ins["asset_path"])
        if asset.endswith(".mp4"):
            # MP4 animado — já tem duração correta, sem loop
            cmd += ["-t", str(ins["duration_s"]), "-i", asset]
        else:
            # PNG estático — loop infinito pra criar stream
            cmd += ["-loop", "1", "-t", str(ins["duration_s"]), "-i", asset]

    filter_parts: list[str] = []
    prev = "0:v"

    # Scale factor pra specs cravadas em frame 720
    radius = int(round(CARD_BORDER_RADIUS * width / 720.0))

    for i, ins in enumerate(visual_inserts, start=1):
        at_s = float(ins["at_s"])
        dur = float(ins["duration_s"])
        end_s = at_s + dur
        layout = _normalize_layout(ins.get("layout"))

        if layout.startswith("split-"):
            # vstack split (legado v2.1) — top mock + bottom talking head
            # Split stream do prev em duas vias pra usar AO MESMO TEMPO
            # como base do crop E como base do overlay.
            filter_parts.append(
                f"[{prev}]split=2[base{i}_a][base{i}_b]"
            )
            filter_parts.append(
                f"[{i}:v]scale={width}:{insert_h}:force_original_aspect_ratio=increase,"
                f"crop={width}:{insert_h},setsar=1,format=yuv420p,"
                f"setpts=PTS-STARTPTS+{at_s}/TB[mock{i}]"
            )
            filter_parts.append(
                f"[base{i}_a]crop={width}:{head_h}:0:{(height - head_h)}[base{i}_crop]"
            )
            filter_parts.append(
                f"[mock{i}][base{i}_crop]vstack=inputs=2[split{i}]"
            )
            filter_parts.append(
                f"[base{i}_b][split{i}]overlay=enable='between(t,{at_s},{end_s})'[v{i}]"
            )
            prev = f"v{i}"

        elif layout in CARD_OVERLAY_POSITIONS:
            # Card overlay: scale 85% width × proportional height,
            # rounded corners via geq alpha mask, posição variável.
            pos = CARD_OVERLAY_POSITIONS[layout]
            card_w = int(round(width * pos["max_w_pct"]))
            # Card height: deixa scale preservar aspect ratio (input do animate_mock
            # é tipicamente 720×512 → 4:3-ish). Ao escalar pra card_w, height vira
            # proporcional automaticamente via -1.
            card_x = int(round((width - card_w) / 2))  # centralizado horizontal
            card_y_center = int(round(height * pos["y_pct"]))

            # Geq alpha mask pra bordas arredondadas (SDF de rounded rectangle):
            # alpha = 255 dentro do retângulo arredondado, 0 fora.
            # NOTAS críticas do ffmpeg geq:
            #   - usa W/H maiúsculos (NÃO iw/ih), tipico de outros filters
            #   - NÃO suporta max(a,b) — usa truque (x*gt(x,0)) que zera valores negativos
            #   - vírgulas dentro de funções precisam escape (\,) quando filtro vai por -vf
            #     mas como vai em filter_complex via lista, não precisa escape aqui
            alpha_expr = (
                f"255*lte("
                f"sqrt("
                f"pow((abs(X-W/2)-W/2+{radius})*gt(abs(X-W/2)-W/2+{radius},0),2)"
                f"+pow((abs(Y-H/2)-H/2+{radius})*gt(abs(Y-H/2)-H/2+{radius},0),2)"
                f"),{radius})"
            )

            filter_parts.append(
                f"[{i}:v]scale={card_w}:-2:flags=lanczos,"
                f"format=rgba,"
                f"geq=r=r(X\\,Y):g=g(X\\,Y):b=b(X\\,Y):a='{alpha_expr}',"
                f"setsar=1,"
                f"setpts=PTS-STARTPTS+{at_s}/TB[card{i}]"
            )
            # card_y na fórmula overlay = card_y_center - card_height/2
            # mas card_height vem do scale (depende do mock). Usa H do main como ref.
            # Truque: ao invés de calcular height aqui, deixa o overlay usar
            # y={card_y_center}-h/2 onde h é altura do overlay stream.
            filter_parts.append(
                f"[{prev}][card{i}]overlay="
                f"x={card_x}:y={card_y_center}-h/2:"
                f"enable='between(t,{at_s},{end_s})'[v{i}]"
            )
            prev = f"v{i}"

        else:
            # full-frame-puro (default + alias de "fullframe"):
            # fit-to-frame com letterbox (preserva aspect ratio do mock)
            # decrease + pad pra evitar esmagar templates 720×512 num frame 720×1280
            filter_parts.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=white,"
                f"setsar=1,format=yuv420p,"
                f"setpts=PTS-STARTPTS+{at_s}/TB[full{i}]"
            )
            filter_parts.append(
                f"[{prev}][full{i}]overlay=enable='between(t,{at_s},{end_s})'[v{i}]"
            )
            prev = f"v{i}"

    filter_complex = ";".join(filter_parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{prev}]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(output),
    ]
    subprocess.run(cmd, check=True)


# ─── Gates conversacionais ────────────────────────────────────────────────────

def mode_aprovar_transcript(work_dir: Path) -> int:
    """Gate 1: user revisou transcript-revisao.md. Parseia → corrigido.json.

    Após avançar pra plano_inserts, dispara o ciclo Claude-no-loop:
    1) Roda propor_inserts (mecânico — hook + brand + CTA)
    2) Imprime mensagem stderr instruindo Claude a ler transcript-corrigido.json,
       escrever inserts_manuais.json e re-rodar propor-inserts pra mergear.
    """
    state = load_state(work_dir)
    if state is None or state["etapa_atual"] != "transcript_revisao":
        print("Erro: workflow não está em 'transcript_revisao'", file=sys.stderr)
        return 1
    revisao = work_dir / "transcript-revisao.md"
    original = work_dir / "transcript.json"
    corrigido = work_dir / "transcript-corrigido.json"
    if not revisao.exists():
        print(f"Erro: {revisao} não existe — gere a revisão primeiro", file=sys.stderr)
        return 1
    _run_skill(SKILLS / "gerar_revisao_transcript.py", [
        "--mode", "parse",
        "--revisao", str(revisao),
        "--original", str(original),
        "--output", str(corrigido),
    ])
    log_decision(work_dir, slug=state["slug"], gate=1, etapa="transcript_revisao", decisao="aprovado")
    advance_stage(work_dir, "transcript_revisao")

    # Após Gate 1, gera plano-inserts mecânico + dispara Claude-no-loop
    mode_propor_inserts(work_dir)

    plano_path = work_dir / "plano-inserts.json"
    plano_html = work_dir / "plano-inserts.html"
    manuais_path = work_dir / "inserts_manuais.json"

    mensagem = f"""
🧠 GATE 1 OK — agora CLAUDE-NO-LOOP (antes do Gate 2 Plano)

Transcript corrigido em: {corrigido}
Plano MECÂNICO gerado (hook + brand exato + CTA). Sua vez, Claude:

  1) Leia o transcript corrigido: {corrigido}
  2) Analise contextualmente cada bloco (o que ela está explicando? que UI seria útil?)
  3) Escreva inserts contextuais em: {manuais_path}
     Schema de cada item:
       {{
         "at_s": float, "duration_s": float,
         "tipo": "ui-mock"|"titulo"|"cta",
         "subtipo": str,
         "layout": "split-5050-head-bottom"|"fullframe"|"fullframe-overlay",
         "label": str (curto, vai pro card),
         "termo_no_audio": str (frase que disparou),
         "raciocinio": str (POR QUE esse insert agrega — obrigatório)
       }}
  4) Re-rode: orchestrator.py --mode propor-inserts --work-dir {work_dir}
     → vai mergear mecânico + manuais, regenerar HTML do Gate 2, abrir pro user aprovar.

Plano mecânico atual está em: {plano_path}
HTML preview: {plano_html}
"""
    print(mensagem, file=sys.stderr)
    print(json.dumps({
        "status": "ok",
        "next": "plano_inserts",
        "transcript_corrigido": str(corrigido),
        "plano_path": str(plano_path),
        "plano_html": str(plano_html),
        "manuais_path": str(manuais_path),
    }))
    return 0


def mode_aprovar_plano(work_dir: Path) -> int:
    """Gate 2: user aprovou plano-inserts. Avança pra master + dispara compor-master."""
    state = load_state(work_dir)
    if state is None:
        print(f"Erro: workflow_state.json não existe em {work_dir}", file=sys.stderr)
        return 1
    if state["etapa_atual"] != "plano_inserts":
        print(f"Erro: etapa atual é {state['etapa_atual']}, esperava 'plano_inserts'", file=sys.stderr)
        return 1
    log_decision(work_dir, slug=state["slug"], gate=2, etapa="plano_inserts", decisao="aprovado")
    advance_stage(work_dir, "plano_inserts")

    # Dispara composição do master automaticamente
    rc = mode_compor_master(work_dir)
    if rc != 0:
        return rc

    print(json.dumps({"status": "ok", "next": "master", "master": str(work_dir / "master.mp4")}))
    return 0


def mode_aprovar_master(work_dir: Path) -> int:
    """Gate 3: user aprovou master.mp4. Avança pra legenda + dispara aplicar-legenda."""
    state = load_state(work_dir)
    if state is None or state["etapa_atual"] != "master":
        print("Erro: workflow não está em 'master'", file=sys.stderr)
        return 1
    log_decision(work_dir, slug=state["slug"], gate=3, etapa="master", decisao="aprovado")
    advance_stage(work_dir, "master")

    rc = mode_aplicar_legenda(work_dir)
    if rc != 0:
        return rc

    print(json.dumps({"status": "ok", "next": "legenda",
                      "master_legendado": str(work_dir / "master-legendado.mp4")}))
    return 0


def mode_aprovar_legenda(work_dir: Path) -> int:
    """Gate 4: user aprovou master-legendado. Avança pra acelerar."""
    state = load_state(work_dir)
    if state is None or state["etapa_atual"] != "legenda":
        print("Erro: workflow não está em 'legenda'", file=sys.stderr)
        return 1
    log_decision(work_dir, slug=state["slug"], gate=4, etapa="legenda", decisao="aprovado")
    advance_stage(work_dir, "legenda")
    print(json.dumps({"status": "ok", "next": "acelerar"}))
    return 0


def mode_acelerar(work_dir: Path, fator: float) -> int:
    """Gate 5: aplica fator final em master-legendado → final.mp4."""
    state = load_state(work_dir)
    if state is None or state["etapa_atual"] != "acelerar":
        print("Erro: workflow não está em 'acelerar'", file=sys.stderr)
        return 1
    master_leg = work_dir / "master-legendado.mp4"
    final = work_dir / "final.mp4"
    transcript = work_dir / "transcript-corrigido.json"
    if not master_leg.exists():
        print(f"Erro: {master_leg} não existe", file=sys.stderr)
        return 1
    cmd_args = [
        "--input", str(master_leg),
        "--output", str(final),
        "--fator", str(fator),
    ]
    if transcript.exists():
        cmd_args.extend(["--transcript", str(transcript)])
    _run_skill(SKILLS / "acelerar_video.py", cmd_args)
    log_decision(work_dir, slug=state["slug"], gate=5, etapa="acelerar",
                 decisao=f"{fator}x", detalhe=f"fator={fator}")
    advance_stage(work_dir, "acelerar")
    print(json.dumps({"status": "ok", "final": str(final)}))
    return 0


def mode_desfaz(work_dir: Path) -> int:
    state = load_state(work_dir)
    if state is None:
        print(f"Erro: workflow_state.json não existe em {work_dir}", file=sys.stderr)
        return 1
    try:
        new_state = rollback_stage(work_dir)
    except RuntimeError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    log_decision(work_dir, slug=new_state["slug"], gate=0, etapa="desfaz",
                 decisao="rollback", detalhe=f"voltou para {new_state['etapa_atual']}")
    print(json.dumps({"status": "ok", "etapa_atual": new_state["etapa_atual"]}))
    return 0


def mode_versao(work_dir: Path) -> int:
    """Detecta versão do workflow: v1 (sem workflow_state.json) ou v2."""
    state = load_state(work_dir)
    if state is None:
        print(json.dumps({"version": "1.0.0", "etapa_atual": None}))
    else:
        print(json.dumps({
            "version": state["version"],
            "etapa_atual": state["etapa_atual"],
            "etapas_completas": state["etapas_completas"],
        }))
    return 0


def mode_propor_inserts(work_dir: Path) -> int:
    """Roda propor_inserts — usa transcript-corrigido.json se existe (pós-Gate 1),
    senão transcript.json bruto. style_config.json deve existir."""
    transcript_corrigido = work_dir / "transcript-corrigido.json"
    transcript_bruto = work_dir / "transcript.json"
    transcript = transcript_corrigido if transcript_corrigido.exists() else transcript_bruto
    style_config = work_dir / "style_config.json"
    cortado = work_dir / "cortado.mp4"
    if not transcript.exists() or not style_config.exists():
        print(f"Erro: faltam transcript.json ou style_config.json em {work_dir}", file=sys.stderr)
        return 1
    duracao = _probe_duration_s(cortado) if cortado.exists() else 60.0
    plano = work_dir / "plano-inserts.json"
    plano_html = work_dir / "plano-inserts.html"
    state = load_state(work_dir)
    slug = state["slug"] if state else work_dir.name
    cmd_args = [
        "--transcript", str(transcript),
        "--style-config", str(style_config),
        "--video-duration", str(duracao),
        "--slug", slug,
        "--output", str(plano),
        "--output-html", str(plano_html),
    ]
    if cortado.exists():
        cmd_args.extend(["--video", str(cortado)])
    manuais = work_dir / "inserts_manuais.json"
    if manuais.exists():
        cmd_args.extend(["--manuais", str(manuais)])
    _run_skill(SKILLS / "propor_inserts.py", cmd_args)
    print(json.dumps({"plano": str(plano), "html": str(plano_html)}))
    return 0


# ─── Gate 3 — composição do master (cabeia inserir_broll) ─────────────────────

def mode_compor_master(work_dir: Path) -> int:
    """Compõe master.mp4 = cortado.mp4 + inserts ui-mock animados.

    Versão 2.1 — mocks ANIMADOS MP4 + split 60/40 + live talking head:

    1. Resolve dimensões do cortado (deve ser 720×1280 já).
    2. Lê style_config → split (default 60/40) + talking_head_mode (default "live").
    3. Se talking_head_mode='stillframe', monta stillframe.mp4 = 1 frame congelado + áudio.
       Se 'live' (default), usa cortado.mp4 direto — gaps entre inserts mostram pessoa falando.
    4. Pra cada insert ui-mock:
         - layout='fullframe' → MP4 animado overlay fullframe
         - layout começa com 'split-' → MP4 animado 720×512,
           vstack 60/40 (mock top + talking head bottom)
       Mocks são renderizados via skills/animate_mock.py (Playwright video recording
       de CSS animations → WebM → MP4). PLACEHOLDER GENÉRICO PROIBIDO.
    5. ffmpeg compose: MP4 inputs sem `-loop 1`, PNG inputs com `-loop 1`.
    6. Roda quality_gates stage=master.
    """
    plano_path = work_dir / "plano-inserts.json"
    cortado = work_dir / "cortado.mp4"
    style_config_path = work_dir / "style_config.json"
    master = work_dir / "master.mp4"

    if not plano_path.exists():
        print(f"Erro: {plano_path} não existe", file=sys.stderr)
        return 1
    if not cortado.exists():
        print(f"Erro: {cortado} não existe", file=sys.stderr)
        return 1

    plano = json.loads(plano_path.read_text())
    state = load_state(work_dir)
    slug = state["slug"] if state else work_dir.name

    # Style config — v2.2.0 lê layout_default; fallback pro legado v2.1 (split)
    layout_default = "full-frame-puro"   # v2.2.0 default
    talking_head_mode = "live"
    split = "60/40"                       # legado v2.1 (usado se layout_default for split-*)
    if style_config_path.exists():
        cfg = json.loads(style_config_path.read_text())
        talking_head_mode = cfg.get("talking_head_mode", "live")
        # v2.2.0: layout_default tem prioridade. Aceita string nova OU legada.
        layout_default = _normalize_layout(
            cfg.get("layout_default") or cfg.get("split") or "full-frame-puro"
        )
        # split-string mantida pra compose split_pct
        split = cfg.get("split", "60/40")
    try:
        head_pct = int(split.split("/")[0]) if "/" in split else int(split.split("-")[1])
    except (ValueError, IndexError):
        head_pct = 60
    insert_pct = 100 - head_pct

    # Probe dimensões do cortado
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", str(cortado)
        ], text=True).strip()
        w_str, h_str = out.split("x")
        width, height = int(w_str), int(h_str)
    except Exception:
        width, height = 720, 1280

    insert_height = int(round(height * insert_pct / 100.0))  # 512 com 60/40

    # Talking head base: live ou stillframe
    base_video = cortado
    if talking_head_mode == "stillframe":
        print("→ talking_head_mode=stillframe: extraindo frame + áudio...", file=sys.stderr)
        stillframe_mp4 = work_dir / "stillframe.mp4"
        _build_stillframe_video(cortado, stillframe_mp4, extract_at_s=2.0)
        base_video = stillframe_mp4
    elif talking_head_mode != "live":
        print(f"  [warn] talking_head_mode desconhecido: {talking_head_mode}; usando 'live'",
              file=sys.stderr)

    # Filtra inserts ui-mock (titulo/cta vão pro Gate 4)
    mocks_dir = work_dir / "mocks"
    mocks_dir.mkdir(exist_ok=True)
    inserts_para_compor: list[dict] = []
    inserts_falhados: list[str] = []
    for ins in plano.get("inserts", []):
        tipo = ins.get("tipo")
        if tipo in ("titulo", "cta"):
            continue
        if tipo != "ui-mock":
            continue
        # Aplica layout_default se insert não especificou (v2.2.0)
        raw_layout = ins.get("layout") or layout_default
        layout = _normalize_layout(raw_layout)
        if layout == "fullframe-overlay":
            # fullframe-overlay = título-like, vai pro Gate 4
            continue
        if layout == "overlay-fullframe":
            # v2.2.0: overlay-fullframe = sem visual (gap natural com talking head 100%).
            # Marca o intervalo mas não renderiza mock. Compose ignora.
            inserts_para_compor.append({**ins, "layout": layout, "asset_path": None})
            continue

        asset = ins.get("asset_path")
        if asset and Path(asset).exists():
            inserts_para_compor.append({**ins, "layout": layout, "asset_path": asset})
            continue

        # Determina dimensão alvo do mock pra render via animate_mock
        if layout.startswith("split-"):
            target_w, target_h = width, insert_height
        elif layout in CARD_OVERLAY_POSITIONS:
            # Cards rounded: renderiza no tamanho NATIVO do template (720×512),
            # compose faz scale pra max_w_pct depois.
            target_w, target_h = 720, 512
        else:
            # full-frame-puro / fullframe (alias): renderiza no frame inteiro
            target_w, target_h = width, height

        try:
            asset_path = _render_animated_mock_for_insert(ins, mocks_dir,
                                                          insert_height=target_h,
                                                          insert_width=target_w)
            inserts_para_compor.append({**ins, "layout": layout, "asset_path": str(asset_path)})
        except RuntimeError as e:
            print(f"  [erro] mock '{ins.get('subtipo')}' não renderizou: {e}", file=sys.stderr)
            inserts_falhados.append(str(ins.get("subtipo")))

    if inserts_falhados:
        print(f"⚠ {len(inserts_falhados)} mock(s) falharam (sem template nem live URL): "
              f"{inserts_falhados}", file=sys.stderr)
        print("  → adicione template em video-design-system/ui-mocks/<subtipo>/template.html",
              file=sys.stderr)

    # Dump shot-list pra auditoria
    shot_list = work_dir / "shot-list-master.json"
    shot_list.write_text(json.dumps({
        "inserts": inserts_para_compor,
        "talking_head_mode": talking_head_mode,
        "split": split,
    }, indent=2, ensure_ascii=False))

    # Compose direto via ffmpeg (bypassa inserir_broll pra suportar vstack split 60/40)
    _compose_master_with_splits(
        base_video=base_video,
        inserts_with_assets=inserts_para_compor,
        output=master,
        width=width, height=height,
        head_pct=head_pct,
    )

    # Quality gates stage=master
    print("→ rodando quality gates stage=master...", file=sys.stderr)
    qrep_path = work_dir / "quality_report_master.json"
    report = quality_gates.run_for_stage(stage="master", video_path=master)
    qrep_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log_quality_check(work_dir, slug=slug, stage="master",
                      resultado=report["overall"], report_path=str(qrep_path))
    if report["overall"] == "FAIL":
        print(f"⚠ Quality gates FAIL em master. Veja {qrep_path}", file=sys.stderr)
        for c in report["checks"]:
            if c["status"] == "FAIL":
                print(f"  - {c['name']}: {c['detail']}", file=sys.stderr)

    print(f"OK: master.mp4 composto com {len(inserts_para_compor)} mock(s)", file=sys.stderr)
    return 0


# ─── Gate 4 — aplicar legenda + título overlay ────────────────────────────────

def mode_aplicar_legenda(work_dir: Path) -> int:
    """Aplica título overlay (TT1 hook + TT2 CTA) + caption A burned-in face-aware.

    Output: master-legendado.mp4

    Estratégia título: tenta renderizar via animar_titulo.py (Playwright);
    se Playwright não estiver disponível ou falhar, gera PNG placeholder PIL.
    Estratégia legenda: chama adicionar_legenda.py com transcript-corrigido.json.
    Os intervalos onde título está visível são acumulados pra futura supressão
    (passados pro adicionar_legenda quando suportado; por ora só logados).
    """
    master = work_dir / "master.mp4"
    plano_path = work_dir / "plano-inserts.json"
    transcript_corrigido = work_dir / "transcript-corrigido.json"
    transcript_bruto = work_dir / "transcript.json"
    face_zones = work_dir / "face_zones.json"
    style_config = work_dir / "style_config.json"
    out_legendado = work_dir / "master-legendado.mp4"

    if not master.exists():
        print(f"Erro: {master} não existe", file=sys.stderr)
        return 1
    if not plano_path.exists():
        print(f"Erro: {plano_path} não existe", file=sys.stderr)
        return 1
    transcript = transcript_corrigido if transcript_corrigido.exists() else transcript_bruto
    if not transcript.exists():
        print(f"Erro: nenhum transcript em {work_dir}", file=sys.stderr)
        return 1

    state = load_state(work_dir)
    slug = state["slug"] if state else work_dir.name

    plano = json.loads(plano_path.read_text())

    # Probe dimensões do master
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", str(master)
        ], text=True).strip()
        w_str, h_str = out.split("x")
        width, height = int(w_str), int(h_str)
    except Exception:
        width, height = 1080, 1920

    # 1) Renderiza títulos (TT1 hook + TT2 CTA) + aplica via overlay
    titulos_dir = work_dir / "titulos"
    titulos_dir.mkdir(exist_ok=True)
    titulo_overlays: list[dict] = []
    suppress_intervals: list[tuple[float, float]] = []
    for ins in plano.get("inserts", []):
        if ins.get("tipo") not in ("titulo", "cta"):
            continue
        at_s = float(ins["at_s"])
        dur = float(ins["duration_s"])
        subtipo = ins.get("subtipo") or "TT1"
        texto = (
            ins.get("conteudo", {}).get("texto")
            or ins.get("conteudo", {}).get("label")
            or ins.get("label")
            or subtipo
        )
        png_path = titulos_dir / f"titulo-{ins.get('id', len(titulo_overlays))}-{subtipo}.png"
        ok_playwright = False
        try:
            _run_skill(SKILLS / "animar_titulo.py", [
                "--text", str(texto),
                "--type", subtipo,
                "--output", str(png_path),
                "--width", str(width),
                "--height", str(height),
            ])
            ok_playwright = png_path.exists()
        except Exception as e:
            print(f"  [warn] animar_titulo falhou ({e}); usando placeholder PIL", file=sys.stderr)
            ok_playwright = False
        if not ok_playwright:
            # Fallback: placeholder transparente com texto grande
            _gen_placeholder_png(png_path, label=str(texto)[:40],
                                width=width, height=height)
        titulo_overlays.append({"at_s": at_s, "duration_s": dur, "asset_path": str(png_path)})
        suppress_intervals.append((at_s, at_s + dur))

    # 2) Compõe títulos no master via ffmpeg (multi-overlay)
    if titulo_overlays:
        master_com_titulo = work_dir / "master-com-titulo.mp4"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(master)]
        for ov in titulo_overlays:
            cmd += ["-loop", "1", "-t", str(ov["duration_s"]), "-i", str(ov["asset_path"])]
        # filter_complex: cada overlay aplicado em série
        parts = []
        prev = "0:v"
        for i, ov in enumerate(titulo_overlays, start=1):
            at = ov["at_s"]
            end = at + ov["duration_s"]
            parts.append(
                f"[{i}:v]format=rgba,setpts=PTS-STARTPTS+{at}/TB[t{i}];"
                f"[{prev}][t{i}]overlay=enable='between(t,{at},{end})'[v{i}]"
            )
            prev = f"v{i}"
        filter_complex = ";".join(parts)
        cmd += [
            "-filter_complex", filter_complex,
            "-map", f"[{prev}]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            str(master_com_titulo),
        ]
        subprocess.run(cmd, check=True)
        master_pra_legenda = master_com_titulo
    else:
        master_pra_legenda = master

    # 3) Chama adicionar_legenda.py com suppress_intervals (v2.1: implementado)
    suppress_str = ",".join(f"{s}-{e}" for s, e in suppress_intervals) if suppress_intervals else ""
    cmd_args = [
        "--input", str(master_pra_legenda),
        "--transcript", str(transcript),
        "--face-zones", str(face_zones),
        "--output", str(out_legendado),
        "--words-per-block", "2",
    ]
    if suppress_str:
        cmd_args.extend(["--suppress-intervals", suppress_str])
    if style_config.exists():
        cmd_args.extend(["--config", str(style_config)])
    if not face_zones.exists():
        # adicionar_legenda exige face-zones; gera lista vazia pra não quebrar
        # (formato real: list[{"t": float, "face": {...} | None}])
        face_zones.write_text(json.dumps([], ensure_ascii=False))
    _run_skill(SKILLS / "adicionar_legenda.py", cmd_args)

    # 4) Quality gates stage=legendado + auto_fix
    print("→ rodando quality gates stage=legendado...", file=sys.stderr)
    qrep_path = work_dir / "quality_report_legendado.json"
    report = quality_gates.run_for_stage(stage="legendado", video_path=out_legendado)
    qrep_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log_quality_check(work_dir, slug=slug, stage="legendado",
                      resultado=report["overall"], report_path=str(qrep_path))
    if report["overall"] == "FAIL":
        print(f"⚠ Quality gates FAIL em legendado. Veja {qrep_path}", file=sys.stderr)
        for c in report["checks"]:
            if c["status"] == "FAIL":
                print(f"  - {c['name']}: {c['detail']}", file=sys.stderr)

    print(f"OK: master-legendado.mp4 ({len(titulo_overlays)} título(s) + caption)", file=sys.stderr)
    if suppress_intervals:
        print(f"  [info] suppress_intervals aplicados: {suppress_intervals}",
              file=sys.stderr)
    return 0


# ─── Pipeline E2E ─────────────────────────────────────────────────────────────

def mode_discovery(input_dir: Path, output_path: Path) -> int:
    from input_discoverer import discover
    result = discover(input_dir)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"OK: discovery salvo em {output_path}", file=sys.stderr)
    return 0


def mode_analyze(input_video: Path, work_dir: Path) -> int:
    work_dir.mkdir(parents=True, exist_ok=True)

    print("→ transcrevendo (model=base — FIX P0 #6: tiny tem WER ruim em PT)...", file=sys.stderr)
    _run_skill(SKILLS / "transcrever.py", [
        "--input", str(input_video),
        "--output", str(work_dir / "transcript.json"),
        "--model", "base",
    ])

    print("→ scene detect...", file=sys.stderr)
    _run_skill(SKILLS / "scene_detect.py", [
        "--input", str(input_video),
        "--output", str(work_dir / "scenes.json"),
    ])

    print("→ face detect...", file=sys.stderr)
    _run_skill(SKILLS / "face_detect.py", [
        "--input", str(input_video),
        "--output", str(work_dir / "face_zones.json"),
        "--sample-rate", "2.0",
    ])

    print(f"OK: análise completa em {work_dir}", file=sys.stderr)
    return 0


def mode_edit(
    input_dir: Path,
    work_dir: Path,
    estilo: str = "04-premium-cinematic",
    split: str = "60/40",
    tipo_conteudo: str = "Tutorial",
    skip_cuts: bool = False,
) -> int:
    """Workflow v2.1: inicializa state, roda Etapa 0 (config) + Etapa 1 (cuts_base) +
    gera transcript-revisao.md. PARA no Gate 1 (transcript_revisao).

    Cada gate subsequente é invocado manualmente:
      aprovar-transcript → dispara propor-inserts + Claude-no-loop
      aprovar-plano      → dispara compor-master
      aprovar-master     → dispara aplicar-legenda
      aprovar-legenda    → libera Gate 5
      acelerar           → final.mp4
    """
    from input_discoverer import discover
    from library import slug_for, write_meta, hash_file

    inventory = discover(input_dir)
    if not inventory["brutos"]:
        print("Erro: nenhum vídeo bruto encontrado", file=sys.stderr)
        return 1

    bruto = Path(inventory["brutos"][0]["path"])
    slug = slug_for(str(bruto), estilo, "reels")
    slug_dir = work_dir / slug
    slug_dir.mkdir(parents=True, exist_ok=True)

    write_meta(slug_dir, {
        "slug": slug,
        "source_path": str(bruto),
        "source_hash": hash_file(bruto),
        "estilo": estilo,
        "platform": "reels",
    })

    style_config_data = {
        "split": split,
        "estilo": estilo,
        "tipo_conteudo": tipo_conteudo,
    }
    init_state(slug_dir, slug=slug, style_config=style_config_data)

    # Etapa 0 (inicial): aplicar estilo + style_config
    style_config = slug_dir / "style_config.json"
    _run_skill(SKILLS / "aplicar_estilo.py", [
        "--estilo", estilo,
        "--output", str(style_config),
    ])
    existing = json.loads(style_config.read_text())
    existing.update(style_config_data)
    style_config.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    advance_stage(slug_dir, "inicial")

    # Etapa 1 (cuts_base): analyze + cortar silêncios.
    # Detecção automática: se path do bruto sugere vídeo já editado (DaVinci, Premiere,
    # finalizados, edited, final), pula cortar_silencios pra não cortar de novo.
    # Flag explícita --skip-cuts (skip_cuts=True) força o pulo independente do path.
    mode_analyze(bruto, slug_dir)
    cortado = slug_dir / "cortado.mp4"
    matched_pattern = _detect_pre_edited(bruto)
    should_skip = skip_cuts or matched_pattern is not None
    if should_skip:
        reason = "flag --skip-cuts" if skip_cuts else f"path contém '{matched_pattern}'"
        print(
            f"⚠ Input parece pré-editado ({reason}) — pulando Etapa 1 (cortar_silencios). "
            f"Copiando bruto direto como cortado.mp4.",
            file=sys.stderr,
        )
        # Re-encode minimal via stream copy — normaliza container sem re-encodar áudio/vídeo.
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(bruto), "-c", "copy", str(cortado)],
            check=True,
        )
    else:
        _run_skill(SKILLS / "cortar_silencios.py", [
            "--input", str(bruto),
            "--output", str(cortado),
            "--config", str(style_config),
        ])
    advance_stage(slug_dir, "cuts_base")

    # Quality gates stage=cortado
    print("→ rodando quality gates stage=cortado...", file=sys.stderr)
    qrep_path = slug_dir / "quality_report.json"
    report = quality_gates.run_for_stage(stage="cortado", video_path=cortado)
    qrep_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log_quality_check(slug_dir, slug=slug, stage="cortado",
                      resultado=report["overall"], report_path=str(qrep_path))
    if report["overall"] == "FAIL":
        print(f"⚠ Quality gates FAIL em cuts_base. Veja {qrep_path}", file=sys.stderr)
        for c in report["checks"]:
            if c["status"] == "FAIL":
                print(f"  - {c['name']}: {c['detail']}", file=sys.stderr)

    # Gate 1 (transcript_revisao): gera transcript-revisao.md pro user editar
    transcript_path = slug_dir / "transcript.json"
    revisao_path = slug_dir / "transcript-revisao.md"
    _run_skill(SKILLS / "gerar_revisao_transcript.py", [
        "--mode", "generate",
        "--transcript", str(transcript_path),
        "--output", str(revisao_path),
    ])

    mensagem = f"""
📝 GATE 1 — REVISÃO DO TRANSCRIPT

Whisper transcreveu o áudio mas SEMPRE erra brand names, gírias e palavras técnicas
(ex: 'Claude' → 'Clod', 'prompts' → 'prontos').

  1) Abra o arquivo de revisão: {revisao_path}
  2) Edite as palavras erradas direto no .md
  3) Confirme no chat com 'aprovado'
  4) Rode: orchestrator.py --mode aprovar-transcript --work-dir {slug_dir}
     → vai parsear, gerar transcript-corrigido.json, gerar plano-inserts mecânico
     e disparar o ciclo Claude-no-loop pra propor inserts contextuais antes do Gate 2.
"""
    print(mensagem, file=sys.stderr)
    print(json.dumps({
        "slug": slug,
        "work_dir": str(slug_dir),
        "etapa_atual": "transcript_revisao",
        "next_step": "user revisar transcript-revisao.md + rodar aprovar-transcript",
        "transcript_path": str(transcript_path),
        "revisao_path": str(revisao_path),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="/editar-video")
    p.add_argument("--mode", required=True, choices=[
        "discovery", "analyze", "edit",
        "propor-inserts",
        "compor-master", "aplicar-legenda",
        "aprovar-transcript", "aprovar-plano", "aprovar-master", "aprovar-legenda",
        "acelerar", "desfaz", "versao",
    ])
    p.add_argument("--input-dir", help="Pasta input (discovery, edit)")
    p.add_argument("--input", help="Arquivo input (analyze)")
    p.add_argument("--output", help="Output path (discovery)")
    p.add_argument("--work-dir", default=str(Path.home() / "Documents" / "luiscortex" / "edicoes"))
    p.add_argument("--estilo", default="04-premium-cinematic")
    p.add_argument("--style", default="60/40", help="Split: 60/40, 50/50, 70/30, 80/20")
    p.add_argument("--tipo-conteudo", default="Tutorial")
    p.add_argument("--fator", type=float, default=1.0, help="Fator de aceleração (acelerar)")
    p.add_argument("--skip-cuts", action="store_true",
                   help="Pula Etapa 1 (cortar_silencios). Use quando input já é vídeo editado (DaVinci/Premiere export).")
    args = p.parse_args(argv)

    if args.mode == "discovery":
        return mode_discovery(Path(args.input_dir), Path(args.output))
    if args.mode == "analyze":
        return mode_analyze(Path(args.input), Path(args.work_dir))
    if args.mode == "edit":
        return mode_edit(Path(args.input_dir), Path(args.work_dir), args.estilo,
                         split=args.style, tipo_conteudo=args.tipo_conteudo,
                         skip_cuts=args.skip_cuts)
    if args.mode == "propor-inserts":
        return mode_propor_inserts(Path(args.work_dir))
    if args.mode == "compor-master":
        return mode_compor_master(Path(args.work_dir))
    if args.mode == "aplicar-legenda":
        return mode_aplicar_legenda(Path(args.work_dir))
    if args.mode == "aprovar-transcript":
        return mode_aprovar_transcript(Path(args.work_dir))
    if args.mode == "aprovar-plano":
        return mode_aprovar_plano(Path(args.work_dir))
    if args.mode == "aprovar-master":
        return mode_aprovar_master(Path(args.work_dir))
    if args.mode == "aprovar-legenda":
        return mode_aprovar_legenda(Path(args.work_dir))
    if args.mode == "acelerar":
        return mode_acelerar(Path(args.work_dir), args.fator)
    if args.mode == "desfaz":
        return mode_desfaz(Path(args.work_dir))
    if args.mode == "versao":
        return mode_versao(Path(args.work_dir))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
