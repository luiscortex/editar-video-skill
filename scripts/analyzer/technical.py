"""Loudness LUFS via ffmpeg ebur128."""
import re
import subprocess
from pathlib import Path


def measure_loudness(video_path: Path) -> dict:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(video_path),
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = proc.stderr
    integrated = re.search(r"I:\s*(-?\d+\.\d+)\s*LUFS", output)
    true_peak = re.search(r"Peak:\s*(-?\d+\.\d+)\s*dBFS", output)
    return {
        "integrated_lufs": float(integrated.group(1)) if integrated else None,
        "true_peak": float(true_peak.group(1)) if true_peak else None,
    }
