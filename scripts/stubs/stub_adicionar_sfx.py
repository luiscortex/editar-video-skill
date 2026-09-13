"""STUB MVP — adicionar_sfx. Substituir por skill real em v1.1+."""
import argparse
import json
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="STUB skill adicionar_sfx (MVP placeholder)")
    p.add_argument("--input", help="Input file/JSON")
    p.add_argument("--output", required=True)
    p.add_argument("--config", help="JSON config (optional)")
    args, unknown = p.parse_known_args(argv)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.input and Path(args.input).suffix.lower() in {".mp4", ".mov", ".webm"}:
        shutil.copy(args.input, out)
    else:
        out.write_text(json.dumps({
            "_stub": True,
            "_skill": "adicionar_sfx",
            "_message": "Stub MVP — implementação real em v1.1+",
        }, indent=2))

    print(f"OK (stub): adicionar_sfx → {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
