"""Aplica fator de velocidade via setpts (video) + atempo (áudio).
Calcula PPM antes/depois (palavras por minuto).
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def calcular_ppm(transcript_path: Path, duracao_s: float) -> float:
    data = json.loads(transcript_path.read_text())
    words = data.get("words", [])
    if duracao_s <= 0:
        return 0.0
    return round((len(words) / duracao_s) * 60, 1)


def acelerar(input_path: Path, output_path: Path, fator: float) -> None:
    if abs(fator - 1.0) < 0.01:
        shutil.copy(input_path, output_path)
        return
    if not (0.5 <= fator <= 2.0):
        raise ValueError(f"Fator {fator} fora do range [0.5, 2.0]")
    filter_complex = f"[0:v]setpts=PTS/{fator}[v];[0:a]atempo={fator}[a]"
    subprocess.check_call([
        "ffmpeg", "-y", "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ], stderr=subprocess.DEVNULL)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="acelerar_video")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--fator", required=True, type=float)
    p.add_argument("--transcript")
    args = p.parse_args(argv)
    acelerar(Path(args.input), Path(args.output), args.fator)
    if args.transcript:
        # Reporta PPM para stdout
        d_in = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", args.input
        ], text=True).strip())
        d_out = d_in / args.fator
        ppm_in = calcular_ppm(Path(args.transcript), d_in)
        ppm_out = round(ppm_in * args.fator, 1)
        print(json.dumps({
            "fator": args.fator,
            "duracao_in_s": round(d_in, 2),
            "duracao_out_s": round(d_out, 2),
            "ppm_in": ppm_in,
            "ppm_out": ppm_out,
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
