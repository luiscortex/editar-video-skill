#!/usr/bin/env python3
"""
animar_titulo.py — Gera PNG transparente com título overlay (TT1-TT4).

Monta HTML que importa o Video Design System CSS via file:// e captura
via Playwright com omit_background=True para fundo transparente.

Uso:
    python animar_titulo.py \
        --text "isso não é tutorial" \
        --type TT4 \
        --output titulo.png \
        [--accent "é manifesto"] \
        [--width 1080] [--height 1920] \
        [--wait-ms 1500] \
        [--case lower|upper|normal] \
        [--canvas-preset preview|reel|story]
"""
import argparse
import os
import sys
from html import escape
from pathlib import Path

from PIL import Image

# ─── Canvas presets (font-size override por destino) ───────────────────────
# preview: mantém DS original (backward-compat)
# reel:    1080x1920 Reels/Shorts/TikTok — fontes 3-4x maiores
# story:   1080x1920 IG Story — fontes intermediárias
CANVAS_PRESETS = {
    "preview": {  # default, mantém DS original
        "TT1": 36, "TT2": 28, "TT3": 32, "TT4": 44,
    },
    "reel": {  # 1080x1920 — TIK/REELS/SHORTS — 9:16 vertical
        "TT1": 144,  # palavra curta de impacto
        "TT2": 96,   # "PASSO 01" em caixa
        "TT3": 80,   # URL/comando técnico
        "TT4": 128,  # frase impacto serif
    },
    "story": {  # 1080x1920 — IG Story — fontes intermediárias (mais respiro)
        "TT1": 120, "TT2": 80, "TT3": 64, "TT4": 104,
    },
}

# ─── Caminhos do Video Design System ───────────────────────────────────────
# Default: video-design-system/ bundlado dentro da própria skill (portável).
# Override: EDITAR_VIDEO_DS_DIR aponta pra outra pasta video-design-system/ (ex: uma fonte viva externa).
_DS_ROOT = Path(os.environ.get(
    "EDITAR_VIDEO_DS_DIR",
    str(Path(__file__).resolve().parents[2] / "video-design-system"),
))
DS_STYLES = _DS_ROOT / "styles"
CSS_TOKENS = DS_STYLES / "tokens.css"
CSS_TITLES = DS_STYLES / "titles.css"
CSS_ANIM   = DS_STYLES / "animations.css"

# ─── Mapeamento tipo → CSS classes e case default ──────────────────────────
TYPE_MAP = {
    "TT1": {
        "classes": "title-tt1 anim-torn-paper",
        "case_default": "lower",
    },
    "TT2": {
        "classes": "title-tt2 anim-slide-up",
        "case_default": "upper",
    },
    "TT3": {
        "classes": "title-tt3 anim-blur-fade anim-pulse-glow",
        "case_default": "normal",
    },
    "TT4": {
        "classes": "title-tt4 anim-blur-fade",
        "case_default": "normal",
    },
}

VALID_TYPES = sorted(TYPE_MAP.keys())
VALID_CASES = ("lower", "upper", "normal")


# ─── Font override CSS por canvas preset ──────────────────────────────────

def _font_override_css(preset: str) -> str:
    """Retorna bloco <style> inline que sobrescreve font-size do DS para o preset dado."""
    sizes = CANVAS_PRESETS[preset]
    return f"""<style>
  .title-tt1 {{ font-size: {sizes['TT1']}px !important; }}
  .title-tt2 {{ font-size: {sizes['TT2']}px !important; }}
  .title-tt3 {{ font-size: {sizes['TT3']}px !important; }}
  .title-tt4 {{ font-size: {sizes['TT4']}px !important; }}
</style>"""


# ─── Validação do Design System ────────────────────────────────────────────

