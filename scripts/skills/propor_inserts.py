"""Propõe plano de inserts em 4 camadas a partir de transcript + roteiro.

Output: plano-inserts.json + plano-inserts.html (cards visuais).

Camadas:
1. Manuais (inserts_manuais.json se existir, override)
2. Brand mentions (Claude, Cursor, Anthropic, etc — UI mock)
3. Photo keywords (palavras como "tela", "código" → photo)
4. Mascote (sem timing fixo — sugestões em momentos de ênfase)

Hook automático (0-3.5s, tipo TT1) e CTA automático (últimos 4s, tipo TT2)
sempre são propostos.
"""
import argparse
import json
import os
import subprocess
from pathlib import Path


# Inclui variantes que o Whisper costuma errar em PT (ex: "Clod" pra Claude)
BRAND_MAP = {
    "claude": ("ui-mock", "claude-ai-greeting", "split-5050-head-bottom"),
    "clod": ("ui-mock", "claude-ai-greeting", "split-5050-head-bottom"),  # Whisper PT erra Claude → Clod
    "anthropic": ("ui-mock", "claude-ai-greeting", "split-5050-head-bottom"),
    "cursor": ("ui-mock", "cursor-ide", "split-5050-head-bottom"),
    "notion": ("ui-mock", "notion-page", "fullframe"),
    "finder": ("ui-mock", "macos-finder", "fullframe"),
    "premiere": ("ui-mock", "nle-timeline", "fullframe"),
    "davinci": ("ui-mock", "nle-timeline", "fullframe"),
    "chatgpt": ("ui-mock", "chatgpt-prompt", "split-5050-head-bottom"),
    "gpt": ("ui-mock", "chatgpt-prompt", "split-5050-head-bottom"),
}

PHOTO_KEYWORDS = {"tela", "código", "computador", "edicao"}


def _norm_palavra(w: str) -> str:
    """Normaliza palavra do Whisper: tira espaço e pontuação, lowercase."""
    return w.strip().lower().strip(".,!?;:")

ORIGEM_COLOR = {
    "hook-auto": "#ff7a00",
    "cta-auto": "#ff7a00",
    "manual": "#19c2c2",
    "brand-mention": "#7fd17f",
    "photo-keyword": "#c08fff",
    "mascote": "#ffd34d",
}


def _detectar_brand_mentions(words: list[dict]) -> list[dict]:
    """Detecta brand mentions no transcript. Dedup: 1 insert por brand × janela 8s."""
    inserts = []
    ultima_aparicao: dict[str, float] = {}
    for w in words:
        palavra = _norm_palavra(w["word"])
        if palavra not in BRAND_MAP:
            continue
        # Dedup: se mesma brand apareceu nos últimos 8s, pula
        if palavra in ultima_aparicao and (w["start"] - ultima_aparicao[palavra]) < 8.0:
            continue
        ultima_aparicao[palavra] = w["start"]
        tipo, subtipo, layout = BRAND_MAP[palavra]
        inserts.append({
            "tipo": tipo,
            "subtipo": subtipo,
            "at_s": round(w["start"], 2),
            "duration_s": 4.0,
            "layout": layout,
            "fonte_tecnica": "html-playwright",
            "template": f"ui-mocks/{subtipo}/template.html",
            "conteudo": {"label": palavra.title(), "termo_no_audio": w["word"].strip()},
            "asset_path": None,
            "status": "proposto",
            "origem": "brand-mention",
        })
    return inserts


def _extrair_texto_hook(words: list[dict], duration_s: float = 3.5) -> str:
    """Pega as palavras até `duration_s` segundos pra usar como hook real."""
    if not words:
        return "[hook do vídeo — adicionar manual]"
    partes = [w["word"].strip() for w in words if w["start"] < duration_s]
    if not partes:
        return "[hook do vídeo — adicionar manual]"
    texto = " ".join(partes).strip()
    # Limpa pontuação final e capitaliza primeira letra
    texto = texto.rstrip(",.;:")
    return texto


def _propor_hook(duration_s: float, words: list[dict] | None = None) -> dict:
    return {
        "tipo": "titulo",
        "subtipo": "TT1",
        "at_s": 0.0,
        "duration_s": 3.5,
        "layout": "fullframe-overlay",
        "texto": _extrair_texto_hook(words or [], duration_s=3.5),
        "fonte_tecnica": "animar_titulo",
        "asset_path": None,
        "status": "proposto",
        "origem": "hook-auto",
    }


