"""Slug determinístico + meta.json + cache hit (estilo claude-watch)."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

LIBRARY_ROOT = Path.home() / "Documents" / "luiscortex" / "edicoes"


def slug_for(input_path: str, style: str, platform: str = "reels") -> str:
    """YYYY-MM-DD-<basename>-<sha1[:4]> baseado em input + params."""
    name = Path(input_path).stem.lower().replace(" ", "-")[:30]
    h = hashlib.sha1(f"{input_path}|{style}|{platform}".encode()).hexdigest()[:4]
    date = datetime.now().strftime("%Y-%m-%d")
    return f"{date}-{name}-{h}"


def write_meta(work_dir: Path, meta: dict) -> None:
    """Grava meta.json no work_dir."""
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def read_meta(work_dir: Path) -> dict | None:
    """Lê meta.json. Retorna None se não existir."""
    p = work_dir / "meta.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def cache_hit(work_dir: Path, source_hash: str) -> bool:
    """Cache hit se meta.json existe e source_hash bate."""
    meta = read_meta(work_dir)
    if meta is None:
        return False
    return meta.get("source_hash") == source_hash


def hash_file(path: Path) -> str:
    """SHA256 de um arquivo (pra cache invalidation)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
