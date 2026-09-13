"""Auditoria de decisões dos gates + helper de undo."""
import json
import os
from datetime import datetime
from pathlib import Path

# Limite de undos consecutivos lidos pelo orchestrator a partir das entries
# (consumido pelo modo `desfaz` em orchestrator.py — Task 25)
MAX_UNDO = 3


def _log_path(work_dir: Path) -> Path:
    return work_dir / "edit_log.json"


def load_log(work_dir: Path) -> dict:
    path = _log_path(work_dir)
    if not path.exists():
        return {"version": "2.0.0", "slug": None, "entries": [], "quality_checks": []}
    return json.loads(path.read_text())


def _save_log(work_dir: Path, log: dict) -> None:
    """Atomic write: escreve em .tmp e renomeia, evitando JSON corrompido."""
    work_dir.mkdir(parents=True, exist_ok=True)
    final = _log_path(work_dir)
    tmp = final.with_suffix(final.suffix + ".tmp")
    tmp.write_text(json.dumps(log, indent=2, ensure_ascii=False))
    os.replace(tmp, final)


def log_decision(work_dir: Path, slug: str, gate: int, etapa: str, decisao: str, detalhe: str | None = None) -> None:
    log = load_log(work_dir)
    # setdefault defensivo pra logs de versão anterior sem essas chaves
    log.setdefault("entries", [])
    log.setdefault("quality_checks", [])
    if log["slug"] is None:
        log["slug"] = slug
    log["entries"].append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "gate": gate,
        "etapa": etapa,
        "decisao": decisao,
        "detalhe": detalhe,
    })
    _save_log(work_dir, log)


def log_quality_check(work_dir: Path, slug: str, stage: str, resultado: str, report_path: str) -> None:
    log = load_log(work_dir)
    # setdefault defensivo pra logs de versão anterior sem essas chaves
    log.setdefault("entries", [])
    log.setdefault("quality_checks", [])
    if log["slug"] is None:
        log["slug"] = slug
    log["quality_checks"].append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "resultado": resultado,
        "report_path": report_path,
    })
    _save_log(work_dir, log)
