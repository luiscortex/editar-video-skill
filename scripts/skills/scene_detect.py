"""Scene change detection via ffmpeg metadata=print. Sempre emite anchor em t=0.

FIX P0 #2: regex anterior aplicado linha-a-linha NUNCA casava (scene em outra linha).
Solução: parsear stderr inteiro, capturar pts_time + lavfi.scene_score próximos.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# metadata=print emite blocos tipo:
#   frame:42  pts:1234  pts_time:1.4
#   lavfi.scene_score=0.456789
# Capturamos pts_time + scene_score próximos (até 200 chars de distância).
SCENE_BLOCK_RX = re.compile(
    r"pts_time:([\d.]+)[\s\S]{0,200}?lavfi\.scene_score=([\d.]+)"
)


def detect_scenes(video: Path, threshold: float = 0.10) -> list[dict]:
    """Retorna lista de {t, score, kind}."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(video),
        "-vf", f"select='gt(scene,{threshold})',metadata=print",
        "-an", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    out = [{"t": 0.0, "score": 1.0, "kind": "anchor"}]
    seen = set()

    for m in SCENE_BLOCK_RX.finditer(proc.stderr):
        t = round(float(m.group(1)), 3)
        score = round(float(m.group(2)), 3)
        if t > 0.0 and t not in seen:
            seen.add(t)
            out.append({"t": t, "score": score, "kind": "detected"})

    return sorted(out, key=lambda s: s["t"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--threshold", type=float, default=0.10)
    args = p.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erro: input não existe: {input_path}", file=sys.stderr)
        return 1

    scenes = detect_scenes(input_path, threshold=args.threshold)
    Path(args.output).write_text(json.dumps(scenes, indent=2))
    print(f"OK: {len(scenes)} scenes detectadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
