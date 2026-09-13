"""Helpers de state machine para o workflow v2.0.0 de /editar-video.

Cada edição persiste `workflow_state.json` em edicoes/<slug>/.
Ausência do arquivo = edição v1 (legacy).
"""
import json
import os
from pathlib import Path

# Fonte única da verdade da versão do schema. Os testes importam DAQUI —
# assertion com a versão cravada já quebrou duas vezes ao subir o schema
# (v2.1.0 e agora v2.2.0), e o conserto era sempre editar os testes na mão.
SCHEMA_VERSION = "2.2.0"   # v2.2: layout-per-insert + caption_position derivada

STAGES = [
    "inicial",
    "cuts_base",
    "transcript_revisao",  # gate 1 — REVISÃO Whisper antes de qualquer coisa que dependa de texto
    "plano_inserts",       # gate 2 — Claude propõe contextual a partir do transcript CORRIGIDO
    "master",              # gate 3 — vídeo com inserts compostos, sem legenda
    "legenda",             # gate 4 — burned-in caption + título overlay + CTA visual
    "acelerar",            # gate 5 — fator final (PPM analysis)
    "done",
]


def _state_path(work_dir: Path) -> Path:
    return work_dir / "workflow_state.json"


def init_state(work_dir: Path, slug: str, style_config: dict) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "version": SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "etapa_atual": STAGES[0],
        "etapas_completas": [],
        "style_config": style_config,
        "paths": {},
    }
    save_state(work_dir, state)
    return state


def load_state(work_dir: Path) -> dict | None:
    path = _state_path(work_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_state(work_dir: Path, state: dict) -> None:
    """Atomic write: escreve em .tmp e renomeia, evitando JSON corrompido se o
    processo morrer no meio da escrita."""
    final = _state_path(work_dir)
    tmp = final.with_suffix(final.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(tmp, final)


def advance_stage(work_dir: Path, current: str) -> dict:
    state = load_state(work_dir)
    if state is None:
        raise RuntimeError(f"workflow_state.json não existe em {work_dir}")
    if current not in STAGES:
        raise ValueError(f"Stage desconhecido: {current}")
    if current not in state["etapas_completas"]:
        state["etapas_completas"].append(current)
    idx = STAGES.index(current)
    state["etapa_atual"] = STAGES[idx + 1] if idx + 1 < len(STAGES) else "done"
    save_state(work_dir, state)
    return state


def rollback_stage(work_dir: Path) -> dict:
    """Volta 1 stage: remove última etapa completa, define como atual."""
    state = load_state(work_dir)
    if state is None:
        raise RuntimeError("workflow_state.json não existe")
    if not state["etapas_completas"]:
        raise RuntimeError("Nada pra desfazer — workflow no início")
    ultima = state["etapas_completas"].pop()
    state["etapa_atual"] = ultima
    save_state(work_dir, state)
    return state
