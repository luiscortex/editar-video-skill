# /editar-video — Estúdio de Gravação e Edição de Vídeo

Skill orquestradora do Claude Code (e do Codex CLI) pra **vídeo bruto**: talking head, gravação de tela ou qualquer gravação com fala. Cobre desde a tarefa simples — cortar silêncios/tomadas erradas e separar em clipes avulsos, **no formato e resolução originais**, sem recodificar — até o fluxo completo de 6 etapas + 5 gates: transcrição, inserts visuais e legenda queimada, terminando num Reel/Short 9:16 pronto pra postar. Você descreve o que quer no chat; a skill decide o quanto do pipeline usar.

**Spec completa:** ver `SKILL.md` nesta pasta.

## Quick start

Copie esta pasta pra `~/.claude/skills/editar-video/` e, dentro do Claude Code, rode:

```
/editar-video <pasta-com-o-video-bruto>
```

A skill é conversacional — pergunta o essencial (layout, estilo, tipo de conteúdo) em uma mensagem só e guia você pelos 5 gates.

## Pré-requisitos

- **macOS** (a detecção de fonte usa `/Library/Fonts` — em Linux funciona, mas cai no fallback padrão do Pillow)
- **Python 3.11+** (testado em 3.13/3.14)
- **ffmpeg + ffprobe** no PATH (`brew install ffmpeg`) — o motor de vídeo/áudio: corta, junta, sobrepõe imagem, queima legenda, mede duração e loudness. Tudo que toca o vídeo em si passa por ele.
- Dependências Python — `pip install -r requirements.txt`:
  - `faster-whisper` — transcreve a fala localmente (baixa o modelo na 1ª execução). Dá o texto pra revisão do Gate 1 e o timestamp de cada palavra, usado pra posicionar legenda e inserts.
  - `mediapipe` + `opencv-contrib-python` — detecta onde está o rosto no frame, pra legenda e cards nunca cobrirem a cara de quem está falando (`opencv` só faz a leitura de frame que o `mediapipe` precisa).
  - `playwright` — títulos, legenda estilizada e os inserts visuais (mock de tela, card de logo, comparação) são montados como página HTML+CSS; o Playwright abre essa página num Chromium invisível e tira um screenshot ou grava um clipe curto (insert animado). Depois do `pip install`, rode `playwright install chromium` — sem isso, nada disso renderiza.
  - `cairosvg` + `cairocffi` — converte logo em SVG (baixado da web) pra PNG, porque a composição final trabalha com imagem raster, não com vetor.
- Opcional: fonte **Plus Jakarta Sans** instalada em `~/Library/Fonts/` — sem ela, cai pra Helvetica Neue Bold automaticamente (aviso no log, não quebra)

## Variáveis de ambiente (opcionais)

| Variável | Pra quê | Default |
|---|---|---|
| `EDITAR_VIDEO_PYTHON` | Interpretador usado pra rodar os scripts filhos via subprocess | o mesmo `python3` que está rodando a skill |
| `EDITAR_VIDEO_DS_DIR` | Pasta `video-design-system/` (CSS de títulos/legenda + templates de UI mock) | `video-design-system/` dentro desta própria pasta (já vem no repo) |

Não precisa mexer em nenhuma das duas pra uso normal — só existem pra quem mantém uma fonte externa do design system ou um venv separado.

## Stack

Python + ffmpeg + Playwright + mediapipe, tudo local (sem API paga). O único custo é a primeira transcrição, que baixa o modelo do Whisper.

## Status

v2.2.2 — workflow v2.1/v2.2 (6 etapas + 5 gates), estilo Premium Cinematic default, layout de insert per-insert (v2.2). Ver `SKILL.md` → "📝 Versionamento" pro changelog completo.

## Nota sobre o `SKILL.md`

O `SKILL.md` deste repo foi generalizado a partir do spec interno do autor: nenhum caminho pessoal ou nome de máquina específico ficou nele. Algumas seções mencionam integrações *opcionais* com outras skills do autor (roteiro, banco de assets, aprendizado entre vídeos) — nenhuma delas vem neste repositório nem é necessária; sem elas, a skill usa os defaults documentados no próprio arquivo (estilo "04 — Premium Cinematic", legenda "A — Clean").

## Repositório

Este é um **mirror público**, gerado a partir do repositório de desenvolvimento (privado) do autor. Issues e PRs não são acompanhados ativamente — é uma cópia de distribuição, não um projeto aberto a contribuições no momento.
