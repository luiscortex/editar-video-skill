#!/usr/bin/env python3
"""
render_mock.py — Renderiza UI mock REAL via Playwright (sem placeholder genérico).

Estratégia:
  1. Se `live_url` fornecido → abre o site real via Playwright headless, screenshot na viewport
  2. Senão, busca template HTML em `video-design-system/ui-mocks/<subtipo>/template.html`
  3. Se nem live URL nem template existem → ERRO (regra cravada: NUNCA placeholder genérico)

Uso:
  python render_mock.py --subtipo napkin-ai-home --label "Napkin AI" \
      --output mock.png --live-url https://www.napkin.ai/ \
      --width 720 --height 512

  python render_mock.py --subtipo canva-logo --label "Canva" \
      --output mock.png --width 720 --height 512
"""
import argparse
import os
import sys
from pathlib import Path

# Diretório onde vivem os templates de UI mocks.
# Default: video-design-system/ bundlado dentro da própria skill (portável).
# Override: EDITAR_VIDEO_DS_DIR aponta pra outra pasta video-design-system/ (ex: uma fonte viva externa).
_DS_ROOT = Path(os.environ.get(
    "EDITAR_VIDEO_DS_DIR",
    str(Path(__file__).resolve().parents[2] / "video-design-system"),
))
UI_MOCKS_DIR = _DS_ROOT / "ui-mocks"


def render_mock(
    subtipo: str,
    label: str,
    out_png: Path,
    viewport: tuple[int, int] = (720, 512),
    live_url: str | None = None,
    wait_ms: int = 2000,
) -> None:
    """Renderiza UI mock real via Playwright.

    Args:
        subtipo: nome do subtipo (ex: "napkin-ai-home", "canva-logo")
        label: label descritivo (usado em logs)
        out_png: caminho do PNG de saída
        viewport: (width, height) do screenshot
        live_url: URL para capturar ao vivo (opcional)
        wait_ms: tempo de espera após load

    Raises:
        RuntimeError: se nem live_url nem template existem para esse subtipo
    """
    from playwright.sync_api import sync_playwright

    width, height = viewport
    out_png.parent.mkdir(parents=True, exist_ok=True)

    # Resolve fonte: live URL > template HTML
    template_dir = UI_MOCKS_DIR / subtipo
    template_html = template_dir / "template.html"

    source_url: str
    if live_url:
        source_url = live_url
        print(f"  [render_mock] {subtipo} ← live URL: {live_url}", file=sys.stderr)
    elif template_html.exists():
        source_url = template_html.as_uri()
        print(f"  [render_mock] {subtipo} ← template: {template_html}", file=sys.stderr)
    else:
        raise RuntimeError(
            f"render_mock: nem live_url nem template existem para subtipo '{subtipo}' "
            f"(esperado em {template_html}). Mock placeholder genérico é PROIBIDO."
        )

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
        try:
            page.goto(source_url, wait_until="networkidle", timeout=20000)
        except Exception as e:
            # Sites grandes podem não atingir networkidle — fallback domcontentloaded
            print(f"  [render_mock] networkidle falhou ({e}); fallback domcontentloaded", file=sys.stderr)
            page.goto(source_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(wait_ms)
        page.screenshot(
            path=str(out_png),
            full_page=False,
            clip={"x": 0, "y": 0, "width": width, "height": height},
        )
        browser.close()

    if not out_png.exists() or out_png.stat().st_size < 1024:
        raise RuntimeError(f"render_mock: PNG inválido em {out_png}")

    print(f"  [render_mock] OK → {out_png} ({out_png.stat().st_size} B)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Renderiza UI mock real via Playwright.")
    p.add_argument("--subtipo", required=True, help="Subtipo do mock (ex: napkin-ai-home)")
    p.add_argument("--label", default="", help="Label descritivo")
    p.add_argument("--output", required=True, type=Path, help="PNG de saída")
    p.add_argument("--live-url", default=None, help="URL ao vivo (opcional)")
    p.add_argument("--width", type=int, default=720)
    p.add_argument("--height", type=int, default=512)
    p.add_argument("--wait-ms", type=int, default=2000)
    args = p.parse_args(argv)

    try:
        render_mock(
            subtipo=args.subtipo,
            label=args.label,
            out_png=args.output,
            viewport=(args.width, args.height),
            live_url=args.live_url,
            wait_ms=args.wait_ms,
        )
    except RuntimeError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
