"""Noto font downloader with local caching and retry."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import BuildConfig, ScriptEntry

log = logging.getLogger(__name__)

_USER_AGENT = "op_fonts/0.1"
_MAX_RETRIES = 3
_RETRY_DELAY = 2.0  # seconds


def _download(url: str, dest: Path) -> None:
    """Download url to dest with retries."""
    log.info("Downloading %s", url)
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with urlopen(req, timeout=60) as resp:
                data = resp.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            log.debug("Saved %s (%d bytes)", dest, len(data))
            return
        except (HTTPError, URLError, OSError) as exc:
            if attempt == _MAX_RETRIES:
                raise RuntimeError(f"Failed to download {url} after {_MAX_RETRIES} attempts") from exc
            log.warning("Attempt %d/%d failed for %s: %s", attempt, _MAX_RETRIES, url, exc)
            time.sleep(_RETRY_DELAY * attempt)


def _font_url(config: BuildConfig, script: ScriptEntry) -> str:
    """Build the download URL for a script's font."""
    font_name = quote(script.noto_font, safe="")
    if script.is_cjk:
        return f"{config.sources.cjk.base_url}/{script.noto_path}/{font_name}"
    variant = script.variant or config.sources.cdn.variant
    return f"{config.sources.cdn.base_url}/{script.noto_path}/{variant}/{font_name}"


def _symbol_url(config: BuildConfig, font_name: str, font_path: str) -> str:
    return f"{config.sources.cdn.base_url}/{font_path}/{config.sources.cdn.variant}/{font_name}"


def _cache_path(cache_dir: Path, font_name: str) -> Path:
    return cache_dir / font_name


def ensure_font(config: BuildConfig, script: ScriptEntry) -> Path:
    """Return local path to the font for the given script, downloading if needed."""
    cached = _cache_path(config.cache_dir, script.noto_font)
    if cached.exists():
        log.debug("Cache hit: %s", cached)
        return cached
    url = _font_url(config, script)
    _download(url, cached)
    return cached


def ensure_symbol_font(config: BuildConfig, font_name: str, font_path: str) -> Path:
    """Return local path to a symbol font, downloading if needed."""
    cached = _cache_path(config.cache_dir, font_name)
    if cached.exists():
        log.debug("Cache hit: %s", cached)
        return cached
    url = _symbol_url(config, font_name, font_path)
    _download(url, cached)
    return cached


def ensure_url_font(config: BuildConfig, font_name: str, url: str) -> Path:
    """Download a font from an explicit URL, caching locally."""
    cached = _cache_path(config.cache_dir, font_name)
    if cached.exists():
        log.debug("Cache hit: %s", cached)
        return cached
    _download(url, cached)
    return cached


def _emoji_url(config: BuildConfig) -> str:
    if config.emoji.url:
        return config.emoji.url
    return _symbol_url(config, config.emoji.font, config.emoji.path)


def get_download_plan(config: BuildConfig) -> list[tuple[str, str, Path]]:
    """Return (name, url, cache_path) tuples for all fonts that would be downloaded."""
    plan: list[tuple[str, str, Path]] = []
    for script in config.scripts:
        if not script.enabled:
            continue
        cached = _cache_path(config.cache_dir, script.noto_font)
        url = _font_url(config, script)
        plan.append((script.noto_font, url, cached))
    if config.symbols.enabled:
        for sf in config.symbols.fonts:
            cached = _cache_path(config.cache_dir, sf.name)
            url = _symbol_url(config, sf.name, sf.path)
            plan.append((sf.name, url, cached))
    if config.emoji.enabled and config.emoji.font:
        cached = _cache_path(config.cache_dir, config.emoji.font)
        url = _emoji_url(config)
        plan.append((config.emoji.font, url, cached))
    return plan
