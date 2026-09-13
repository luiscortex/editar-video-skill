"""Production safety net v2 — 8 quality checks pra workflow v2.0.0.

Cada check retorna dict {status, name, detail}.
status ∈ {"OK", "FAIL", "SKIP"}.
"""
import json
import subprocess
import tempfile
from pathlib import Path

# Threshold de distância imagehash.phash pra considerar 2 frames "duplicados"
# (~5 é o limite standard da lib pra near-duplicate).
PHASH_NEAR_DUPLICATE = 5

# Deps opcionais consolidadas no topo — facilita tooling (ruff/mypy) e leitura.
try:
    import imagehash
    from PIL import Image, ImageFont
    HAS_IMAGEHASH = True
    HAS_PIL = True
except ImportError:
    HAS_IMAGEHASH = False
    HAS_PIL = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from skimage.metrics import structural_similarity as ssim_metric
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


def OK(name: str, detail: str) -> dict:
    return {"status": "OK", "name": name, "detail": detail}


def FAIL(name: str, detail: str) -> dict:
    return {"status": "FAIL", "name": name, "detail": detail}


def SKIP(name: str, detail: str) -> dict:
    return {"status": "SKIP", "name": name, "detail": detail}


def _extrair_frames_1fps(video_path: Path) -> list[Path]:
    out_dir = Path(tempfile.mkdtemp(prefix="frames_"))
    subprocess.check_call([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", "fps=1", str(out_dir / "f_%04d.png")
    ], stderr=subprocess.DEVNULL)
    return sorted(out_dir.glob("f_*.png"))


def check_frame_variance(video_path: Path, threshold: float = 0.95) -> dict:
    """FAIL se > threshold% pares consecutivos têm hash idêntico (vídeo travado)."""
    if not HAS_IMAGEHASH:
        return SKIP("frame_variance", "imagehash não instalado")
    frames = _extrair_frames_1fps(video_path)
    if len(frames) < 2:
        return SKIP("frame_variance", f"Apenas {len(frames)} frame extraído")
    hashes = [imagehash.phash(Image.open(f)) for f in frames]
    pares_iguais = sum(1 for a, b in zip(hashes, hashes[1:]) if (a - b) < PHASH_NEAR_DUPLICATE)
    ratio = pares_iguais / max(len(hashes) - 1, 1)
    if ratio > threshold:
        return FAIL("frame_variance", f"Frame estático: {ratio:.0%} dos frames são idênticos")
    return OK("frame_variance", f"variação OK: {(1-ratio):.0%}")


def _ffprobe_keyframes(video_path: Path) -> list[float]:
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-select_streams", "v",
        "-show_entries", "packet=pts_time,flags",
        "-of", "csv=print_section=0",
        str(video_path)
    ], text=True)
    keyframes = []
    for line in out.strip().splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                keyframes.append(float(parts[0]))
            except ValueError:
                continue
    return keyframes


def check_keyframe_density(video_path: Path, max_gap_s: float = 2.5) -> dict:
    keyframes = _ffprobe_keyframes(video_path)
    if len(keyframes) < 2:
        return SKIP("keyframe_density", f"Apenas {len(keyframes)} keyframe")
    gaps = [b - a for a, b in zip(keyframes, keyframes[1:])]
    max_gap = max(gaps)
    if max_gap > max_gap_s:
        return FAIL("keyframe_density", f"Gap máximo {max_gap:.1f}s (limite {max_gap_s}s)")
    return OK("keyframe_density", f"gap máximo {max_gap:.1f}s")


def _ffprobe_stream_duration(video_path: Path, stream: str) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", stream,
        "-show_entries", "stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)
    ], text=True).strip()
    return float(out) if out else 0.0


def check_av_sync(video_path: Path, tolerance_s: float = 0.1) -> dict:
    v_dur = _ffprobe_stream_duration(video_path, "v:0")
    a_dur = _ffprobe_stream_duration(video_path, "a:0")
    diff = abs(v_dur - a_dur)
    if diff > tolerance_s:
        return FAIL("av_sync", f"drift {diff:.3f}s (v={v_dur:.3f}s a={a_dur:.3f}s)")
    return OK("av_sync", f"drift {diff:.3f}s")


def check_schema_lock(plano_path: Path, renderizados_path: Path) -> dict:
    """Compara inserts aprovados (plano-inserts.json) vs renderizados."""
    if not plano_path.exists():
        return SKIP("schema_lock", "sem plano-inserts.json")
    if not renderizados_path.exists():
        return SKIP("schema_lock", "sem inserts-renderizados.json")
    plano = json.loads(plano_path.read_text())
    renderizados = json.loads(renderizados_path.read_text())
    plano_ids = {(i["at_s"], i["tipo"]) for i in plano["inserts"]}
    render_ids = {(i["at_s"], i["tipo"]) for i in renderizados}
    diff = plano_ids.symmetric_difference(render_ids)
    if diff:
        return FAIL("schema_lock", f"{len(diff)} inserts diferem do plano: {sorted(diff)}")
    return OK("schema_lock", f"{len(plano_ids)}/{len(plano_ids)} inserts batem")


