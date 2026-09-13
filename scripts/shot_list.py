"""Estrutura central de decisões de edição."""
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Scene:
    n: int
    tipo: str  # "hook" | "passo" | "ponte" | "cta" | "outro"
    t_start: float
    t_end: float
    titulo_overlay: Optional[str] = None
    assets_necessarios: list = field(default_factory=list)
    face_zone: Optional[dict] = None
    preservar: list = field(default_factory=list)


@dataclass
class ShotList:
    duracao_total_s: float
    plataforma: str  # "reels" | "yt-long" | "linkedin" | etc
    estilo: str  # "04-premium-cinematic" | etc
    cenas: list[Scene] = field(default_factory=list)
    inserts: list[dict] = field(default_factory=list)  # MVP Fase 2 — {at_s, duration_s, asset_path, fonte, ...}
    sfx_sugeridos: list = field(default_factory=list)
    alertas: list = field(default_factory=list)

    def add_scene(self, scene: Scene) -> None:
        self.cenas.append(scene)

    def save(self, path: Path) -> None:
        data = asdict(self)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> "ShotList":
        data = json.loads(path.read_text())
        cenas = [Scene(**c) for c in data.pop("cenas", [])]
        sl = cls(**data)
        sl.cenas = cenas
        return sl

    def validate(self) -> list[str]:
        errors = []
        for i in range(1, len(self.cenas)):
            prev, curr = self.cenas[i - 1], self.cenas[i]
            if curr.t_start < prev.t_end:
                errors.append(f"Scenes {prev.n} and {curr.n} overlap")
        if self.cenas and self.cenas[-1].t_end > self.duracao_total_s + 0.5:
            errors.append("Last scene exceeds duracao_total_s")
        return errors
