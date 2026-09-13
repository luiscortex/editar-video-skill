"""Cadeia de prioridade de assets — v2 (6 níveis).

Cadeia em ordem de prioridade:
  1. inserts_manuais (mesma cena)
  2. inserts_manuais (livre, match descricao)
  3. banco_local (via AssetBank.search)
  4. fotos_user  (~/…/avatar-photos/ — match keyword pt-br)
  5. logos_simpleicons (CDN gratuita, sem API key, cache local)
  6. needs_user_input (fallback)
"""
import re
import sys
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

from scripts.asset_bank import AssetBank

# ─── Constantes ───────────────────────────────────────────────────────────────

PHOTOS_BASE_DIR = Path.home() / ".editar-video/avatar-photos"

LOGOS_CACHE_DIR = Path.home() / "Library/Caches/editar-video/logos"

# Mapeamento keyword (pt-br) → photo_id
PHOTO_KEYWORDS: list[tuple[list[str], str]] = [
    (["sorrindo", "feliz", "alegre", "boas-vindas", "intro", "abertura"], "01-frontal-sorrindo"),
    (["apontando", "olha", "veja", "aqui", "cta", "fim", "final", "encerramento"], "02-apontando"),
    (["pensando", "duvida", "hmm", "considere"], "03-pensando"),
    (["empolgado", "incrivel", "uau", "wow", "topisimo"], "04-empolgado"),
    (["serio", "sério", "atenção", "cuidado", "importante"], "05-serio"),
    (["lateral", "perfil"], "06-lateral"),
    (["bro-roll", "anti-pose", "casual"], "07-bro-roll"),
]

# Keywords que forçam xlarge (CTA slide)
CTA_KEYWORDS = {"cta", "fim", "final", "encerramento"}

BRAND_ALIASES: dict[str, str] = {
    "claude": "claude",
    "anthropic": "anthropic",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
    "gemini": "google",
    "google": "google",
    "youtube": "youtube",
    "yt": "youtube",
    "instagram": "instagram",
    "insta": "instagram",
    "ig": "instagram",
    "tiktok": "tiktok",
    "linkedin": "linkedin",
    "github": "github",
    "vscode": "vscode",
    "cursor": "cursor",
    "warp": "warp",
    "stripe": "stripe",
    "notion": "notion",
    "figma": "figma",
    "slack": "slack",
    "whatsapp": "whatsapp",
    "shopify": "shopify",
    "perplexity": "perplexity",
    "midjourney": "midjourney",
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "react": "react",
    "nextjs": "nextdotjs",
    "tailwind": "tailwindcss",
    "vercel": "vercel",
    "netlify": "netlify",
    "chrome": "googlechrome",
    "firefox": "firefox",
    "safari": "safari",
    "brave": "brave",
}

SIMPLEICONS_CDN = "https://cdn.simpleicons.org/{slug}/0F1F3A"

# Importação opcional de cairosvg
try:
    from cairosvg import svg2png
    _HAS_CAIROSVG = True
except ImportError:
    _HAS_CAIROSVG = False

    def svg2png(*args, **kwargs):  # type: ignore[misc]
        raise ImportError("cairosvg não instalado")


# ─── Resolver ─────────────────────────────────────────────────────────────────