def _propor_cta(duration_s: float, words: list[dict] | None = None) -> dict:
    texto = "COMENTA VIDEO"
    if words:
        # Procura "comenta X" no transcript (normaliza palavras)
        joined = " ".join(_norm_palavra(w["word"]) for w in words)
        idx = joined.find("comenta ")
        if idx >= 0:
            resto = joined[idx + len("comenta "):].split()
            if resto:
                texto = f"COMENTA {resto[0].upper()}"
    return {
        "tipo": "cta",
        "subtipo": "TT2",
        "at_s": max(0.0, round(duration_s - 4.0, 2)),
        "duration_s": min(4.0, duration_s),
        "layout": "fullframe-overlay",
        "texto": texto,
        "fonte_tecnica": "animar_titulo",
        "asset_path": None,
        "status": "proposto",
        "origem": "cta-auto",
    }


def _ordenar_e_resolver_overlap(inserts: list[dict]) -> list[dict]:
    inserts = sorted(inserts, key=lambda i: i["at_s"])
    # IDs sequenciais
    for idx, ins in enumerate(inserts, start=1):
        ins["id"] = idx
    return inserts


def _detectar_passthroughs(inserts: list[dict], video_duration: float) -> list[dict]:
    ocupados = sorted(
        [(i["at_s"], i["at_s"] + i["duration_s"]) for i in inserts]
    )
    passthroughs = []
    cursor = 0.0
    for start, end in ocupados:
        if start > cursor + 0.05:
            passthroughs.append({
                "at_s": round(cursor, 2),
                "end_s": round(start, 2),
                "descricao": "vídeo original",
            })
        cursor = max(cursor, end)
    if cursor < video_duration - 0.05:
        passthroughs.append({
            "at_s": round(cursor, 2),
            "end_s": round(video_duration, 2),
            "descricao": "vídeo original",
        })
    return passthroughs


def _aplicar_manuais(inserts: list[dict], manuais_path: Path, layout_default: str = "overlay-fullframe") -> list[dict]:
    manuais = json.loads(manuais_path.read_text())
    manuais_inserts = []
    for m in manuais:
        manuais_inserts.append({
            "tipo": m.get("tipo", "ui-mock"),
            "subtipo": m.get("subtipo", m.get("termo", "manual")),
            "at_s": m["at_s"],
            "duration_s": m.get("duration_s", 4.0),
            "layout": m.get("layout", layout_default),
            "conteudo": m,
            "asset_path": m.get("asset_path"),
            "fonte_tecnica": m.get("fonte_tecnica", "html-playwright"),
            "status": "proposto",
            "origem": "manual",
        })
    # Remove automáticos que colidem (mesmo at_s), exceto hook/cta autos
    ats_manuais = {m["at_s"] for m in manuais_inserts}
    inserts = [
        i for i in inserts
        if i["at_s"] not in ats_manuais or i.get("origem") in {"hook-auto", "cta-auto"}
    ]
    return inserts + manuais_inserts