def validate_ds_files():
    missing = [str(p) for p in (CSS_TOKENS, CSS_TITLES, CSS_ANIM) if not p.exists()]
    if missing:
        print(
            "Erro: Video Design System não encontrado em "
            f"{DS_STYLES}/\n"
            f"Arquivos ausentes: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)


# ─── Montagem do HTML ──────────────────────────────────────────────────────

def build_html(
    text: str,
    css_classes: str,
    case: str,
    accent: str | None,
    width: int,
    height: int,
    canvas_preset: str = "preview",
) -> str:
    # Adiciona modificador de case quando necessário
    case_class = ""
    if case == "upper":
        case_class = " case-upper"
    elif case == "lower":
        case_class = " case-lower"
    elif case == "normal":
        case_class = " case-normal"

    full_classes = css_classes + case_class

    # Monta o conteúdo interno do span
    text_escaped = escape(text)
    if accent:
        accent_escaped = escape(accent)
        text_html = f'{text_escaped}<br><span class="accent">{accent_escaped}</span>'
    else:
        text_html = text_escaped

    tokens_uri = CSS_TOKENS.as_uri()
    titles_uri = CSS_TITLES.as_uri()
    anim_uri   = CSS_ANIM.as_uri()

    font_override = _font_override_css(canvas_preset)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="{tokens_uri}">
<link rel="stylesheet" href="{titles_uri}">
<link rel="stylesheet" href="{anim_uri}">
{font_override}
<style>
  html, body {{
    margin: 0; padding: 0;
    width: {width}px; height: {height}px;
    background: transparent;
  }}
  .stage {{
    position: relative;
    width: 100%; height: 100%;
    display: flex; align-items: center; justify-content: center;
  }}
</style>
</head>
<body>
<div class="stage">
  <div class="title-overlay">
    <span class="{full_classes}">{text_html}</span>
  </div>
</div>
</body>
</html>"""


# ─── Renderização via Playwright ───────────────────────────────────────────

def render_titulo(html: str, output_path: Path, width: int, height: int, wait_ms: int):
    import tempfile
    from playwright.sync_api import sync_playwright

    # Salva HTML em arquivo temp e carrega via file:// para que os <link>
    # stylesheet com file:// URIs sejam permitidos pelo Chromium.
    # set_content() bloqueia recursos locais ("Not allowed to load local resource").
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as tmp:
        tmp.write(html)
        tmp_path = Path(tmp.name)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--allow-file-access-from-files",
                "--disable-web-security",
                "--no-sandbox",
            ]
        )
        ctx = browser.new_context(viewport={"width": width, "height": height})
        page = ctx.new_page()
        page.goto(tmp_path.as_uri(), wait_until="networkidle")
        # Espera animação CSS completar o estado pós-entrada.
        # Decisão: wait_for_timeout fixo (1500ms default) cobre os tempos de
        # entrada de todas as anims (torn-paper 600ms, slide-up 540ms,
        # blur-fade 700ms). TT3 tem pulse-glow infinita — capturas em estado
        # já "aceso" pós 1500ms. Alternativa (wait_for_function verificando
        # opacity) seria frágil pois TT3 nunca "para". Timeout é suficiente.
        page.wait_for_timeout(wait_ms)
        page.screenshot(
            path=str(output_path),
            omit_background=True,
            full_page=False,
        )
        browser.close()
    tmp_path.unlink(missing_ok=True)


# ─── Validação do PNG gerado ───────────────────────────────────────────────

def validate_output(output_path: Path):
    if not output_path.exists():
        print(f"Erro: arquivo não foi criado em {output_path}", file=sys.stderr)
        sys.exit(1)

    size = output_path.stat().st_size
    if size < 1024:
        print(
            f"Erro: PNG gerado suspeito ({size}B < 1KB) — provável falha de renderização.",
            file=sys.stderr,
        )
        sys.exit(1)

    img = Image.open(output_path)
    if img.mode not in {"RGBA", "LA"}:
        print(
            f"Erro: PNG não tem canal alpha ({img.mode}) — transparência não preservada.",
            file=sys.stderr,
        )
        sys.exit(1)


# ─── CLI ───────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Gera PNG transparente de título overlay (TT1-TT4) via Playwright."
    )
    parser.add_argument("--text", required=True, help="Texto principal do título")
    parser.add_argument(
        "--type", required=True, dest="tipo",
        choices=VALID_TYPES,
        metavar="TIPO",
        help=f"Estilo do título: {', '.join(VALID_TYPES)}",
    )
    parser.add_argument("--output", required=True, type=Path, help="Caminho do PNG de saída")
    parser.add_argument("--accent", default=None, help="Palavra/frase em destaque (TT4)")
    parser.add_argument("--width", type=int, default=1080, help="Largura do canvas (px)")
    parser.add_argument("--height", type=int, default=1920, help="Altura do canvas (px)")
    parser.add_argument(
        "--wait-ms", type=int, default=1500,
        help="Milliseconds para aguardar animação antes de capturar (default: 1500)"
    )
    parser.add_argument(
        "--case", choices=VALID_CASES, default=None,
        help="Capitalização: lower|upper|normal (default: depende do tipo)"
    )
    parser.add_argument(
        "--canvas-preset", choices=list(CANVAS_PRESETS.keys()), default="preview",
        dest="canvas_preset",
        help="Preset de tamanho de fonte. preview=DS original, reel=1080x1920 grande, story=1080x1920 médio"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Valida text não vazio
    if not args.text.strip():
        print("Erro: --text não pode ser vazio.", file=sys.stderr)
        sys.exit(1)

    # Valida DS files antes de fazer qualquer coisa pesada
    validate_ds_files()

    tipo_cfg = TYPE_MAP[args.tipo]
    case = args.case if args.case is not None else tipo_cfg["case_default"]

    html = build_html(
        text=args.text,
        css_classes=tipo_cfg["classes"],
        case=case,
        accent=args.accent,
        width=args.width,
        height=args.height,
        canvas_preset=args.canvas_preset,
    )

    output_path = args.output
    render_titulo(html, output_path, args.width, args.height, args.wait_ms)
    validate_output(output_path)

    size_kb = output_path.stat().st_size / 1024
    print(f"OK: {output_path} ({size_kb:.1f}KB) — {args.tipo} '{args.text}'")


if __name__ == "__main__":
    main()
