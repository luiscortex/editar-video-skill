"""Gera shot_list.json com decisões de B-roll automáticas.

Pipeline em 5 camadas:
  A. inserts manuais com prefixo de cena   (priority 100)
  B. inserts manuais livres, distribuídos  (priority 80)
  C. brand mentions no transcript          (priority 60)
  D. photo keywords no transcript          (priority 40)
  E. mascote Clawd quando "Claude" mencionado (priority 70, bypass min_gap)

Filtros: max-inserts, min-gap, zona-hook (t<1.5s), zona-cta (t > duracao-duration-1s).

Título de hook (Fase 3): inserido automaticamente a partir da 1ª frase do transcript.
  - tipo="titulo", priority=90, bypass das regras hook/gap/cta
  - asset_path=None (preenchido pelo orchestrator via animar_titulo.py)

Mascote Clawd (Fase 4 v1.1):
  - tipo="mascote", priority=70, bypass min_gap
  - aparece 0.5s ANTES da 1ª menção de "Claude" no transcript
  - overlay no canto inferior direito (25% da largura)
  - MVP: max 1× por vídeo
  - graceful skip se asset não existir
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Garante que scripts/ está no path quando rodado como __main__
_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.shot_list import ShotList, Scene
from scripts.asset_resolver import AssetResolver, BRAND_ALIASES, PHOTO_KEYWORDS


# ─── Constantes mascote ───────────────────────────────────────────────────────

MASCOTE_ASSETS_ROOT = Path.home() / "Documents" / "luiscortex" / "projeto instagram" / "mascot"
MASCOTE_DEFAULT_ACTION = "actions/typing-coder/clawd-typing-coder-alpha-3s.mov"


# ─── Helpers ──────────────────────────────────────────────────────────────────

# Muletas pt-br removidas SÓ se forem a 1ª ou 2ª palavra do hook
_MULETAS = {"aí", "ai", "tipo", "então", "entao", "olha"}

# Mapeamento estilo → tipo de título padrão
_ESTILO_TITULO_MAP: dict[str, str] = {
    "01-stepped-tutorial": "TT2",
    "02-clean-handdrawn": "TT1",
    "03-split-cinematic": "TT4",
    "04-premium-cinematic": "TT4",
}


def _build_titulo_hook(words: list[dict], style_config: dict) -> dict | None:
    """Extrai texto da 1ª frase do transcript e cria insert tipo='titulo'.

    Critérios de extração:
    - Pega até 8 palavras OU até 4 segundos a partir da 1ª palavra (o que vier primeiro)
    - Remove muletas pt-br (case-insensitive) se forem a 1ª ou 2ª palavra
    - Capitaliza primeira letra, remove pontuação trailing

    Retorna None se:
    - words vazio
    - texto resultante vazio após sanitização
    - style_config sem titulo_padrao (usa default TT4 — nunca None)
    """
    if not words:
        return None

    # Só gera título se houver config com estilo ou titulo_padrao explícito
    if not style_config or (not style_config.get("estilo") and not style_config.get("titulo_padrao")):
        return None

    MAX_WORDS = 8
    MAX_DURATION_S = 4.0

    first_word = words[0]
    first_start = first_word["start"]
    cutoff_time = first_start + MAX_DURATION_S

    # Coleta palavras até limite de quantidade ou tempo
    selected = []
    for w in words:
        if len(selected) >= MAX_WORDS:
            break
        if w["start"] > cutoff_time:
            break
        selected.append(w)

    if not selected:
        return None

    # Remove muletas apenas nas 2 primeiras posições
    filtered = []
    for i, w in enumerate(selected):
        word_lower = w["word"].strip().lower()
        if i < 2 and word_lower in _MULETAS:
            continue
        filtered.append(w["word"].strip())

    if not filtered:
        return None

    # Monta texto: capitaliza 1ª letra, remove pontuação trailing
    raw_text = " ".join(filtered)
    raw_text = raw_text.lstrip()
    if raw_text:
        raw_text = raw_text[0].upper() + raw_text[1:]
    raw_text = raw_text.rstrip(".,;:!?")
    raw_text = raw_text.strip()

    if not raw_text:
        return None

    # Determina tipo de título
    estilo = style_config.get("estilo", "04-premium-cinematic")
    titulo_type = style_config.get("titulo_padrao") or _ESTILO_TITULO_MAP.get(estilo, "TT4")

    # Duração: fica 1s além da última palavra selecionada, máximo 5s
    last_word = selected[-1]
    duration_s = min(5.0, last_word["end"] - first_word["start"] + 1.0)

    return {
        "at_s": first_word["start"],
        "duration_s": duration_s,
        "asset_path": None,
        "ken_burns": False,
        "fonte": "titulo_hook",
        "priority": 90,
        "tipo": "titulo",
        "titulo_text": raw_text,
        "titulo_type": titulo_type,
        "titulo_canvas_preset": "reel",
    }


def _load_inserts_manuais(inserts_path: Path) -> list[dict]:
    """Aceita discovery.json (lê campo inserts_manuais) ou lista direta."""
    data = json.loads(inserts_path.read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "inserts_manuais" in data:
        return data["inserts_manuais"]
    return []


def _load_config(config_path: Path | None) -> dict:
    if config_path and config_path.exists():
        return json.loads(config_path.read_text())
    return {}


def _build_candidates_manuais(inserts_manuais: list[dict], scenes: list[dict],
                               insert_duration_s: float) -> list[dict]:
    """Camadas A e B — inserts manuais com e sem cena."""
    candidates = []

    # Camada A: inserts com prefixo de cena → casa com scene[N-1]
    used_scene_indices: set[int] = set()
    for ins in inserts_manuais:
        cena = ins.get("cena")
        if cena is not None:
            idx = cena - 1
            if 0 <= idx < len(scenes):
                candidates.append({
                    "at_s": scenes[idx]["t"],
                    "duration_s": insert_duration_s,
                    "asset_path": ins["path"],
                    "fonte": "inserts_manuais",
                    "priority": 100,
                })
                used_scene_indices.add(idx)

    # Camada B: inserts livres → distribui uniformemente pelas cenas não ocupadas
    livres = [ins for ins in inserts_manuais if ins.get("cena") is None]
    free_scenes = [i for i in range(len(scenes)) if i not in used_scene_indices]
    n = len(livres)
    if n > 0 and len(free_scenes) > 0:
        if n >= len(free_scenes):
            chosen_indices = free_scenes  # todos
        else:
            step = len(free_scenes) / n
            chosen_indices = [free_scenes[int(i * step)] for i in range(n)]
        for ins, idx in zip(livres, chosen_indices):
            candidates.append({
                "at_s": scenes[idx]["t"],
                "duration_s": insert_duration_s,
                "asset_path": ins["path"],
                "fonte": "inserts_manuais_livre",
                "priority": 80,
            })

    return candidates


def _build_candidate_mascote(words: list[dict]) -> dict | None:
    """Camada E — mascote Clawd na 1ª menção de 'Claude' no transcript (MVP: max 1×).

    - Detecta word boundary case-insensitive para "claude"
    - at_s = palavra.start - 0.5s (introduz 0.5s antes da menção)
    - Graceful skip se asset não existir no filesystem
    """
    asset_path = MASCOTE_ASSETS_ROOT / MASCOTE_DEFAULT_ACTION
    if not asset_path.exists():
        print(
            f"  [warning] asset mascote não encontrado, pulando Clawd overlay: {asset_path}",
            file=sys.stderr,
        )
        return None

    for w in words:
        word_clean = w["word"].strip()
        if re.search(r"\bclaude\b", word_clean, re.IGNORECASE):
            at_s = max(0.0, w["start"] - 0.5)
            return {
                "at_s": at_s,
                "duration_s": 3.0,
                "asset_path": str(asset_path),
                "fonte": "mascote_clawd",
                "priority": 70,
                "tipo": "mascote",
                "mascote_position": "bottom-right",
                "mascote_scale": 0.25,
                "ken_burns": False,
            }

    return None


def _build_candidates_transcript(words: list[dict], insert_duration_s: float,
                                  resolver: AssetResolver) -> list[dict]:
    """Camadas C e D — brand mentions e photo keywords no transcript."""
    candidates = []

    # Camada C: brand mentions
    seen_brand_at: dict[str, float] = {}  # slug → último at_s aceito
    BRAND_DEDUP_WINDOW = 5.0  # segundos

    for w in words:
        word_clean = w["word"].strip().lower()
        slug = None
        for alias, s in BRAND_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", word_clean):
                slug = s
                break

        if slug is None:
            continue

        at_s = w["start"]
        last = seen_brand_at.get(slug)
        if last is not None and (at_s - last) < BRAND_DEDUP_WINDOW:
            continue  # dedup: mesmo logo em janela de 5s

        result = resolver.resolve(word_clean, cena=None, inserts_manuais=[])
        if result.get("fonte") == "logos_simpleicons":
            candidates.append({
                "at_s": at_s,
                "duration_s": insert_duration_s,
                "asset_path": result["path"],
                "fonte": "logos_simpleicons",
                "slug": result.get("slug", slug),
                "priority": 60,
            })
            seen_brand_at[slug] = at_s

    # Camada D: photo keywords
    seen_photo_ids: set[str] = set()
    for w in words:
        word_clean = w["word"].strip().lower()
        photo_id = None
        for keywords, pid in PHOTO_KEYWORDS:
            if any(re.search(rf"\b{re.escape(kw)}\b", word_clean) for kw in keywords):
                photo_id = pid
                break

        if photo_id is None or photo_id in seen_photo_ids:
            continue

        result = resolver.resolve(word_clean, cena=None, inserts_manuais=[])
        if result.get("fonte") == "fotos_user":
            candidates.append({
                "at_s": w["start"],
                "duration_s": insert_duration_s,
                "asset_path": result["path"],
                "fonte": "fotos_user",
                "photo_id": result.get("photo_id"),
                "priority": 40,
            })
            seen_photo_ids.add(photo_id)

    return candidates


def _filter_candidates(candidates: list[dict], max_inserts: int,
                        min_gap_s: float, duration_total_s: float,
                        insert_duration_s: float) -> list[dict]:
    """Greedy pick com regras de hook, CTA, gap, max.

    Regras hook/CTA aplicam a candidatos automáticos (transcript).
    Inserts manuais (A/B, priority>=80) passam pelo hook mas ainda
    respeitam a zona CTA e o min_gap.
    Títulos de hook (tipo='titulo') bypass TODAS as regras — sempre aceitos.
    Mascote (tipo='mascote') bypass min_gap mas respeita hook zone (t<1.5s) e CTA.
    """
    MANUAL_FONTES = {"inserts_manuais", "inserts_manuais_livre"}
    hook_floor = 1.5  # mascote usa 1.5s (mais conservador que B-roll automático)
    hook_floor_auto = 1.0  # B-roll automático usa 1.0s
    cta_ceil = duration_total_s - insert_duration_s - 1.0

    # Separa bypasses totais (título) dos demais candidatos
    titulo_inserts = [c for c in candidates if c.get("tipo") == "titulo"]
    # Mascote é gerenciado separadamente (bypass min_gap)
    mascote_inserts_raw = [c for c in candidates if c.get("tipo") == "mascote"]
    outros_candidates = [c for c in candidates if c.get("tipo") not in ("titulo", "mascote")]

    # Filtra mascotes (respeitam hook 1.5s e CTA, mas não min_gap)
    mascote_accepted: list[dict] = []
    for c in mascote_inserts_raw:
        at = c["at_s"]
        fonte = c.get("fonte", "mascote")
        if at < hook_floor:
            print(f"  [skipped] {fonte} @ {at:.1f}s — hook zone (<{hook_floor}s)", file=sys.stderr)
            continue
        if at > cta_ceil:
            print(f"  [skipped] {fonte} @ {at:.1f}s — cta zone (>{cta_ceil:.1f}s)", file=sys.stderr)
            continue
        mascote_accepted.append(c)
        print(f"  [mascote] {fonte} @ {at:.1f}s ({c['duration_s']:.1f}s) → {c['asset_path']}", file=sys.stderr)

    # Sort por priority desc, depois at_s asc
    ordered = sorted(outros_candidates, key=lambda c: (-c["priority"], c["at_s"]))

    accepted: list[dict] = []
    last_at = -999.0

    for c in ordered:
        if len(accepted) >= max_inserts:
            break
        at = c["at_s"]
        fonte = c.get("fonte", "?")
        is_manual = c.get("fonte") in MANUAL_FONTES

        # Regra hook: só bloqueia candidatos automáticos (transcript)
        if not is_manual and at < hook_floor_auto:
            print(f"  [skipped] {fonte} @ {at:.1f}s — hook zone (<{hook_floor_auto}s)", file=sys.stderr)
            continue
        # Regra CTA: aplica a todos
        if at > cta_ceil:
            print(f"  [skipped] {fonte} @ {at:.1f}s — cta zone (>{cta_ceil:.1f}s)", file=sys.stderr)
            continue
        # Min-gap: aplica a todos
        if at - last_at < min_gap_s and accepted:
            print(f"  [skipped] {fonte} @ {at:.1f}s — gap conflict (last @ {last_at:.1f}s, min_gap={min_gap_s}s)", file=sys.stderr)
            continue
        accepted.append(c)
        last_at = at

    # Merge: títulos e mascotes sempre na lista, sem contar no max_inserts
    all_accepted = titulo_inserts + mascote_accepted + accepted

    # Retorna ordenados por at_s (ordem cronológica)
    return sorted(all_accepted, key=lambda c: c["at_s"])


# ─── Função principal ─────────────────────────────────────────────────────────

def build_shot_list(
    scenes_path: Path,
    transcript_path: Path,
    inserts_manuais_path: Path,
    output_path: Path,
    max_inserts: int = 5,
    min_gap_s: float = 4.0,
    insert_duration_s: float = 3.0,
    bank_root: Path | None = None,
    config_path: Path | None = None,
) -> ShotList:
    """Gera ShotList com inserts automáticos. Salva em output_path."""
    # Carrega inputs
    scenes: list[dict] = json.loads(scenes_path.read_text())
    transcript: dict = json.loads(transcript_path.read_text())
    inserts_manuais = _load_inserts_manuais(inserts_manuais_path)
    config = _load_config(config_path)

    duration_s: float = transcript.get("duration_s", 0.0)
    words: list[dict] = transcript.get("words", [])

    plataforma = config.get("plataforma", "reels")
    estilo = config.get("estilo", "04-premium-cinematic")

    if bank_root is None:
        bank_root = Path.home() / ".editar-video/asset-bank"

    resolver = AssetResolver(bank_root=bank_root)

    # Coleta candidatos das 5 camadas
    candidates: list[dict] = []
    candidates += _build_candidates_manuais(inserts_manuais, scenes, insert_duration_s)
    candidates += _build_candidates_transcript(words, insert_duration_s, resolver)

    # Camada E: mascote Clawd (bypass min_gap, 1ª menção de "Claude" apenas)
    mascote_candidate = _build_candidate_mascote(words)
    if mascote_candidate:
        candidates.append(mascote_candidate)
        print(
            f"  [mascote_clawd] 'Claude' detectado @ {mascote_candidate['at_s'] + 0.5:.1f}s → "
            f"mascote entra @ {mascote_candidate['at_s']:.1f}s",
            file=sys.stderr,
        )

    # Título de hook (Fase 3) — bypass de regras, gerado separadamente
    titulo_hook = _build_titulo_hook(words, config)
    if titulo_hook:
        candidates.append(titulo_hook)
        print(
            f"  [titulo_hook] '{titulo_hook['titulo_text']}' ({titulo_hook['titulo_type']}) "
            f"@ {titulo_hook['at_s']:.1f}s ({titulo_hook['duration_s']:.1f}s)",
            file=sys.stderr,
        )

    # Filtra
    inserts = _filter_candidates(candidates, max_inserts, min_gap_s, duration_s, insert_duration_s)

    # Materializa cenas (1 por scene change)
    cenas: list[Scene] = []
    for i, scene in enumerate(scenes):
        t_start = scene["t"]
        t_end = scenes[i + 1]["t"] if i + 1 < len(scenes) else duration_s
        tipo = "hook" if i == 0 else ("cta" if i == len(scenes) - 1 else "passo")
        cenas.append(Scene(n=i + 1, tipo=tipo, t_start=t_start, t_end=t_end))

    # Se scenes.json só tem o anchor (1 cena), cria 1 cena cobrindo tudo
    if not cenas:
        cenas.append(Scene(n=1, tipo="hook", t_start=0.0, t_end=duration_s))

    # Monta ShotList
    sl = ShotList(
        duracao_total_s=duration_s,
        plataforma=plataforma,
        estilo=estilo,
        cenas=cenas,
        inserts=inserts,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sl.save(output_path)

    # Resumo stdout
    print(f"OK: shot_list gerado — {duration_s:.0f}s, {len(cenas)} cenas, {len(inserts)} inserts:")
    for ins in inserts:
        path_display = ins.get("asset_path", "?")
        fonte = ins.get("fonte", "?")
        print(f"  {ins['at_s']:.1f}s ({ins['duration_s']:.1f}s) → {path_display}  [{fonte}]")

    return sl


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gera shot_list.json com decisões de B-roll")
    p.add_argument("--scenes", required=True, help="scenes.json")
    p.add_argument("--transcript", required=True, help="transcript.json")
    p.add_argument("--inserts-manuais", required=True, dest="inserts_manuais", help="discovery.json ou lista de inserts")
    p.add_argument("--output", required=True, help="shot_list.json de saída")
    p.add_argument("--max-inserts", type=int, default=5)
    p.add_argument("--min-gap-s", type=float, default=4.0, dest="min_gap_s")
    p.add_argument("--insert-duration-s", type=float, default=3.0, dest="insert_duration_s")
    p.add_argument("--bank-root", default=None, dest="bank_root")
    p.add_argument("--config", default=None)
    args = p.parse_args(argv)

    scenes_path = Path(args.scenes)
    transcript_path = Path(args.transcript)
    inserts_manuais_path = Path(args.inserts_manuais)
    output_path = Path(args.output)

    for f, name in [(scenes_path, "--scenes"), (transcript_path, "--transcript"),
                    (inserts_manuais_path, "--inserts-manuais")]:
        if not f.exists():
            print(f"Erro: {name} não existe: {f}", file=sys.stderr)
            return 1

    bank_root = Path(args.bank_root) if args.bank_root else None
    config_path = Path(args.config) if args.config else None

    build_shot_list(
        scenes_path=scenes_path,
        transcript_path=transcript_path,
        inserts_manuais_path=inserts_manuais_path,
        output_path=output_path,
        max_inserts=args.max_inserts,
        min_gap_s=args.min_gap_s,
        insert_duration_s=args.insert_duration_s,
        bank_root=bank_root,
        config_path=config_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
