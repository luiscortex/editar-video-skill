"""Auto-conserto de quality_gate failures críticas.

Aplicado APENAS em checks que têm fix conhecido:
- caption_multiline → reduz words_per_block (2 → 1)
- caption_inside_frame → aumenta side_margin (+20px)

Outros failures viram warning pro user resolver manualmente.

CLI standalone (uso opcional pelo orchestrator):
    python3 auto_fix.py --report report.json --config config.json --output new_config.json
"""
import argparse
import json
from pathlib import Path


def _has_fail(report: dict, check_name: str) -> bool:
    return any(
        c.get("name") == check_name and c.get("status") == "FAIL"
        for c in report.get("checks", [])
    )


def auto_fix_caption_multiline(report: dict, config: dict) -> tuple[dict, str]:
    """Se caption_multiline FAIL, reduz words_per_block (mín 1).

    Returns (new_config, action_description). Se sem FAIL, retorna (config, "").
    """
    if not _has_fail(report, "caption_multiline"):
        return config, ""
    current = config.get("words_per_block", 2)
    new = max(1, current - 1)
    new_config = {**config, "words_per_block": new}
    return new_config, f"Reduziu words_per_block de {current} para {new}"


def auto_fix_caption_inside_frame(report: dict, config: dict) -> tuple[dict, str]:
    """Se caption_inside_frame FAIL, aumenta side_margin em +20px."""
    if not _has_fail(report, "caption_inside_frame"):
        return config, ""
    current = config.get("side_margin", 60)
    new = current + 20
    new_config = {**config, "side_margin": new}
    return new_config, f"Aumentou side_margin de {current} para {new}px"


def auto_fix_all(report: dict, config: dict) -> tuple[dict, list[str]]:
    """Aplica todos os auto-fixes conhecidos. Retorna config + lista de ações."""
    actions: list[str] = []
    new_config = dict(config)
    new_config, act = auto_fix_caption_multiline(report, new_config)
    if act:
        actions.append(act)
    new_config, act = auto_fix_caption_inside_frame(report, new_config)
    if act:
        actions.append(act)
    return new_config, actions


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="auto_fix")
    p.add_argument("--report", required=True, help="JSON do quality_gates run")
    p.add_argument("--config", required=True, help="JSON config atual (words_per_block, side_margin)")
    p.add_argument("--output", required=True, help="Onde salvar novo config")
    args = p.parse_args(argv)

    report = json.loads(Path(args.report).read_text())
    config = json.loads(Path(args.config).read_text())
    new_config, actions = auto_fix_all(report, config)

    Path(args.output).write_text(json.dumps(new_config, indent=2, ensure_ascii=False))
    if actions:
        for a in actions:
            print(f"[auto_fix] {a}")
        return 0
    print("[auto_fix] nenhum auto-fix aplicável")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