def _extrair_frames_originais(video_path: Path, timestamps_s: list[float], out_dir: Path) -> dict[float, Path]:
    """Pra cada timestamp, extrai 1 frame PNG do vídeo original.

    Retorna dict {timestamp: path_relativo_pra_html}.
    Falha silenciosa: se ffmpeg quebrar, skipa esse frame (placeholder no HTML).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    resultado: dict[float, Path] = {}
    for ts in timestamps_s:
        nome = f"t{int(round(ts))}s.png"
        path = out_dir / nome
        if path.exists():
            resultado[ts] = path
            continue
        try:
            subprocess.check_call([
                "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
                "-frames:v", "1", "-q:v", "3",
                "-vf", "scale=360:-1",  # thumb leve
                str(path)
            ], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            if path.exists():
                resultado[ts] = path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return resultado


def _classificar_card_status(ins: dict) -> tuple[str, str]:
    """Retorna (classe_css, status_label) — 'pronto' verde pra inserts, etc."""
    # Inserts são sempre 'pronto' nesse stage (Gate 1 — plano proposto, não renderizado)
    return "pronto", "✓ Proposto"


def _mock_placeholder_svg(subtipo: str, layout: str) -> str:
    """Gera um placeholder SVG colorido inline pra slot do mock (antes do render real)."""
    cor_map = {
        "TT1": "#DA7756", "TT2": "#DA7756", "TT3": "#DA7756", "TT4": "#DA7756",
    }
    cor = cor_map.get(subtipo, "#1F3556")
    label = subtipo.replace("-", " ")
    return (
        f'<div style="background:{cor};width:100%;height:100%;display:flex;'
        f'flex-direction:column;align-items:center;justify-content:center;'
        f'padding:8px;text-align:center;font-family:JetBrains Mono,monospace;">'
        f'<div style="font-size:9px;color:rgba(255,255,255,0.6);text-transform:uppercase;letter-spacing:1px;">mock</div>'
        f'<div style="font-size:11px;font-weight:700;color:white;margin-top:4px;line-height:1.3;">{label}</div>'
        f'<div style="font-size:8px;color:rgba(255,255,255,0.5);margin-top:6px;">{layout}</div>'
        f'</div>'
    )


def _formatar_conteudo(ins: dict) -> str:
    """Formata o conteúdo de cada insert de forma legível no card HTML."""
    if ins.get("texto"):
        return ins["texto"]
    conteudo = ins.get("conteudo", {})
    if isinstance(conteudo, dict):
        # Brand mention: mostra label + termo capturado
        if "label" in conteudo:
            label = conteudo["label"]
            termo = conteudo.get("termo_no_audio", "")
            if termo and termo.lower() != label.lower():
                return f'<strong>{label}</strong> <span style="opacity:0.6">(falou "{termo}")</span>'
            return f"<strong>{label}</strong>"
        # Manual com texto explícito
        if "texto" in conteudo:
            return conteudo["texto"]
        # Fallback: lista chave: valor legível
        partes = [f"{k}: {v}" for k, v in conteudo.items() if k not in {"at_s","duration_s","tipo","layout"}]
        return " · ".join(partes) if partes else "(sem detalhe)"
    return str(conteudo)


def _frame_path_relativo(at_s: float, frames_existentes: dict) -> str | None:
    """Retorna 'originals/tNs.png' se o frame foi extraído, senão None."""
    if at_s in frames_existentes:
        return f"originals/{frames_existentes[at_s].name}"
    # Tenta arredondamento (extração usa int(round(ts)))
    for ts_key, path in frames_existentes.items():
        if abs(ts_key - at_s) < 0.5:
            return f"originals/{path.name}"
    return None


def _render_card_insert(ins: dict, frames: dict) -> str:
    classe, status_label = _classificar_card_status(ins)
    layout = ins.get("layout", "fullframe")
    titulo = ins.get("conteudo", {}).get("label") if isinstance(ins.get("conteudo"), dict) else None
    titulo = titulo or ins.get("texto") or f"{ins.get('tipo','').title()} {ins.get('subtipo','')}"
    if len(titulo) > 60:
        titulo = titulo[:58] + "…"

    # Preview esquerdo: mock placeholder
    mock_html = _mock_placeholder_svg(ins.get("subtipo", ""), layout)
    # Preview direito: frame original do vídeo
    frame_rel = _frame_path_relativo(ins["at_s"], frames)
    if frame_rel:
        original_html = f'<img src="{frame_rel}" alt="original t={ins["at_s"]:.1f}s">'
    else:
        original_html = '<div style="background:#0A1628;width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#8B9BB4;font-size:10px;font-family:JetBrains Mono,monospace;">sem preview</div>'

    # Corpo descritivo
    body_parts = []
    if ins.get("texto") and ins.get("subtipo", "").startswith("TT"):
        body_parts.append(f'<strong>Texto:</strong> "{ins["texto"]}"')
    body_parts.append(f'<strong>Layout:</strong> <span class="orange">{layout}</span>')
    fonte = ins.get("fonte_tecnica", "html-playwright")
    body_parts.append(f'<strong>Fonte:</strong> <span class="tech">{fonte}</span>')
    origem = ins.get("origem", "manual")
    body_parts.append(f'<strong>Origem:</strong> {origem}')
    if isinstance(ins.get("conteudo"), dict):
        c = ins["conteudo"]
        if c.get("termo_no_audio"):
            body_parts.append(f'<strong>Disparou em:</strong> "<em>{c["termo_no_audio"]}</em>"')
        if c.get("raciocinio"):
            body_parts.append(f'<strong>Raciocínio:</strong> {c["raciocinio"]}')
    body_html = "<br>".join(body_parts)

    end_s = ins["at_s"] + ins["duration_s"]
    return f"""
  <div class="card {classe}">
    <div class="card-head">
      <div>
        <div class="card-num">INSERT {ins["id"]:02d}</div>
        <div class="card-title">{titulo}</div>
        <div class="card-time">{ins["at_s"]:.1f}s — {end_s:.1f}s</div>
      </div>
      <div class="card-status {classe}">{status_label}</div>
    </div>
    <div class="preview">
      <div class="preview-cell">{mock_html}<div class="preview-label">mock</div></div>
      <div class="preview-cell">{original_html}<div class="preview-label">original t={ins["at_s"]:.1f}s</div></div>
    </div>
    <div class="card-body">{body_html}</div>
  </div>"""


def _render_card_passthrough(p: dict, idx: int, frames: dict) -> str:
    at_s = p["at_s"]
    end_s = p["end_s"]
    meio = (at_s + end_s) / 2
    frame_rel = _frame_path_relativo(meio, frames) or _frame_path_relativo(at_s, frames)
    if frame_rel:
        img_html = f'<img src="{frame_rel}" alt="trecho">'
    else:
        img_html = '<div style="background:#0A1628;width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#8B9BB4;font-size:10px;font-family:JetBrains Mono,monospace;">sem preview</div>'
    return f"""
  <div class="card trecho">
    <div class="card-head">
      <div>
        <div class="card-num">TRECHO {idx:02d}</div>
        <div class="card-title">Vídeo original passando</div>
        <div class="card-time">{at_s:.1f}s — {end_s:.1f}s</div>
      </div>
      <div class="card-status trecho">▶ Vídeo</div>
    </div>
    <div class="preview">
      <div class="preview-cell">{img_html}<div class="preview-label">video.mp4 t={meio:.1f}s</div></div>
      <div class="preview-cell">{img_html}<div class="preview-label">mesmo</div></div>
    </div>
    <div class="card-body">
      <strong>Solução:</strong> trecho do <span class="tech">video.mp4</span> original passando t={at_s:.1f}-{end_s:.1f}s<br>
      <strong>Duração:</strong> {end_s - at_s:.1f}s sem insert (talking head fluindo)
    </div>
  </div>"""


def _gerar_html(
    inserts: list[dict],
    passthroughs: list[dict],
    video_duration: float,
    slug: str | None = None,
    style_config: dict | None = None,
    frames_originais: dict | None = None,
) -> str:
    frames = frames_originais or {}
    style_config = style_config or {}
    split = style_config.get("split", "60/40")
    estilo = style_config.get("estilo", "—")
    tipo_conteudo = style_config.get("tipo_conteudo", "—")

    insert_cards = "\n".join(_render_card_insert(ins, frames) for ins in inserts)
    trecho_cards = "\n".join(
        _render_card_passthrough(p, idx, frames)
        for idx, p in enumerate(passthroughs, start=1)
    )

    subtitulo = f"{len(inserts)} inserts propostos · {len(passthroughs)} trechos do original · split {split} · {tipo_conteudo}"
    tag_label = f"ETAPA 2 · GATE 1{(' · ' + slug) if slug else ''}"

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Plano de Inserts{(' — ' + slug) if slug else ''}</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --navy: #0F1F3A;
  --navy-deep: #0A1628;
  --orange: #DA7756;
  --cyan: #5DE8FF;
  --green: #5ee8a3;
  --red: #ff6b6b;
  --yellow: #f5cb5c;
  --white: #F8FAFC;
  --muted: #8B9BB4;
  --line: #1F3556;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--navy-deep);
  color: var(--white);
  font-family: 'Plus Jakarta Sans', sans-serif;
  padding: 40px 32px;
  line-height: 1.5;
}}
.wrap {{ max-width: 1400px; margin: 0 auto; }}
header {{ border-bottom: 1px solid var(--line); padding-bottom: 24px; margin-bottom: 32px; }}
.tag {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--orange);
  letter-spacing: 2px;
  text-transform: uppercase;
}}
h1 {{ font-size: 42px; font-weight: 800; letter-spacing: -1px; margin-top: 6px; }}
h1 em {{ font-family: 'Instrument Serif', serif; font-style: italic; font-weight: 400; color: var(--orange); }}
.subtitle {{ color: var(--muted); font-size: 16px; margin-top: 8px; }}
.config {{
  background: var(--navy); border: 1px solid var(--orange);
  border-radius: 12px; padding: 20px 24px; margin: 24px 0 32px;
}}
.config-title {{
  font-family: 'JetBrains Mono', monospace; font-size: 11px;
  color: var(--orange); letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 8px;
}}
.config-q {{ font-size: 18px; font-weight: 700; margin-bottom: 6px; }}
.config-a {{ color: var(--muted); font-size: 14px; }}
.config-a strong {{ color: var(--cyan); }}
.legend {{ display: flex; gap: 24px; margin: 24px 0 32px; flex-wrap: wrap; }}
.legend-item {{ display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); }}
.dot {{ width: 12px; height: 12px; border-radius: 50%; }}
.dot.pronto {{ background: var(--green); }}
.dot.trecho {{ background: var(--cyan); }}
.section-head {{
  font-family: 'JetBrains Mono', monospace; font-size: 11px;
  color: var(--muted); letter-spacing: 2px; text-transform: uppercase;
  margin: 24px 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--line);
}}
.cards {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(440px, 1fr));
  gap: 20px;
}}
.card {{
  background: var(--navy); border: 1px solid var(--line);
  border-radius: 12px; padding: 20px; position: relative;
}}
.card.pronto {{ border-left: 4px solid var(--green); }}
.card.trecho {{ border-left: 4px solid var(--cyan); }}
.card-head {{ display: flex; justify-content: space-between; align-items: start; margin-bottom: 12px; }}
.card-num {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--muted); margin-bottom: 4px; }}
.card-title {{ font-size: 19px; font-weight: 700; line-height: 1.2; }}
.card-time {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--orange); margin-top: 4px; }}
.card-status {{
  font-family: 'JetBrains Mono', monospace; font-size: 10px;
  text-transform: uppercase; letter-spacing: 1px;
  padding: 4px 10px; border-radius: 4px; white-space: nowrap; height: fit-content;
}}
.card-status.pronto {{ background: rgba(94,232,163,0.15); color: var(--green); }}
.card-status.trecho {{ background: rgba(93,232,255,0.15); color: var(--cyan); }}
.preview {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 14px 0; }}
.preview-cell {{
  background: #000; border-radius: 6px; overflow: hidden;
  aspect-ratio: 9/16; position: relative;
}}
.preview-cell img {{ width: 100%; height: 100%; object-fit: cover; }}
.preview-label {{
  position: absolute; bottom: 4px; left: 4px;
  font-family: 'JetBrains Mono', monospace; font-size: 9px;
  background: rgba(0,0,0,0.7); color: white; padding: 2px 6px; border-radius: 3px;
  text-transform: uppercase; letter-spacing: 0.5px;
}}
.card-body {{ font-size: 13px; color: var(--muted); line-height: 1.6; margin-top: 12px; }}
.card-body strong {{ color: var(--white); font-weight: 600; }}
.card-body em {{ font-family: 'Instrument Serif', serif; font-style: italic; color: var(--white); }}
.card-body .tech {{
  font-family: 'JetBrains Mono', monospace; font-size: 11px;
  color: var(--cyan); background: rgba(93,232,255,0.08);
  padding: 1px 6px; border-radius: 3px;
}}
.card-body .orange {{
  font-family: 'JetBrains Mono', monospace; font-size: 11px;
  color: var(--orange); background: rgba(218,119,86,0.12);
  padding: 1px 6px; border-radius: 3px; font-weight: 600;
}}
footer {{ margin-top: 48px; padding-top: 32px; border-top: 1px solid var(--line); }}
footer h2 {{ font-size: 24px; font-weight: 700; margin-bottom: 16px; }}
footer h2 em {{ font-family: 'Instrument Serif', serif; color: var(--orange); font-style: italic; font-weight: 400; }}
.actions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
.action {{ background: var(--navy); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
.action-key {{
  font-family: 'JetBrains Mono', monospace; font-size: 14px; font-weight: 700;
  color: var(--orange); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px;
}}
.action-text {{ font-size: 13px; color: var(--muted); line-height: 1.5; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="tag">{tag_label}</div>
  <h1>Plano de <em>Inserts</em></h1>
  <div class="subtitle">{subtitulo}</div>

  <div class="config">
    <div class="config-title">⚙ Configuração inicial</div>
    <div class="config-q">Split: {split} · Estilo: {estilo} · Tipo: {tipo_conteudo}</div>
    <div class="config-a">Duração total <strong>{video_duration:.1f}s</strong>. Pode mudar essas escolhas via <strong>--mode desfaz</strong> e re-rodar a etapa inicial.</div>
  </div>

  <div class="legend">
    <div class="legend-item"><span class="dot pronto"></span>Insert proposto (mock placeholder por enquanto · render real na Etapa 3)</div>
    <div class="legend-item"><span class="dot trecho"></span>Trecho do vídeo original passando (não precisa render)</div>
  </div>
</header>

<div class="section-head">Inserts ({len(inserts)})</div>
<div class="cards">
{insert_cards}
</div>

<div class="section-head">Trechos do original ({len(passthroughs)})</div>
<div class="cards">
{trecho_cards}
</div>

<footer>
  <h2>🛑 Gate 1 — <em>Aprovação</em> do Plano</h2>
  <div class="actions">
    <div class="action">
      <div class="action-key">a) Aprovado</div>
      <div class="action-text">Segue pra Etapa 3 — aplico os inserts no vídeo master e mando você aprovar o resultado.</div>
    </div>
    <div class="action">
      <div class="action-key">b) Ajusta</div>
      <div class="action-text">Algum insert pra mudar (me fala qual número e o que ajustar — timing, conteúdo, layout).</div>
    </div>
    <div class="action">
      <div class="action-key">c) Tira N</div>
      <div class="action-text">Remove o insert N (ex: "tira insert 4").</div>
    </div>
    <div class="action">
      <div class="action-key">d) Desfaz</div>
      <div class="action-text">Volta pra etapa anterior pra mudar split, estilo ou tipo de conteúdo.</div>
    </div>
  </div>
</footer>

</div>
</body>
</html>"""