class AssetResolver:
    """6 níveis — inserts_manuais → banco_local → fotos_user → logos → needs_user_input."""

    def __init__(self, bank_root: Path):
        self.bank = AssetBank(bank_root)

    def resolve(self, query: str, cena: int | None, inserts_manuais: list[dict]) -> dict:
        """Tenta achar asset. Retorna {fonte, path, ...} ou {fonte: 'needs_user_input', query}.

        Cadeia de prioridade (6 níveis):
          1. inserts_manuais com prefixo da cena
          2. inserts_manuais livre (match por descricao)
          3. banco_local (~/.editar-video/asset-bank/)
          4. logos_simpleicons (se query menciona brand conhecida — vence fotos_user)
          5. fotos_user (~/.../avatar-photos/medium ou xlarge)
          6. needs_user_input (fallback)

        NOTA: a ordem 4→5 é uma decisão pragmática (commit 2c6c72a) — em queries
        com brand E keyword de foto (ex: "olha o github"), o logo vence.
        Caso composto (logo + foto) fica pra Fase 3 (multi-asset shots).
        """
        q = query.lower()

        # 1. inserts_manuais (mesma cena)
        for ins in inserts_manuais:
            if ins.get("cena") == cena:
                return {"fonte": "inserts_manuais", "path": ins["path"]}

        # 2. inserts_manuais (livre, match descricao)
        for ins in inserts_manuais:
            if ins.get("cena") is None and q in ins.get("descricao", "").lower():
                return {"fonte": "inserts_manuais_livre", "path": ins["path"]}

        # 3. banco_local
        results = self.bank.search(query)
        if results:
            asset = results[0]
            return {
                "fonte": "banco_local",
                "path": str(self.bank.root / asset["path"]),
                "asset_id": asset["id"],
            }

        # 4. fotos_user (só se não tem brand detectada — brand vai para logos no nível 5)
        brand_slug = self._detect_brand(q)
        if brand_slug is None:
            foto = self._resolve_fotos_user(q)
            if foto:
                return foto

        # 5. logos_simpleicons
        logo = self._resolve_logos_simpleicons(q, brand_slug=brand_slug)
        if logo:
            return logo

        # 6. needs_user_input
        return {"fonte": "needs_user_input", "query": query, "cena": cena}

    # ─── nível 4 ──────────────────────────────────────────────────────────────

    def _resolve_fotos_user(self, q: str) -> dict | None:
        """Retorna foto do usuário se keyword bater, None caso contrário."""
        words = set(q.split())

        # Detecta se é CTA para forçar xlarge (word-boundary match)
        is_cta = bool(words & CTA_KEYWORDS)

        # Descobre photo_id pelo mapeamento (match de palavra inteira)
        photo_id = None
        for keywords, pid in PHOTO_KEYWORDS:
            if any(re.search(rf"\b{re.escape(kw)}\b", q) for kw in keywords):
                photo_id = pid
                break

        if photo_id is None:
            return None

        size = "xlarge" if is_cta else "medium"
        photo_path = PHOTOS_BASE_DIR / size / f"{photo_id}.png"

        if not photo_path.exists():
            return None

        return {
            "fonte": "fotos_user",
            "path": str(photo_path),
            "photo_id": photo_id,
            "size": size,
        }

    # ─── nível 5 ──────────────────────────────────────────────────────────────

    def _detect_brand(self, q: str) -> str | None:
        """Retorna slug da brand se detectada em q, None caso contrário."""
        for alias in BRAND_ALIASES:
            if re.search(rf"\b{re.escape(alias)}\b", q):
                return BRAND_ALIASES[alias]
        return None

    def _resolve_logos_simpleicons(self, q: str, brand_slug: str | None = None) -> dict | None:
        """Busca brand logo via SimpleIcons CDN, cacheia localmente. None se falhar."""
        # Usa slug já detectado ou detecta agora
        if brand_slug is None:
            brand_slug = self._detect_brand(q)

        slug = brand_slug
        if slug is None:
            return None

        # Acha brand_name (alias) para o retorno
        brand_name = next((alias for alias, s in BRAND_ALIASES.items() if s == slug), slug)

        LOGOS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        svg_path = LOGOS_CACHE_DIR / f"{slug}.svg"
        png_path = LOGOS_CACHE_DIR / f"{slug}.png"

        # Se PNG já está em cache, retorna direto (valida size mínimo — P0-3)
        if png_path.exists():
            if png_path.stat().st_size > 100:
                return {"fonte": "logos_simpleicons", "path": str(png_path), "slug": slug, "brand": brand_name}
            else:
                png_path.unlink(missing_ok=True)  # limpa PNG corrompido cached

        # Download SVG se não está em cache (P0-1: User-Agent pra evitar 403)
        if not svg_path.exists():
            url = SIMPLEICONS_CDN.format(slug=slug)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Claude/editar-video"},
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = resp.read()
                svg_path.write_bytes(data)
            except (HTTPError, URLError, OSError, TimeoutError) as e:
                svg_path.unlink(missing_ok=True)  # limpa parcial (P0-3)
                print(f"⚠ logo {slug}: falha download ({e})", file=sys.stderr)
                return None

        # Converte SVG → PNG (P0-3: cleanup em falha + valida size)
        if _HAS_CAIROSVG:
            try:
                svg2png(url=str(svg_path), write_to=str(png_path), output_width=400)
                if not png_path.exists() or png_path.stat().st_size < 100:
                    raise ValueError("PNG inválido ou vazio")
                return {"fonte": "logos_simpleicons", "path": str(png_path), "slug": slug, "brand": brand_name}
            except Exception as e:
                png_path.unlink(missing_ok=True)  # limpa PNG parcial/corrompido
                print(f"⚠ logo {slug}: falha conversão SVG→PNG ({e})", file=sys.stderr)

        # Fallback: tenta rsvg-convert
        import shutil
        import subprocess
        if shutil.which("rsvg-convert"):
            try:
                subprocess.run(
                    ["rsvg-convert", "-w", "400", "-f", "png", "-o", str(png_path), str(svg_path)],
                    check=True,
                    timeout=10,
                )
                if png_path.exists() and png_path.stat().st_size > 100:
                    return {"fonte": "logos_simpleicons", "path": str(png_path), "slug": slug, "brand": brand_name}
                png_path.unlink(missing_ok=True)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
                print(f"⚠ logo {slug}: falha rsvg-convert ({e})", file=sys.stderr)

        # SVG baixado mas sem conversor — avisa e retorna SVG (ffmpeg não consomem diretamente)
        print(
            f"[asset_resolver] logo SVG baixado mas faltou conversor — "
            f"instala cairosvg ou rsvg-convert pra usar como overlay",
            file=sys.stderr,
        )
        return {"fonte": "logos_simpleicons", "path": str(svg_path), "slug": slug, "brand": brand_name}
