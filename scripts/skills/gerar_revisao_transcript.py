"""Modo generate: transcript.json → transcript-revisao.md editável.
Modo parse: transcript-revisao.md editado → transcript-corrigido.json.

User edita brand names e gírias mal transcritas pelo Whisper.
"""
import argparse
import json
import os
import re
from pathlib import Path

BRANDS_SUSPEITAS = {"anthropic", "claude", "cursor", "notion", "heygen", "davinci"}

LINE_RE = re.compile(r"\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]\s+(.+?)(?:\s*#.*)?$")


def _agrupar_em_frases(words: list[dict], max_gap_s: float = 0.6) -> list[dict]:
    """Agrupa palavras em frases curtas (uma linha por frase)."""
    if not words:
        return []
    frases = []
    atual = [words[0]]
    for w in words[1:]:
        gap = w["start"] - atual[-1]["end"]
        if gap > max_gap_s or len(atual) >= 8:
            frases.append(atual)
            atual = [w]
        else:
            atual.append(w)
    if atual:
        frases.append(atual)
    return [
        {
            "start": f[0]["start"],
            "end": f[-1]["end"],
            "texto": " ".join(w["word"] for w in f),
        }
        for f in frases
    ]


def _palavra_suspeita(palavra: str) -> bool:
    """Palavras suspeitas: brand conhecida OU maiúscula no meio (ex: 'Antrópico')."""
    p = palavra.lower().strip(".,!?")
    if p in BRANDS_SUSPEITAS:
        return True
    # Maiúscula em palavra que não é início de frase
    if re.match(r"^[A-Z][a-zàâãéêíóôõúç]+", palavra):
        return True
    return False


def gerar_revisao(transcript_path: Path, output_path: Path) -> None:
    transcript = json.loads(transcript_path.read_text())
    words = transcript.get("words", [])
    frases = _agrupar_em_frases(words)
    linhas = [
        "# Transcript — Revisão Manual",
        "# Instruções: corrija erros do Whisper diretamente no texto.",
        "# NÃO altere os timestamps entre colchetes — são usados pra gerar a legenda.",
        "# Salve o arquivo e responda 'pronto' no chat quando terminar.",
        "",
    ]
    for f in frases:
        suspeita = any(_palavra_suspeita(w) for w in f["texto"].split())
        marker = "  # ⚠ revisar" if suspeita else ""
        linhas.append(f"[{f['start']:.2f}-{f['end']:.2f}] {f['texto']}{marker}")
    output_path.write_text("\n".join(linhas))


def _distribuir_timestamps(texto: str, start: float, end: float) -> list[dict]:
    palavras = texto.split()
    if not palavras:
        return []
    duracao = end - start
    passo = duracao / len(palavras) if palavras else 0
    return [
        {
            "word": w,
            "start": round(start + i * passo, 3),
            "end": round(start + (i + 1) * passo, 3),
        }
        for i, w in enumerate(palavras)
    ]


def parse_revisao(revisao_path: Path, original_path: Path, output_path: Path) -> None:
    palavras_corrigidas = []
    for linha in revisao_path.read_text().splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        m = LINE_RE.match(linha)
        if not m:
            continue
        start, end, texto = float(m.group(1)), float(m.group(2)), m.group(3).strip()
        palavras_corrigidas.extend(_distribuir_timestamps(texto, start, end))
    payload = json.dumps({"words": palavras_corrigidas}, indent=2, ensure_ascii=False)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(payload)
    os.replace(tmp, output_path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="gerar_revisao_transcript")
    p.add_argument("--mode", required=True, choices=["generate", "parse"])
    p.add_argument("--transcript")
    p.add_argument("--revisao")
    p.add_argument("--original")
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    if args.mode == "generate":
        gerar_revisao(Path(args.transcript), Path(args.output))
        return 0
    if args.mode == "parse":
        parse_revisao(Path(args.revisao), Path(args.original), Path(args.output))
        return 0
    raise SystemExit(f"Modo desconhecido: {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