def propor(
    transcript_path: Path,
    style_config_path: Path,
    video_duration: float,
    roteiro: Path | None = None,
    manuais: Path | None = None,
    slug: str | None = None,
) -> dict:
    transcript = json.loads(transcript_path.read_text())
    # Adapta schema: transcrever.py usa flat {"words":[]} (faster-whisper),
    # mas se vier segmented {"segments":[{"words":[]}]}, junta tudo.
    if "words" in transcript:
        words = transcript["words"]
    elif "segments" in transcript:
        words = []
        for seg in transcript["segments"]:
            words.extend(seg.get("words", []))
    else:
        words = []
    inserts = [_propor_hook(video_duration, words)]
    inserts.extend(_detectar_brand_mentions(words))
    inserts.append(_propor_cta(video_duration, words))
    if manuais and Path(manuais).exists():
        style_cfg = json.loads(style_config_path.read_text()) if Path(style_config_path).exists() else {}
        layout_default = style_cfg.get("layout_default", "overlay-fullframe")
        inserts = _aplicar_manuais(inserts, Path(manuais), layout_default=layout_default)
    inserts = _ordenar_e_resolver_overlap(inserts)
    passthroughs = _detectar_passthroughs(inserts, video_duration)
    return {
        "version": "2.0.0",
        "slug": slug,
        "video_duration_s": video_duration,
        "inserts": inserts,
        "passthroughs": passthroughs,
    }


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="propor_inserts")
    p.add_argument("--transcript", required=True)
    p.add_argument("--style-config", required=True)
    p.add_argument("--video-duration", required=True, type=float)
    p.add_argument("--video", default=None, help="Path do vídeo (cortado.mp4) pra extrair frames originais nos cards")
    p.add_argument("--roteiro", default=None)
    p.add_argument("--manuais", default=None)
    p.add_argument("--slug", default=None)
    p.add_argument("--output", required=True)
    p.add_argument("--output-html", required=True)
    args = p.parse_args(argv)

    plano = propor(
        Path(args.transcript),
        Path(args.style_config),
        args.video_duration,
        Path(args.roteiro) if args.roteiro else None,
        Path(args.manuais) if args.manuais else None,
        slug=args.slug,
    )
    _atomic_write(Path(args.output), json.dumps(plano, indent=2, ensure_ascii=False))

    # Extrai frames originais pra cada timestamp (inserts + meio de cada passthrough)
    frames_dict: dict[float, Path] = {}
    if args.video and Path(args.video).exists():
        out_dir = Path(args.output_html).parent / "originals"
        timestamps = [ins["at_s"] for ins in plano["inserts"]]
        timestamps += [(p["at_s"] + p["end_s"]) / 2 for p in plano["passthroughs"]]
        timestamps += [p["at_s"] for p in plano["passthroughs"]]
        frames_dict = _extrair_frames_originais(Path(args.video), sorted(set(timestamps)), out_dir)

    style_config = json.loads(Path(args.style_config).read_text()) if Path(args.style_config).exists() else {}

    html = _gerar_html(
        plano["inserts"],
        plano["passthroughs"],
        plano["video_duration_s"],
        slug=args.slug,
        style_config=style_config,
        frames_originais=frames_dict,
    )
    _atomic_write(Path(args.output_html), html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