def _load_font(font_size: int):
    """Tenta Plus Jakarta Sans ExtraBold; fallback Helvetica; default PIL."""
    candidatos = [
        "/Library/Fonts/PlusJakartaSans-ExtraBold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in candidatos:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, font_size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    """Quebra texto em linhas que cabem em max_width pixels.

    Se uma palavra sozinha estoura max_width (caso patológico: palavra
    sem espaços maior que a largura), conta linhas proporcionais ao
    overflow (cada bloco de max_width vira 1 linha).
    """
    palavras = text.split()
    linhas, atual = [], ""
    for palavra in palavras:
        teste = (atual + " " + palavra).strip()
        bbox = font.getbbox(teste)
        largura = bbox[2] - bbox[0]
        if largura <= max_width:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            bbox_word = font.getbbox(palavra)
            w_word = bbox_word[2] - bbox_word[0]
            if w_word > max_width:
                # Palavra solitária maior que max_width — estima linhas pelo overflow
                n_overflow = (w_word + max_width - 1) // max_width
                for _ in range(n_overflow):
                    linhas.append(palavra)
                atual = ""
            else:
                atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def check_caption_multiline(
    transcript_path: Path,
    video_width: int = 720,
    side_margin: int = 60,
    font_size: int = 48,
    words_per_block: int = 2,
) -> dict:
    if not HAS_PIL:
        return SKIP("caption_multiline", "PIL não instalado")
    words = json.loads(transcript_path.read_text())["words"]
    max_width = video_width - 2 * side_margin
    font = _load_font(font_size)
    blocos_3linhas = []
    for i in range(0, len(words), words_per_block):
        chunk = words[i:i + words_per_block]
        texto = " ".join(w["word"] for w in chunk)
        linhas = _wrap_text(texto, font, max_width)
        if len(linhas) > 2:
            blocos_3linhas.append((texto, len(linhas)))
    if blocos_3linhas:
        return FAIL(
            "caption_multiline",
            f"{len(blocos_3linhas)} blocos com 3+ linhas. Reprocesse com words_per_block=1. Ex: {blocos_3linhas[:2]}"
        )
    return OK("caption_multiline", f"todos os {len(words)//words_per_block} blocos ≤ 2 linhas")


def _extract_frame_at(video_path: Path, ts: float, out_png: Path) -> None:
    subprocess.check_call([
        "ffmpeg", "-y", "-ss", str(ts), "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2", str(out_png)
    ], stderr=subprocess.DEVNULL)


def _tem_texto_na_borda(coluna: "np.ndarray", threshold_branco: int = 220) -> bool:
    """Detecta pixels brancos intensos na coluna (provável texto BRANCO).

    LIMITAÇÃO: só detecta legenda branca (padrão do Design System).
    Texto preto/colorido NÃO é detectado — se mudar o estilo no futuro,
    ajustar threshold ou passar text_color como param.
    """
    mask = (coluna >= threshold_branco).all(axis=-1)
    return mask.any()


def check_caption_inside_frame(
    video_path: Path,
    transcript_path: Path,
    side_margin: int = 60,
    n_samples: int = 5,
) -> dict:
    if not HAS_PIL or not HAS_NUMPY:
        return SKIP("caption_inside_frame", "PIL/numpy não disponível")
    words = json.loads(transcript_path.read_text()).get("words", [])
    if not words:
        return SKIP("caption_inside_frame", "transcript vazio")
    timestamps = [(w["start"] + w["end"]) / 2 for w in words[:n_samples]]
    vazamentos = []
    with tempfile.TemporaryDirectory() as tmp:
        for ts in timestamps:
            png = Path(tmp) / f"f_{ts:.2f}.png"
            try:
                _extract_frame_at(video_path, ts, png)
            except subprocess.CalledProcessError:
                continue
            if not png.exists():
                continue
            img = np.array(Image.open(png).convert("RGB"))
            col_esq = img[:, :side_margin, :]
            col_dir = img[:, -side_margin:, :]
            if _tem_texto_na_borda(col_esq) or _tem_texto_na_borda(col_dir):
                vazamentos.append(ts)
    if vazamentos:
        return FAIL(
            "caption_inside_frame",
            f"texto vazou em {len(vazamentos)} amostras: {[round(t, 2) for t in vazamentos[:3]]}"
        )
    return OK("caption_inside_frame", f"sem vazamento em {len(timestamps)} amostras")


def _extrair_frames_percentuais(video_path: Path, percentuais: list[float]) -> list["np.ndarray"]:
    dur = max(_ffprobe_stream_duration(video_path, "v:0"), 0.1)
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for p in percentuais:
            ts = dur * p
            png = Path(tmp) / f"f_{p:.2f}.png"
            try:
                _extract_frame_at(video_path, ts, png)
                if png.exists():
                    frames.append(np.array(Image.open(png).convert("L")))
            except subprocess.CalledProcessError:
                continue
    return frames


def check_reference_fingerprint(video_path: Path, reference_path: "Path | None", min_ssim: float = 0.85) -> dict:
    if reference_path is None:
        return SKIP("reference_fingerprint", "sem referência")
    if not HAS_SKIMAGE or not HAS_NUMPY:
        return SKIP("reference_fingerprint", "scikit-image/numpy não instalado")
    pcts = [0.10, 0.25, 0.50, 0.75, 0.90]
    frames_new = _extrair_frames_percentuais(video_path, pcts)
    frames_ref = _extrair_frames_percentuais(reference_path, pcts)
    if len(frames_new) != len(frames_ref) or not frames_new:
        return SKIP("reference_fingerprint", "frames insuficientes")
    ssims = []
    for a, b in zip(frames_new, frames_ref):
        if a.shape != b.shape:
            b_img = Image.fromarray(b).resize((a.shape[1], a.shape[0]))
            b = np.array(b_img)
        ssims.append(ssim_metric(a, b, data_range=255))
    min_s = min(ssims)
    if min_s < min_ssim:
        return FAIL("reference_fingerprint", f"SSIM mínimo {min_s:.2f} < {min_ssim}")
    return OK("reference_fingerprint", f"SSIM mínimo {min_s:.2f}")


def check_playback_smoke(video_path: Path, duration_s: int = 60) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return SKIP("playback_smoke", "Playwright não instalado")
    html = f"""<!DOCTYPE html><html><body>
<video id="v" src="file://{video_path}" autoplay muted></video>
<script>
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(e.message));
document.getElementById('v').addEventListener('error', e => window.__errors.push('video:' + e.message));
</script>
</body></html>"""
    html_path = video_path.parent / f"_playback_smoke_{video_path.stem}.html"
    html_path.write_text(html)
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as e:
                return SKIP("playback_smoke", f"chromium não disponível: {e}")
            page = browser.new_page()
            page.goto(f"file://{html_path}")
            page.wait_for_timeout(min(duration_s, 10) * 1000)
            errors = page.evaluate("window.__errors")
            ct = page.evaluate("document.getElementById('v').currentTime")
            browser.close()
        if errors:
            return FAIL("playback_smoke", f"{len(errors)} erros: {errors[:3]}")
        if ct == 0:
            return FAIL("playback_smoke", "currentTime ficou em 0 (vídeo travou)")
        return OK("playback_smoke", f"currentTime avançou pra {ct:.1f}s sem erros")
    finally:
        if html_path.exists():
            html_path.unlink()


import argparse
from datetime import datetime

STAGE_CHECKS = {
    "cortado": ["frame_variance", "av_sync"],
    "master": ["frame_variance", "keyframe_density", "schema_lock", "playback_smoke", "av_sync"],
    "legendado": ["frame_variance", "keyframe_density", "reference_fingerprint", "playback_smoke", "av_sync", "caption_inside_frame", "caption_multiline"],
    "final": ["frame_variance", "keyframe_density", "playback_smoke", "av_sync"],
}


def run_for_stage(
    stage: str,
    video_path: Path,
    transcript_path: "Path | None" = None,
    reference_path: "Path | None" = None,
    plano_path: "Path | None" = None,
    renderizados_path: "Path | None" = None,
) -> dict:
    checks_to_run = STAGE_CHECKS.get(stage, [])
    results = []
    for name in checks_to_run:
        if name == "frame_variance":
            results.append(check_frame_variance(video_path))
        elif name == "keyframe_density":
            results.append(check_keyframe_density(video_path))
        elif name == "av_sync":
            results.append(check_av_sync(video_path))
        elif name == "schema_lock":
            results.append(
                check_schema_lock(plano_path, renderizados_path)
                if plano_path and renderizados_path
                else SKIP("schema_lock", "sem plano/renderizados")
            )
        elif name == "playback_smoke":
            results.append(check_playback_smoke(video_path, duration_s=10))
        elif name == "caption_inside_frame":
            results.append(
                check_caption_inside_frame(video_path, transcript_path)
                if transcript_path
                else SKIP("caption_inside_frame", "sem transcript")
            )
        elif name == "caption_multiline":
            results.append(
                check_caption_multiline(transcript_path)
                if transcript_path
                else SKIP("caption_multiline", "sem transcript")
            )
        elif name == "reference_fingerprint":
            results.append(check_reference_fingerprint(video_path, reference_path))
    passed = sum(1 for r in results if r["status"] == "OK")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    overall = "PASS" if failed == 0 else "FAIL"
    return {
        "stage": stage,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "checks": [{"id": i + 1, **r} for i, r in enumerate(results)],
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "overall": overall,
    }


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quality_gates")
    p.add_argument("--video", required=True)
    p.add_argument("--stage", required=True, choices=list(STAGE_CHECKS.keys()))
    p.add_argument("--transcript")
    p.add_argument("--reference")
    p.add_argument("--plano")
    p.add_argument("--renderizados")
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    report = run_for_stage(
        stage=args.stage,
        video_path=Path(args.video),
        transcript_path=Path(args.transcript) if args.transcript else None,
        reference_path=Path(args.reference) if args.reference else None,
        plano_path=Path(args.plano) if args.plano else None,
        renderizados_path=Path(args.renderizados) if args.renderizados else None,
    )
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
