"""Gera caption-instagram.md seguindo algoritmo-ig-2026.md.

Lê transcript.json + shot_list.json (opcional) + style_config.json (opcional)
e produz arquivo Markdown pronto pra copiar ao postar no IG.

Estrutura do output (algoritmo-ig-2026.md):
  - Hook (1ª linha, ≤120 chars)
  - Story (3-5 parágrafos do transcript, ≤200 palavras)
  - CTA tripla com o handle configurado (--handle, style_config.json ou EDITAR_VIDEO_HANDLE)
  - Palavra-código DM (brand mais mencionada OU substantivo principal)
  - 3-5 hashtags dinâmicas (#IA + nicho + intenção, nunca lista fixa)
  - Alt-text acessibilidade
  - Tópico Reels (Negócios default; Tecnologia se código/dev)
  - Horário sugerido

CLI:
  python gerar_caption_ig.py \\
    --transcript transcript.json \\
    --shot-list shot_list.json \\
    --output caption-instagram.md \\
    [--config style_config.json] \\
    [--estilo 04-premium-cinematic]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from textwrap import wrap


# ─── Constantes ───────────────────────────────────────────────────────────────

# Muletas pt-br: removidas da 1ª posição do hook
_MULETAS = {"aí", "ai", "tipo", "então", "entao", "olha", "bom", "então", "né", "quer dizer"}

# Brands que mapeiam pra hashtag específica (alias → hashtag sem #)
_BRAND_HASHTAG: dict[str, str] = {
    "claude": "ClaudeAI",
    "anthropic": "Anthropic",
    "chatgpt": "ChatGPT",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "meta": "MetaAI",
    "notion": "Notion",
    "zapier": "Zapier",
    "make": "MakeAutomation",
    "n8n": "n8n",
    "github": "GitHub",
}

# Palavras que indicam nicho de tecnologia/dev → tópico Tecnologia
_DEV_KEYWORDS = {"código", "codigo", "dev", "developer", "programação", "programacao",
                 "python", "javascript", "api", "github", "git", "deploy", "backend",
                 "frontend", "banco de dados", "database", "sql"}

# Palavras que indicam marketing/negócio → hashtag #MarketingDigital
_MARKETING_KEYWORDS = {"marketing", "gestor", "gestora", "tráfego", "trafego",
                        "anuncio", "anúncio", "ads", "campanha", "funil", "leads",
                        "conversão", "conversao", "agência", "agencia"}

# Stopwords pt-br — excluídas da detecção de palavra-código
_STOPWORDS = {
    "a", "o", "e", "de", "do", "da", "dos", "das", "que", "em", "para", "por",
    "com", "um", "uma", "os", "as", "se", "no", "na", "nos", "nas", "ao", "aos",
    "à", "às", "pra", "pro", "pela", "pelo", "mais", "mas", "ou", "isso", "esse",
    "essa", "esta", "este", "já", "ja", "é", "eu", "me", "te", "você", "voce",
    "ele", "ela", "eles", "elas", "seu", "sua", "seus", "suas", "meu", "minha",
    "quando", "como", "muito", "bem", "agora", "então", "entao", "pode", "não",
    "nao", "aqui", "ali", "lá", "la", "tudo", "quem", "qual", "ver", "fazer",
    "vou", "vai", "vem", "tem", "são", "sao", "ser", "isso", "esse", "assim",
    "só", "so", "também", "tambem", "ainda", "cada", "todo", "toda", "todos",
    "todas", "pela", "pelo", "entre", "até", "ate", "desde", "num", "numa",
    "disso", "desse", "desse", "nisso", "nesse", "nessa", "mesmo", "mesma",
    "depois", "antes", "precisa", "preciso",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _sanitize_hook_words(words: list[dict]) -> list[str]:
    """Extrai até 12 palavras das primeiras palavras do transcript.

    Remove muletas na 1ª posição. Capitaliza 1ª letra.
    """
    if not words:
        return []

    MAX_WORDS = 12
    selected = words[:MAX_WORDS]

    filtered = []
    for i, w in enumerate(selected):
        word_clean = w["word"].strip()
        if i == 0 and word_clean.lower() in _MULETAS:
            continue
        filtered.append(word_clean)

    if filtered:
        filtered[0] = filtered[0][0].upper() + filtered[0][1:]

    return filtered


def _build_hook(words: list[dict], full_text: str) -> str:
    """Gera a 1ª linha do post (hook) com ≤120 chars.

    Extrai as primeiras palavras do transcript, sanitiza muletas,
    limita a 120 chars. Termina com : ou ? ou . dependendo do conteúdo.
    """
    if not words and not full_text.strip():
        return "[adicionar hook]"

    if not words:
        # Fallback: pega até 120 chars do texto completo
        text = full_text.strip()
        if len(text) <= 120:
            return text
        # Corta na última palavra antes de 120 chars
        truncated = text[:117]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        return truncated + "..."

    hook_words = _sanitize_hook_words(words)
    if not hook_words:
        return "[adicionar hook]"

    hook = " ".join(hook_words)
    # Remove pontuação trailing e adiciona terminador
    hook = hook.rstrip(".,;:!?")

    # Limita a 120 chars
    if len(hook) > 117:
        truncated = hook[:117]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            hook = truncated[:last_space] + "..."
        else:
            hook = truncated + "..."
        return hook

    # Terminador: se contém "?" no final de alguma palavra mantém, senão ":"
    last_word = hook_words[-1] if hook_words else ""
    if last_word.endswith("?"):
        return hook
    return hook + ":"


def _build_story(words: list[dict], duration_s: float, full_text: str) -> str:
    """Gera o bloco story com ≤200 palavras dividido em 3-5 parágrafos.

    Pega palavras do transcript até 85% da duração (ou últimos 5s antes do fim).
    Divide em parágrafos de ~50 palavras.
    """
    if not words and not full_text.strip():
        return "[adicionar story]"

    if not words:
        # Fallback: usa full_text
        raw_words = full_text.split()
        max_words = min(200, len(raw_words))
        raw_words = raw_words[:max_words]
    else:
        # Pega palavras até 85% da duração
        cutoff = min(duration_s * 0.85, duration_s - 5.0) if duration_s > 10 else duration_s
        story_words = [w for w in words if w["start"] <= cutoff]
        if not story_words:
            story_words = words
        raw_words = [w["word"].strip() for w in story_words]
        raw_words = raw_words[:200]  # cap 200

    if not raw_words:
        return "[adicionar story]"

    # Divide em parágrafos de ~50 palavras
    text = " ".join(raw_words)
    paragraphs = wrap(text, width=250)  # wrap por chars

    # Agrupa em blocos de ~50 palavras
    all_words = text.split()
    chunks = []
    chunk_size = max(30, len(all_words) // 4)  # 3-5 parágrafos
    for i in range(0, len(all_words), chunk_size):
        chunk = " ".join(all_words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk.strip())

    # Capitaliza 1ª palavra de cada parágrafo
    formatted = []
    for chunk in chunks[:5]:  # max 5 parágrafos
        if chunk:
            chunk = chunk[0].upper() + chunk[1:]
            formatted.append(chunk)

    return "\n\n".join(formatted)


def _detect_brands_ordered(text_lower: str) -> list[str]:
    """Retorna brands em ordem decrescente de menções no texto."""
    counts: dict[str, int] = {}
    for alias in _BRAND_HASHTAG:
        pattern = rf"\b{re.escape(alias)}\b"
        found = len(re.findall(pattern, text_lower))
        if found > 0:
            counts[alias] = found
    return sorted(counts, key=lambda k: -counts[k])


def _detect_mascote(shot_list: dict) -> bool:
    """Retorna True se shot_list tem insert do tipo mascote."""
    inserts = shot_list.get("inserts", [])
    return any(ins.get("tipo") == "mascote" for ins in inserts)


def _detect_topico(text_lower: str) -> str:
    """Retorna tópico do Reel baseado em palavras-chave do transcript."""
    for kw in _DEV_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", text_lower):
            return "Tecnologia"
    return "Negócios e Empreendedorismo"


def _detect_marketing(text_lower: str) -> bool:
    """Retorna True se transcript menciona marketing/tráfego/agência."""
    for kw in _MARKETING_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", text_lower):
            return True
    return False


def _build_hashtags(text_lower: str, has_mascote: bool, brands_ordered: list[str]) -> list[str]:
    """Gera 3-5 hashtags seguindo algoritmo-ig-2026.md.

    Estrutura: 1 ampla (#IA) + nicho (#PromptEngineering + #Claude se mascote
    + #<Brand> se mencionada) + intenção (#MarketingDigital se marketing).
    Cap: 5 hashtags.
    """
    hashtags: list[str] = []

    # Sempre inclui #IA (ampla)
    hashtags.append("#IA")

    # Nicho base
    hashtags.append("#PromptEngineering")

    # #Claude se mascote presente
    if has_mascote and "#ClaudeAI" not in hashtags:
        hashtags.append("#ClaudeAI")

    # Brands mencionadas (max 1, a mais frequente que não seja "claude" já coberta)
    for brand_alias in brands_ordered:
        if len(hashtags) >= 5:
            break
        ht = "#" + _BRAND_HASHTAG[brand_alias]
        if ht not in hashtags:
            hashtags.append(ht)

    # Marketing se mencionado e ainda tem espaço
    if len(hashtags) < 5 and _detect_marketing(text_lower):
        hashtags.append("#MarketingDigital")

    # Garante máx 5
    return hashtags[:5]


def _extract_palavra_codigo(text_lower: str, brands_ordered: list[str]) -> str:
    """Extrai a palavra-código pro CTA DM.

    Lógica:
    1. Brand mais mencionada (alias lowercase) se houver
    2. Primeiro substantivo significativo (>4 letras, não stopword)
    3. Fallback: "manda"
    """
    # Brand mais mencionada
    if brands_ordered:
        return brands_ordered[0].upper()

    # Primeiro substantivo significativo
    words_clean = re.findall(r"\b[a-záéíóúàâêôãõüçñ]{5,}\b", text_lower)
    for w in words_clean:
        if w not in _STOPWORDS:
            return w.upper()

    return "MANDA"


def _build_alt_text(hook: str, handle: str) -> str:
    """Gera alt-text descritivo pra acessibilidade."""
    # Remove terminadores e sufixo '...'
    hook_clean = hook.rstrip(":?.").rstrip(".")
    hook_clean = hook_clean.replace("...", "").strip()
    return f'"{hook_clean} — {handle} explicando no Reels"'


def _slug_from_hook(hook: str) -> str:
    """Gera slug curto do vídeo baseado no hook."""
    slug = hook.lower()
    slug = re.sub(r"[^a-z0-9\s]", "", slug)
    words = slug.split()[:5]
    return "-".join(words) if words else "reel"


def gerar_caption(
    transcript: dict,
    shot_list: dict | None,
    style_config: dict | None,
    estilo: str = "04-premium-cinematic",
    handle: str | None = None,
) -> str:
    """Gera o conteúdo do caption-instagram.md.

    Retorna string Markdown pronta pra salvar.
    """
    # Extrai campos do transcript
    full_text = transcript.get("text", "").strip()
    words = transcript.get("words", [])
    duration_s = float(transcript.get("duration_s", 0.0))

    # Texto completo em lowercase pra detecções
    # Combina full_text + words pra garantir detecção mesmo quando full_text é placeholder/curto
    words_text = " ".join(w.get("word", "") for w in words)
    combined = f"{full_text} {words_text}".strip()
    text_lower = combined.lower()

    # Handle do Instagram pra CTA/alt-text: --handle > style_config.json > env var > placeholder genérico
    if handle is None:
        handle = (style_config or {}).get("handle") or os.environ.get("EDITAR_VIDEO_HANDLE") or "@seu_instagram"

    # Shot list (opcional)
    sl = shot_list or {}
    has_mascote = _detect_mascote(sl)

    # Detecta elementos
    brands_ordered = _detect_brands_ordered(text_lower)
    topico = _detect_topico(text_lower)
    hashtags = _build_hashtags(text_lower, has_mascote, brands_ordered)
    palavra_codigo = _extract_palavra_codigo(text_lower, brands_ordered)

    # Constrói seções
    hook = _build_hook(words, full_text)
    story = _build_story(words, duration_s, full_text)
    alt_text = _build_alt_text(hook, handle)
    slug = _slug_from_hook(hook)

    # Horário sugerido (regra do algoritmo-ig-2026.md)
    horario = "Postar entre 18h-21h (terça/quarta/quinta — maior alcance pra Reels BR)"

    # Monta hashtags string
    hashtags_str = " ".join(hashtags)

    # ─── Template output ──────────────────────────────────────────────────────
    md = f"""# Caption Instagram — {slug}

> Gerado por /editar-video v1.1. Estrutura segue algoritmo-ig-2026.md.

## 📝 Caption (copia tudo abaixo até "---")

{hook}

{story}

—

Salva esse pra não esquecer 💾
Segue {handle} pra mais
Comenta "{palavra_codigo}" que eu te mando o prompt no DM 👇

{hashtags_str}

---

## 🏷️ Metadados (cola nos campos específicos do IG)

**Tópico do Reel:** {topico}
**Alt-text (acessibilidade):** {alt_text}
**Horário sugerido:** {horario}

## 🔄 Refinar com IA

Se quiser refinar o copy, cola no chat:
> /gerar-conteudo refinar caption do reel — texto base abaixo:

```
{hook}

{story}
```
"""
    return md.strip()


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gera caption-instagram.md")
    p.add_argument("--transcript", required=True, help="transcript.json")
    p.add_argument("--shot-list", dest="shot_list", default=None, help="shot_list.json (opcional)")
    p.add_argument("--output", required=True, help="Caminho do caption-instagram.md")
    p.add_argument("--config", default=None, help="style_config.json (opcional)")
    p.add_argument("--estilo", default="04-premium-cinematic", help="Estilo fallback")
    p.add_argument("--handle", default=None, help="Handle do Instagram pra CTA/alt-text (ex: @seuperfil)")
    args = p.parse_args(argv)

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"Erro: transcript não existe: {transcript_path}", file=sys.stderr)
        return 1

    transcript = json.loads(transcript_path.read_text())

    shot_list = None
    if args.shot_list:
        sl_path = Path(args.shot_list)
        if sl_path.exists():
            shot_list = json.loads(sl_path.read_text())
        else:
            print(f"  [warning] shot_list não encontrado: {sl_path} — seguindo sem", file=sys.stderr)

    style_config = None
    if args.config:
        cfg_path = Path(args.config)
        if cfg_path.exists():
            style_config = json.loads(cfg_path.read_text())

    estilo = args.estilo
    if style_config and style_config.get("estilo"):
        estilo = style_config["estilo"]

    content = gerar_caption(
        transcript=transcript,
        shot_list=shot_list,
        style_config=style_config,
        estilo=estilo,
        handle=args.handle,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    print(f"OK: caption gerado em {output_path}", file=sys.stderr)
    print(f"  hook: {content.splitlines()[4] if len(content.splitlines()) > 4 else '?'}", file=sys.stderr)
    print(f"  topico: {'Tecnologia' if 'Tecnologia' in content else 'Negócios e Empreendedorismo'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
