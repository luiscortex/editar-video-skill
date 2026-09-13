# /editar-video — Estúdio de Gravação e Edição de Vídeo @luiscortex

Skill orquestradora do Claude Code que transforma **vídeo bruto** (talking head, gravação de tela etc.) em **Reel/Short 9:16 editado**, com workflow conversacional em 6 etapas + 5 gates de aprovação.

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
- **ffmpeg + ffprobe** no PATH (`brew install ffmpeg`)
- Dependências Python — `pip install -r requirements.txt`:
  - `faster-whisper` (transcrição local, baixa modelo na 1ª execução)
  - `mediapipe` + `opencv-contrib-python` (detecção de rosto)
  - `playwright` (renderiza mocks/títulos/legenda via HTML+CSS) — depois do pip, rode `playwright install chromium`
  - `cairosvg` + `cairocffi` (conversão de logos SVG → PNG)
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

O `SKILL.md` é o mesmo spec usado internamente pelo autor — por isso ele cita alguns caminhos pessoais (`~/Documents/luiscortex/copy-styles/...`, `~/Documents/luiscortex/edicoes/...`) que **não vêm neste repo**. São arquivos de estilo de conteúdo (tom de voz, presets de copy) e pastas de output do autor — opcionais, a skill funciona com os defaults (estilo "04 — Premium Cinematic", legenda "A — Clean") mesmo sem eles. Nada disso afeta a instalação nem o funcionamento técnico.

## Repositório

Este é um **mirror público**, gerado a partir do repositório de desenvolvimento (privado) do autor. Issues e PRs não são acompanhados ativamente — é uma cópia de distribuição, não um projeto aberto a contribuições no momento.
