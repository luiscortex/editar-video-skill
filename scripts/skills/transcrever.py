"""Transcreve áudio/vídeo via faster-whisper word-level. CLI standalone."""
import argparse
import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel


def transcrever(input_path: Path, model_size: str = "base", language: str = "pt") -> dict:
    """Retorna dict com language + words [{word, start, end, probability}]."""
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(input_path),
        language=language,
        word_timestamps=True,
        beam_size=1,
        vad_filter=True,
    )
    words = []
    text_chunks = []
    for seg in segments:
        text_chunks.append(seg.text)
        if seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 3),
                })
    return {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration_s": round(info.duration, 2),
        "text": " ".join(text_chunks).strip(),
        "words": words,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Transcreve áudio/vídeo com word-level timestamps")
    p.add_argument("--input", required=True, help="Caminho do .mp4/.wav/.m4a")
    p.add_argument("--output", required=True, help="Caminho do .json de saída")
    p.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium", "large-v3"],
                   help="Default 'base' (FIX P0 #6 — 'tiny' tem WER alto demais em PT)")
    p.add_argument("--language", default="pt")
    args = p.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erro: input não existe: {input_path}", file=sys.stderr)
        return 1

    result = transcrever(input_path, model_size=args.model, language=args.language)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"OK: {len(result['words'])} palavras em {result['duration_s']}s ({result['language']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
