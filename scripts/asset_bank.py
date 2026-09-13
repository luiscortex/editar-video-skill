"""Banco local de assets — busca por tag/descrição."""
import json
from pathlib import Path

DEFAULT_BANK = Path.home() / "Documents" / "luiscortex" / "asset-bank"


def init_bank(bank_root: Path = DEFAULT_BANK) -> None:
    """Cria estrutura de pastas + _index.json vazio."""
    bank_root = Path(bank_root)
    for sub in ["photos", "videos", "stickers", "audio/sfx", "audio/music-beds"]:
        (bank_root / sub).mkdir(parents=True, exist_ok=True)
    index_path = bank_root / "_index.json"
    if not index_path.exists():
        index_path.write_text(json.dumps({"assets": []}, indent=2))


class AssetBank:
    def __init__(self, root: Path = DEFAULT_BANK):
        self.root = Path(root)
        self.index_path = self.root / "_index.json"
        if not self.index_path.exists():
            self.index = {"assets": []}
        else:
            self.index = json.loads(self.index_path.read_text())

    def search(self, query: str, tipo: str | None = None) -> list[dict]:
        """Busca por substring em tags/descricao. Filtra por tipo se passado."""
        q = query.lower()
        results = []
        for a in self.index["assets"]:
            if tipo and a.get("tipo") != tipo:
                continue
            tags_match = any(q in t.lower() for t in a.get("tags", []))
            desc_match = q in a.get("descricao", "").lower()
            if tags_match or desc_match:
                results.append(a)
        return results

    def register(self, asset: dict) -> None:
        """Adiciona novo asset ao index e salva."""
        self.index["assets"].append(asset)
        self.index_path.write_text(json.dumps(self.index, indent=2, ensure_ascii=False))
