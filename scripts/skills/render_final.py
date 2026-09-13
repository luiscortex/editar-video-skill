"""Render final com loudness target + escala pra resolução alvo."""
import argparse
import subprocess
import sys
from pathlib import Path


def render_final(
    input_path: Path,
    output_path: Path,
    target_lufs: int = -14,
    width: int = 1080,
    height: int = 1920,
) -> None:
    """ffmpeg compose: scale + loudnorm + h264."""
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    af = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=summary"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--target-lufs", type=int, default=-14)
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--config", help="style_config.json (FIX P1 #7 — interface contract)")
    args = p.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erro: input não existe: {input_path}", file=sys.stderr)
        return 1

    if args.config:
        import json
        cfg = json.loads(Path(args.config).read_text())
        if "color_grading" in cfg:
            print(f"  (style: color_grading={cfg['color_grading']} — aplicado em v1.1)", file=sys.stderr)

    render_final(input_path, Path(args.output), args.target_lufs, args.width, args.height)
    print(f"OK: render final salvo em {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
