#!/usr/bin/env python3
"""
animate_mock.py — Renderiza HTML template com CSS animations como MP4 video clip.

Usa Playwright video recording para capturar animações CSS ao vivo.
Output: MP4 clip pronto para composição no master via ffmpeg overlay.

Diferença do render_mock.py:
  - render_mock.py  → screenshot PNG (estático, sem vida)
  - animate_mock.py → video recording MP4 (animações CSS rodando!)

Uso:
  python animate_mock.py --subtipo canva-logo --label "Canva" \
      --output mock.mp4 --duration 3.0 --width 720 --height 512

  python animate_mock.py --subtipo napkin-ai-home --label "Napkin AI" \
      --output mock.mp4 --duration 4.0 --live-url https://www.napkin.ai/ \
      --width 720 --height 512
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Diretório onde vivem os templates de UI mocks.
# Default: video-design-system/ bundlado dentro da própria skill (portável).
# Override: EDITAR_VIDEO_DS_DIR aponta pra outra pasta video-design-system/ (ex: uma fonte viva externa).
_DS_ROOT = Path(os.environ.get(
    "EDITAR_VIDEO_DS_DIR",
    str(Path(__file__).resolve().parents[2] / "video-design-system"),
))
UI_MOCKS_DIR = _DS_ROOT / "ui-mocks"


def animate_mock(
    subtipo: str,
    label: str,
    out_mp4: Path,
    duration_s: float = 3.0,
    viewport: tuple[int, int] = (720, 512),
    live_url: str | None = None,
    fps: int = 30,
) -> None:
    """Grava HTML template com CSS animations como MP4 via Playwright video recording.

    Args:
        subtipo: nome do subtipo (ex: "canva-logo", "fast-company-quote")
        label: label descritivo (logs)
        out_mp4: caminho do MP4 de saída
        duration_s: duração da gravação em segundos
        viewport: (width, height) do viewport
        live_url: URL ao vivo (opcional — sites reais)
        fps: framerate do output MP4

    Raises:
        RuntimeError: se nem live_url nem template existem para esse subtipo
    """
    from playwright.sync_api import sync_playwright

    width, height = viewport
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    # Resolve fonte: live URL > template HTML
    template_dir = UI_MOCKS_DIR / subtipo
    template_html = template_dir / "template.html"

    source_url: str
    if live_url:
        source_url = live_url
        print(f"  [animate_mock] {subtipo} ← live URL: {live_url}", file=sys.stderr)
    elif template_html.exists():
        source_url = template_html.as_uri()
        print(f"  [animate_mock] {subtipo} ← template: {template_html}", file=sys.stderr)
    else:
        raise RuntimeError(
            f"animate_mock: nem live_url nem template existem para subtipo '{subtipo}' "
            f"(esperado em {template_html}). PROIBIDO gerar sem fonte."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        video_dir = Path(tmpdir) / "recordings"
        video_dir.mkdir()

        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--allow-file-access-from-files",
                "--disable-web-security",
                "--no-sandbox",
            ])
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                record_video_dir=str(video_dir),
                record_video_size={"width": width, "height": height},
            )
            page = ctx.new_page()

            try:
                page.goto(source_url, wait_until="networkidle", timeout=20000)
            except Exception:
                try:
                    page.goto(source_url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e2:
                    print(f"  [animate_mock] WARN load falhou: {e2}", file=sys.stderr)

            # Espera as animações CSS rodarem pela duração completa
            wait_ms = int(duration_s * 1000)
            page.wait_for_timeout(wait_ms)

            # Pega path do vídeo ANTES de fechar context
            video_path_str = page.video.path()
            page.close()
            ctx.close()
            browser.close()

        webm_path = Path(video_path_str)
        if not webm_path.exists() or webm_path.stat().st_size < 512:
            raise RuntimeError(
                f"animate_mock: Playwright não gravou vídeo em {webm_path}"
            )

        print(f"  [animate_mock] WebM gravado: {webm_path} "
              f"({webm_path.stat().st_size} B)", file=sys.stderr)

        # Converte WebM → MP4 (H.264 + yuv420p, sem áudio)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(webm_path),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "18",
            "-r", str(fps),
            "-t", str(duration_s),
            "-an",
            str(out_mp4),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"animate_mock: ffmpeg falhou: {result.stderr}"
            )

    if not out_mp4.exists() or out_mp4.stat().st_size < 1024:
        raise RuntimeError(f"animate_mock: MP4 inválido em {out_mp4}")

    print(f"  [animate_mock] OK → {out_mp4} "
          f"({out_mp4.stat().st_size} B, {duration_s}s)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Renderiza HTML com CSS animations como MP4 video clip."
    )
    p.add_argument("--subtipo", required=True, help="Subtipo do mock")
    p.add_argument("--label", default="", help="Label descritivo")
    p.add_argument("--output", required=True, type=Path, help="MP4 de saída")
    p.add_argument("--duration", type=float, default=3.0, help="Duração em segundos")
    p.add_argument("--live-url", default=None, help="URL ao vivo (opcional)")
    p.add_argument("--width", type=int, default=720)
    p.add_argument("--height", type=int, default=512)
    p.add_argument("--fps", type=int, default=30)
    args = p.parse_args(argv)

    try:
        animate_mock(
            subtipo=args.subtipo,
            label=args.label,
            out_mp4=args.output,
            duration_s=args.duration,
            viewport=(args.width, args.height),
            live_url=args.live_url,
            fps=args.fps,
        )
    except RuntimeError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
