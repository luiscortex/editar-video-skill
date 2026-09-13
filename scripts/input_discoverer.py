"""Parse pasta de input — identifica brutos (single/multi-cam) + inserts manuais + roteiro."""
import re
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
PREFIX_RX = re.compile(r"^(\d{2})-(.+)$")


def _parse_filename(name: str) -> tuple[int | None, str | None]:
    """Retorna (cena, angulo/descricao) ou (None, None) se sem prefixo."""
    stem = Path(name).stem
    m = PREFIX_RX.match(stem)
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)


def discover(pasta: Path) -> dict:
    """Lê estrutura da pasta e retorna inventário."""
    pasta = Path(pasta)
    if not pasta.exists():
        raise FileNotFoundError(f"Pasta não existe: {pasta}")

    brutos = []
    inserts_manuais = []
    roteiro_path = None

    for f in pasta.iterdir():
        if f.is_file() and f.suffix.lower() in {".md", ".txt"}:
            if not f.stem.lower().startswith(("readme", "changelog", "license")):
                roteiro_path = str(f)
                break

    brutos_dir = pasta / "brutos"
    if brutos_dir.is_dir():
        for f in sorted(brutos_dir.iterdir()):
            if f.suffix.lower() in VIDEO_EXTS:
                cena, angulo = _parse_filename(f.name)
                brutos.append({
                    "path": str(f),
                    "cena": cena,
                    "angulo": angulo,
                })
    else:
        for f in sorted(pasta.iterdir()):
            if f.suffix.lower() in VIDEO_EXTS:
                brutos.append({"path": str(f), "cena": None, "angulo": None})

    inserts_dir = pasta / "inserts-manuais"
    if inserts_dir.is_dir():
        for f in sorted(inserts_dir.iterdir()):
            if f.suffix.lower() in (VIDEO_EXTS | IMAGE_EXTS):
                cena, descricao = _parse_filename(f.name)
                inserts_manuais.append({
                    "path": str(f),
                    "cena": cena,
                    "descricao": descricao or f.stem,
                })

    cenas_numeradas = [b["cena"] for b in brutos if b["cena"] is not None]
    multi_cam = len(cenas_numeradas) != len(set(cenas_numeradas))

    return {
        "pasta": str(pasta),
        "brutos": brutos,
        "inserts_manuais": inserts_manuais,
        "roteiro_path": roteiro_path,
        "multi_cam": multi_cam,
    }


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) != 2:
        sys.exit("Usage: input_discoverer.py <pasta>")
    print(json.dumps(discover(Path(sys.argv[1])), indent=2, ensure_ascii=False))
