"""TOML config loading → BuildConfig dataclass."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class SourceCDN:
    base_url: str
    variant: str


@dataclass
class SourceCJK:
    base_url: str


@dataclass
class Sources:
    cdn: SourceCDN
    cjk: SourceCJK


@dataclass
class ScriptEntry:
    name: str
    enabled: bool
    noto_font: str
    noto_path: str
    unicode_ranges: list[str]
    is_cjk: bool = False
    charset_file: str | None = None  # path to codepoint list file (overrides CJK ideograph ranges)
    variant: str | None = None  # per-script CDN variant override (e.g. "full/variable-ttf")


@dataclass
class SymbolEntry:
    name: str
    path: str
    url: str = ""  # explicit download URL (overrides CDN pattern)


@dataclass
class SymbolsConfig:
    enabled: bool
    fonts: list[SymbolEntry]
    unicode_ranges: list[str]


@dataclass
class EmojiConfig:
    enabled: bool
    font: str
    url: str = ""               # direct download URL (overrides CDN pattern)
    path: str = ""              # CDN subpath for download (fallback)
    unicode_ranges: list[str] = field(default_factory=list)


@dataclass
class MergeConfig:
    drop_tables: list[str]
    keep_features: list[str] = field(default_factory=list)  # GSUB/GPOS features to keep (empty = keep all)


@dataclass
class BuildConfig:
    name: str
    style: str
    output: str
    mode: str  # "replace_gonoto" or "unified"
    cache_dir: Path
    output_dir: str
    sources: Sources
    scripts: list[ScriptEntry]
    symbols: SymbolsConfig
    emoji: EmojiConfig
    merge: MergeConfig
    weights: list[str] = field(default_factory=list)
    weight_values: dict[str, int] = field(default_factory=dict)
    languages_url: str = ""


def _parse_script(raw: dict) -> ScriptEntry:
    name = raw["name"]
    is_cjk = name in ("cjk", "hangul") or name.startswith(("cjk_", "cjk-"))
    return ScriptEntry(
        name=name,
        enabled=raw.get("enabled", True),
        noto_font=raw["noto_font"],
        noto_path=raw["noto_path"],
        unicode_ranges=raw.get("unicode_ranges", []),
        is_cjk=is_cjk,
        charset_file=raw.get("charset_file"),
        variant=raw.get("variant"),
    )


def load_config(path: Path, overrides: dict | None = None) -> BuildConfig:
    """Load a TOML config file and return a BuildConfig."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    overrides = overrides or {}

    font = raw["font"]
    cache = raw.get("cache", {})
    sources_raw = raw["sources"]
    scripts_raw = raw.get("scripts", [])
    symbols_raw = raw.get("symbols", {})
    emoji_raw = raw.get("emoji", {})
    merge_raw = raw.get("merge", {})

    mode = overrides.get("mode", font.get("mode", "replace_gonoto"))

    symbols_fonts = []
    for sf in symbols_raw.get("fonts", []):
        symbols_fonts.append(SymbolEntry(name=sf["name"], path=sf.get("path", ""), url=sf.get("url", "")))

    weights = font.get("weights", [])
    weight_values = font.get("weight_values", {})

    output_dir = font.get("output_dir", "dist")

    config = BuildConfig(
        name=font.get("name", "OpFont"),
        style=font.get("style", weights[0] if weights else "Regular"),
        output=overrides.get("output", font.get("output", f"{font.get('name', 'OpFont')}-{weights[0] if weights else 'Regular'}.otf")),
        mode=mode,
        cache_dir=Path(overrides.get("cache_dir", cache.get("dir", "./cache"))),
        output_dir=output_dir,
        sources=Sources(
            cdn=SourceCDN(
                base_url=sources_raw["cdn"]["base_url"],
                variant=sources_raw["cdn"]["variant"],
            ),
            cjk=SourceCJK(
                base_url=sources_raw["cjk"]["base_url"],
            ),
        ),
        scripts=[_parse_script(s) for s in scripts_raw],
        symbols=SymbolsConfig(
            enabled=symbols_raw.get("enabled", False),
            fonts=symbols_fonts,
            unicode_ranges=symbols_raw.get("unicode_ranges", []),
        ),
        emoji=EmojiConfig(
            enabled=emoji_raw.get("enabled", False),
            font=emoji_raw.get("font", ""),
            url=emoji_raw.get("url", ""),
            path=emoji_raw.get("path", ""),
            unicode_ranges=emoji_raw.get("unicode_ranges", []),
        ),
        merge=MergeConfig(
            drop_tables=merge_raw.get("drop_tables", []),
            keep_features=merge_raw.get("keep_features", []),
        ),
        weights=weights,
        weight_values=weight_values,
        languages_url=font.get("languages_url", ""),
    )

    log.debug("Loaded config: mode=%s, %d scripts", config.mode, len(config.scripts))
    return config


def enable_scripts_for(config: BuildConfig, script_names: set[str]) -> None:
    """Enable only the scripts whose names are in script_names (plus symbols).

    Uses prefix matching: script "cjk-sc" matches needed script "cjk".
    """
    for script in config.scripts:
        script.enabled = any(
            script.name == needed or script.name.startswith(needed + "-")
            for needed in script_names
        )
    log.info(
        "Enabled scripts: %s",
        [s.name for s in config.scripts if s.enabled],
    )
