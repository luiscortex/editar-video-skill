"""ffprobe wrapper — metadata visual."""
import json
import subprocess
from pathlib import Path


def _parse_fps(rate_str: str) -> float | None:
    """Parse seguro de 'N/D' (ex: '30/1' ou '30000/1001'). Retorna None se inválido.

    FIX P0 #3: substitui eval() (vulnerável a injection + NameError em 'N/A').
    """
    if not rate_str or rate_str in ("0/0", "N/A"):
        return None
    try:
        if "/" in rate_str:
            n, d = rate_str.split("/", 1)
            return float(n) / float(d) if float(d) != 0 else None
        return float(rate_str)
    except (ValueError, ZeroDivisionError):
        return None


def probe_metadata(video_path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(video_path),
    ]
    out = subprocess.check_output(cmd, text=True)
    data = json.loads(out)
    v_stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a_stream = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    return {
        "duration_s": float(data["format"].get("duration", 0)),
        "width": v_stream["width"] if v_stream else None,
        "height": v_stream["height"] if v_stream else None,
        "fps": _parse_fps(v_stream.get("avg_frame_rate", "")) if v_stream else None,
        "codec_video": v_stream["codec_name"] if v_stream else None,
        "codec_audio": a_stream["codec_name"] if a_stream else None,
        "sample_rate": int(a_stream["sample_rate"]) if a_stream else None,
    }
