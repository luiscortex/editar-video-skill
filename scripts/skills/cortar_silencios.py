"""Corta silêncios > min_silence_ms via silencedetect + select/aselect (A/V sincronizado).

FIX P0 #1: o método anterior (silenceremove + -c:v copy) causava A/V drift cumulativo
porque silenceremove só age no áudio. Solução: detectar silêncios via silencedetect,
inverter pra intervalos a manter, aplicar mesmo recorte em vídeo+áudio via filter_complex.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

SILENCE_START_RX = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END_RX = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(input_path: Path, threshold_db: int, min_silence_s: float) -> list[tuple[float, float]]:
    """Retorna lista de (start_s, end_s) de silêncios detectados."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(input_path),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_s}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    silences = []
    pending_start = None
    for line in proc.stderr.splitlines():
        m_start = SILENCE_START_RX.search(line)
        if m_start:
            pending_start = float(m_start.group(1))
            continue
        m_end = SILENCE_END_RX.search(line)
        if m_end and pending_start is not None:
            silences.append((max(0.0, pending_start), float(m_end.group(1))))
            pending_start = None
    return silences


def get_duration(input_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(input_path),
    ]
    return float(subprocess.check_output(cmd, text=True).strip())


def build_keep_intervals(silences: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Inverte silêncios → intervalos a MANTER."""
    if not silences:
        return [(0.0, duration)]
    keep = []
    cursor = 0.0
    for s_start, s_end in silences:
        if s_start > cursor:
            keep.append((cursor, s_start))
        cursor = s_end
    if cursor < duration:
        keep.append((cursor, duration))
    return keep


def cortar_silencios(input_path: Path, output_path: Path, threshold_db: int = -30, min_silence_ms: int = 300) -> None:
    """Detecta silêncios e corta vídeo+áudio sincronizadamente via filter complex."""
    min_silence_s = min_silence_ms / 1000.0
    silences = detect_silences(input_path, threshold_db, min_silence_s)
    duration = get_duration(input_path)
    keep = build_keep_intervals(silences, duration)

    # Edge cases (degenerados) — copia input direto:
    #   1. Sem silêncios detectados: nada pra cortar
    #   2. Keep vazio: vídeo 100% silente
    #   3. Keep total < 0.5s: vídeo essencialmente todo silêncio (select de poucos ms
    #      produz container vazio sem frames legíveis — bug descoberto no smoke E2E
    #      com fixture mute que retornava 8ms restantes só)
    total_keep_s = sum(end - start for start, end in keep)
    if not silences or not keep or total_keep_s < 0.5:
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(input_path),
            "-c", "copy", str(output_path),
        ], check=True)
        if silences and total_keep_s < 0.5:
            print(
                f"⚠ aviso: vídeo essencialmente todo silêncio "
                f"({total_keep_s*1000:.0f}ms restantes após cortes) — preservado integral",
                file=sys.stderr,
            )
        return

    # Constrói select/aselect com intervalos a MANTER
    keep_expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keep)
    filter_complex = (
        f"[0:v]select='{keep_expr}',setpts=N/FRAME_RATE/TB[v];"
        f"[0:a]aselect='{keep_expr}',asetpts=N/SR/TB[a]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--threshold-db", type=int, default=-30)
    p.add_argument("--min-silence-ms", type=int, default=300)
    p.add_argument("--config", help="style_config.json (ignorado no MVP, contract reservado v1.1)")
    args = p.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erro: input não existe: {input_path}", file=sys.stderr)
        return 1

    cortar_silencios(input_path, Path(args.output), args.threshold_db, args.min_silence_ms)
    print(f"OK: cortado salvo em {args.output} (A/V sincronizado)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
