"""Build pipeline orchestrator: download → subset → merge → rename."""

from __future__ import annotations

import copy
import json
import logging
import tempfile
from pathlib import Path

from .charsets import load_charset_file
from .config import BuildConfig, enable_scripts_for
from .download import ensure_font, ensure_symbol_font, get_download_plan
from .merge import merge_fonts
from .naming import rename_font
from .subset import parse_unicode_ranges, subset_font
from .unicode_blocks import scripts_for_languages

log = logging.getLogger(__name__)


def _apply_languages_json(config: BuildConfig, languages_json: Path) -> None:
    """Read a languages.json file and enable only the needed scripts."""
    with open(languages_json) as f:
        data = json.load(f)

    # openpilot's languages.json: {"Display Name": "lang_code", ...}
    # Also accept a plain list of lang codes.
    if isinstance(data, dict):
        lang_codes = list(data.values())
    elif isinstance(data, list):
        lang_codes = data
    else:
        raise ValueError(f"Unexpected languages.json format: {type(data)}")

    needed_scripts = scripts_for_languages(lang_codes)
    log.info("Languages %s → scripts %s", lang_codes, sorted(needed_scripts))
    enable_scripts_for(config, needed_scripts)


def _resolve_codepoints(script, config: BuildConfig) -> list[int]:
    """Resolve the full codepoint list for a script entry.

    If charset_file is set, load codepoints from it and merge with unicode_ranges
    (so CJK ideographs come from the charset, but punctuation/fullwidth ranges
    are still included from unicode_ranges).
    """
    if script.charset_file:
        charset_path = Path(script.charset_file)
        if not charset_path.is_absolute():
            # Resolve relative to config's cache dir parent (project root)
            charset_path = config.cache_dir.parent / charset_path
        charset_cps = set(load_charset_file(charset_path))
        log.info(
            "%s: loaded %d codepoints from charset file %s",
            script.name, len(charset_cps), charset_path,
        )
        # Merge with unicode_ranges (for punctuation, fullwidth, etc.)
        if script.unicode_ranges:
            range_cps = set(parse_unicode_ranges(script.unicode_ranges))
            charset_cps |= range_cps
        return sorted(charset_cps)
    return parse_unicode_ranges(script.unicode_ranges)


def dry_run(
    config: BuildConfig,
    languages_json: Path | None = None,
) -> None:
    """Print the build plan without executing anything."""
    if languages_json:
        _apply_languages_json(config, languages_json)

    enabled = [s for s in config.scripts if s.enabled]
    print(f"Mode: {config.mode}")
    print(f"Output: {config.output}")
    print(f"Cache: {config.cache_dir}")
    print(f"\nEnabled scripts ({len(enabled)}):")
    for s in enabled:
        cps = _resolve_codepoints(s, config)
        charset_tag = f" [charset: {s.charset_file}]" if s.charset_file else ""
        print(f"  {s.name}: {s.noto_font} → {len(cps)} codepoints{charset_tag}")

    if config.symbols.enabled:
        print(f"\nSymbols:")
        for sf in config.symbols.fonts:
            print(f"  {sf.name}")
        sym_cps = parse_unicode_ranges(config.symbols.unicode_ranges)
        print(f"  {len(sym_cps)} codepoints from ranges")

    print(f"\nDownload plan:")
    for name, url, cached in get_download_plan(config):
        status = "cached" if cached.exists() else "download"
        print(f"  [{status}] {name}")
        print(f"    {url}")

    print(f"\nMerge order (first = baseline metrics):")
    for i, s in enumerate(enabled):
        print(f"  {i + 1}. {s.noto_font} ({s.name})")
    if config.symbols.enabled:
        for sf in config.symbols.fonts:
            print(f"  {len(enabled) + 1}. {sf.name} (symbols)")

    print(f"\nDrop tables: {config.merge.drop_tables}")
    print(f"Final name: {config.name} {config.style}")


