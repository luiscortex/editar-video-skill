"""Lê estilo escolhido, gera style_config.json lido por todas as skills downstream.

FIX P1 #10: também inicializa esqueleto ~/Documents/luiscortex/edicoes-styles/
na primeira execução pra cumprir promessa do §10 da spec (sistema vivo de aprendizado).
"""
import argparse
import json
import sys
from pathlib import Path

EDICOES_STYLES = Path.home() / "Documents" / "luiscortex" / "edicoes-styles"

PRESETS = {
    "01-stepped-tutorial": {
        "estilo": "01-stepped-tutorial",
        "layout": "talking-head-fullframe-with-overlay-cards",
        "caption_style": "B-boxed",
        "caption_color_keyword": "orange",
        "titulo_padrao": "TT2",
        "transitions": "hard-cut",
        "color_grading": "neutral",
        "broll_treatment": "none",
        "particles": False,
        "head_position": "fullframe",
        "head_size_pct": 100,
        "cut_pace_avg_s": 3.0,
        "music_default": "lofi-tutorial",
        "sfx_intensity": "medium",
    },
    "02-clean-handdrawn": {
        "estilo": "02-clean-handdrawn",
        "layout": "talking-head-dominant-with-svg-doodles",
        "caption_style": "A-clean",
        "caption_color_keyword": "navy",
        "titulo_padrao": "TT1",
        "transitions": "fade-300ms",
        "color_grading": "warm-soft",
        "broll_treatment": "none",
        "particles": False,
        "head_position": "fullframe",
        "head_size_pct": 100,
        "cut_pace_avg_s": 4.0,
        "music_default": "acoustic-warm",
        "sfx_intensity": "subtle",
    },
    "03-split-cinematic": {
        "estilo": "03-split-cinematic",
        "layout": "split-50-50-top-broll-bottom-head",
        "caption_style": "C-serif",
        "caption_color_keyword": "orange",
        "titulo_padrao": "TT4",
        "transitions": "crossfade-800ms",
        "color_grading": "cool-editorial",
        "broll_treatment": "static",
        "particles": False,
        "head_position": "bottom-half",
        "head_size_pct": 50,
        "cut_pace_avg_s": 5.0,
        "music_default": "ambient-cinematic",
        "sfx_intensity": "subtle",
    },
    "04-premium-cinematic": {
        "estilo": "04-premium-cinematic",
        "layout": "broll-fullframe-head-pip-graduated-mask",
        "caption_style": "C-serif",
        "caption_color_keyword": "orange",
        "titulo_padrao": "TT4",
        "transitions": "crossfade-1.5s",
        "color_grading": "warm-cinematic",
        "broll_treatment": "ken-burns-zoom-1.0-to-1.05",
        "particles": True,
        "head_position": "bottom-pip",
        "head_size_pct": 25,
        "cut_pace_avg_s": 4.5,
        "music_default": "ambient-cinematic",
        "sfx_intensity": "subtle",
    },
}


def init_edicoes_styles_skeleton() -> None:
    """Cria esqueleto de aprendizado por estilo (idempotente)."""
    base = EDICOES_STYLES / "_base"
    base.mkdir(parents=True, exist_ok=True)

    tom_global = base / "tom-visual-global.md"
    if not tom_global.exists():
        tom_global.write_text(
            "# Tom Visual Global @luiscortex\n\n"
            "Regras CROSS-style (vale pra todos os 4 estilos):\n\n"
            "- Paleta: navy + Claude orange + cyan (acento técnico)\n"
            "- Fontes: Plus Jakarta Sans 800 + Instrument Serif italic + JetBrains Mono\n"
            "- NUNCA usar Inter/Roboto/Arial\n"
            "- Mascote Clawd: só em vídeo que mencione Claude. Max 1× por vídeo.\n"
            "- Watermark @luiscortex discreto canto inferior\n",
            encoding="utf-8",
        )

    proibicoes = base / "proibicoes-edicao.md"
    if not proibicoes.exists():
        proibicoes.write_text(
            "# Proibições — Edição (cross-style)\n\n"
            "- NUNCA escrever 'CEO' ao lado do handle (@luiscortex sozinho)\n"
            "- NUNCA usar fonte Inter/Roboto/Arial\n"
            "- NUNCA mascote sem mencionar Claude\n",
            encoding="utf-8",
        )

    for estilo_id in PRESETS.keys():
        estilo_dir = EDICOES_STYLES / "estilos" / estilo_id
        estilo_dir.mkdir(parents=True, exist_ok=True)
        padroes = estilo_dir / "padroes.md"
        if not padroes.exists():
            padroes.write_text(
                f"# Padrões — {estilo_id}\n\n"
                "Padrões cravados pelo aprendizado automático (sistema vivo).\n"
                "Status: `EM OBSERVAÇÃO` na 1ª ocorrência → `CRAVADO` após 3.\n\n"
                "(Vazio até a 1ª edição neste estilo.)\n",
                encoding="utf-8",
            )

    learnings = EDICOES_STYLES / "LEARNINGS.md"
    if not learnings.exists():
        learnings.write_text(
            "# LEARNINGS — /editar-video\n\n"
            "Log datado de aprendizados. Cada entrada tem tag de estilo.\n",
            encoding="utf-8",
        )

    historico = EDICOES_STYLES / "historico"
    historico.mkdir(parents=True, exist_ok=True)


def aplicar_estilo(estilo: str) -> dict:
    if estilo not in PRESETS:
        raise ValueError(
            f"Estilo '{estilo}' não existe. "
            f"Disponíveis: {list(PRESETS.keys())}"
        )
    init_edicoes_styles_skeleton()
    return PRESETS[estilo].copy()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--estilo", required=True, help="ID do estilo (ex: 04-premium-cinematic)")
    p.add_argument("--output", required=True, help="style_config.json de saída")
    args = p.parse_args(argv)

    try:
        config = aplicar_estilo(args.estilo)
    except ValueError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1

    Path(args.output).write_text(json.dumps(config, indent=2, ensure_ascii=False))
    print(f"OK: estilo {args.estilo} aplicado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