def build(
    config: BuildConfig,
    languages_json: Path | None = None,
    weight_value: int | None = None,
) -> Path:
    """Execute the full build pipeline. Returns the path to the output font."""
    if languages_json:
        _apply_languages_json(config, languages_json)

    enabled = [s for s in config.scripts if s.enabled]
    if not enabled:
        raise RuntimeError("No scripts enabled — nothing to build")

    log.info("Building %s (%s mode, %d scripts)", config.output, config.mode, len(enabled))

    # 1. Download
    log.info("Step 1/4: Downloading fonts...")
    font_paths: dict[str, Path] = {}
    for script in enabled:
        font_paths[script.name] = ensure_font(config, script)

    symbol_paths: list[Path] = []
    if config.symbols.enabled:
        for sf in config.symbols.fonts:
            symbol_paths.append(ensure_symbol_font(config, sf.name, sf.path))

    # 2. Instance variable fonts + Subset
    log.info("Step 2/4: Subsetting fonts...")
    work_dir = Path(tempfile.mkdtemp(prefix="op_fonts_"))
    subset_paths: list[Path] = []
    wght = weight_value or 400

    for script in enabled:
        src = _instance_variable_font(font_paths[script.name], wght)
        out = work_dir / f"subset_{script.name}.ttf"
        codepoints = _resolve_codepoints(script, config)
        try:
            subset_font(src, codepoints=codepoints, output_path=out)
            subset_paths.append(out)
        except ValueError as exc:
            log.warning("Skipping %s: %s", script.name, exc)

    # Symbols
    if config.symbols.enabled and config.symbols.unicode_ranges:
        sym_cps = set(parse_unicode_ranges(config.symbols.unicode_ranges))
        for sp in symbol_paths:
            out = work_dir / f"subset_symbols_{sp.stem}.ttf"
            try:
                subset_font(sp, codepoints=sorted(sym_cps), output_path=out)
                subset_paths.append(out)
            except ValueError as exc:
                log.warning("Skipping symbols %s: %s", sp.name, exc)

    if not subset_paths:
        raise RuntimeError("All subsets were empty — nothing to merge")

    # 3. Merge
    log.info("Step 3/4: Merging %d subset fonts...", len(subset_paths))
    output_path = Path(config.output)
    merge_fonts(subset_paths, output_path, drop_tables=config.merge.drop_tables)

    # 4. Rename & fix metrics
    log.info("Step 4/4: Setting font metadata...")
    rename_font(output_path, config.name, config.style)
    _fix_metrics(output_path)

    final_size = output_path.stat().st_size
    log.info(
        "Done! %s — %.1f KB (%d glyphs)",
        output_path,
        final_size / 1024,
        _count_glyphs(output_path),
    )
    return output_path


def _instance_variable_font(font_path: Path, wght: int = 400) -> Path:
    """If font is a variable font, instance it to the given weight. Returns static path."""
    from fontTools.ttLib import TTFont
    font = TTFont(font_path)
    if "fvar" not in font:
        font.close()
        return font_path

    log.info("Instancing variable font %s to wght=%d", font_path.name, wght)
    from fontTools.varLib.instancer import instantiateVariableFont
    instance = instantiateVariableFont(font, {"wght": wght})
    out_path = font_path.with_suffix(f".w{wght}.ttf")
    instance.save(str(out_path))
    instance.close()
    font.close()
    return out_path


def _fix_metrics(font_path: Path) -> None:
    """Adjust vertical metrics to match Inter's proportions.

    CJK fonts have oversized ascender/descender values relative to UPM,
    which causes raylib/BMFont to render glyphs smaller than Inter.
    We scale the metrics to match Inter's height/UPM ratio (~1.21).
    """
    from fontTools.ttLib import TTFont

    font = TTFont(font_path)
    upm = font["head"].unitsPerEm  # 1000

    # Inter's metrics (scaled to UPM 1000): ascender=969, descender=-242
    # Ratio: (969+242)/1000 = 1.211
    ascender = 969
    descender = -242

    os2 = font["OS/2"]
    os2.sTypoAscender = ascender
    os2.sTypoDescender = descender
    os2.sTypoLineGap = 0
    os2.usWinAscent = ascender
    os2.usWinDescent = abs(descender)

    hhea = font["hhea"]
    hhea.ascent = ascender
    hhea.descent = descender
    hhea.lineGap = 0

    font.save(str(font_path))
    font.close()
    log.info("Fixed vertical metrics: ascender=%d, descender=%d (UPM=%d)", ascender, descender, upm)


def _count_glyphs(font_path: Path) -> int:
    from fontTools.ttLib import TTFont
    font = TTFont(font_path)
    count = len(font.getGlyphOrder())
    font.close()
    return count


def build_all(
    config: BuildConfig,
    languages_json: Path | None = None,
) -> list[Path]:
    """Build all weight variants defined in config."""
    if not config.weights:
        return [build(config, languages_json=languages_json)]

    # Apply language filter once before iterating weights
    if languages_json:
        _apply_languages_json(config, languages_json)

    outputs = []
    for weight in config.weights:
        log.info("=== Building weight: %s ===", weight)
        cfg = copy.deepcopy(config)
        cfg.style = weight
        cfg.output = f"{config.name}-{weight}.ttf"
        wght = config.weight_values.get(weight, 400)
        outputs.append(build(cfg, weight_value=wght))
    return outputs
