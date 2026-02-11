"""Build pipeline orchestrator: download → subset → merge → rename."""

from __future__ import annotations

import copy
import json
import logging
import shutil
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


def _fetch_languages_json(url: str, cache_dir: Path) -> Path:
    """Download languages.json from URL, caching locally."""
    from .download import _download
    cached = cache_dir / "languages.json"
    if cached.exists():
        log.debug("Using cached languages.json: %s", cached)
        return cached
    log.info("Fetching languages.json from %s", url)
    _download(url, cached)
    return cached


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
    resolved = _resolve_languages_json(config, languages_json)
    if resolved:
        _apply_languages_json(config, resolved)

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
) -> Path:
    """Execute the full build pipeline. Returns the path to the output font."""
    if languages_json:
        _apply_languages_json(config, languages_json)

    enabled = [s for s in config.scripts if s.enabled]
    if not enabled:
        raise RuntimeError("No scripts enabled — nothing to build")

    log.info("Building %s (%s mode, %d scripts)", config.output, config.mode, len(enabled))

    # 1. Download
    log.info("Step 1/5: Downloading fonts...")
    font_paths: dict[str, Path] = {}
    for script in enabled:
        font_paths[script.name] = ensure_font(config, script)

    symbol_paths: list[Path] = []
    if config.symbols.enabled:
        for sf in config.symbols.fonts:
            symbol_paths.append(ensure_symbol_font(config, sf.name, sf.path, sf.url))

    # 2. Subset (dedup: later scripts only get codepoints not already covered)
    log.info("Step 2/5: Subsetting fonts...")
    work_dir = Path(tempfile.mkdtemp(prefix="op_fonts_"))
    subset_paths: list[Path] = []
    covered_cps: set[int] = set()

    for script in enabled:
        src = font_paths[script.name]
        out = work_dir / f"subset_{script.name}.otf"
        codepoints = _resolve_codepoints(script, config)
        if covered_cps:
            codepoints = [cp for cp in codepoints if cp not in covered_cps]
        if not codepoints:
            log.info("Skipping %s: all codepoints already covered", script.name)
            continue
        try:
            subset_font(src, codepoints=codepoints, output_path=out)
            # Track actual cmap (font may not have all requested codepoints)
            from fontTools.ttLib import TTFont
            font = TTFont(out)
            covered_cps.update(font.getBestCmap().keys())
            font.close()
            subset_paths.append(out)
        except ValueError as exc:
            log.warning("Skipping %s: %s", script.name, exc)

    # Symbols
    if config.symbols.enabled and config.symbols.unicode_ranges:
        sym_cps = set(parse_unicode_ranges(config.symbols.unicode_ranges))
        for sp in symbol_paths:
            out = work_dir / f"subset_symbols_{sp.stem}.otf"
            try:
                subset_font(sp, codepoints=sorted(sym_cps), output_path=out)
                subset_paths.append(out)
            except ValueError as exc:
                log.warning("Skipping symbols %s: %s", sp.name, exc)

    if not subset_paths:
        raise RuntimeError("All subsets were empty — nothing to merge")

    # 3. Merge
    log.info("Step 3/5: Merging %d subset fonts...", len(subset_paths))
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / config.output
    merge_fonts(subset_paths, output_path, drop_tables=config.merge.drop_tables)

    # 4. Prune unused GSUB/GPOS features and unreferenced glyphs
    if config.merge.keep_features:
        _prune_features(output_path, config.merge.keep_features)

    # 5. Rename, fix metrics & subroutinize
    log.info("Step 5/5: Setting font metadata...")
    rename_font(output_path, config.name, config.style)
    _fix_metrics(output_path)
    _subroutinize(output_path)

    # Clean up temp dir
    shutil.rmtree(work_dir, ignore_errors=True)

    final_size = output_path.stat().st_size
    log.info(
        "Done! %s — %.1f KB (%d glyphs)",
        output_path,
        final_size / 1024,
        _count_glyphs(output_path),
    )
    return output_path



def _prune_features(font_path: Path, keep_features: list[str]) -> None:
    """Remove GSUB/GPOS features not in keep list, then prune unreferenced glyphs.

    Uses fontTools subsetter to re-subset the merged font, keeping only the
    codepoints already in the cmap and the specified layout features. This
    drops alternate glyphs (stylistic sets, CJK variants, etc.) that aren't
    needed for a car UI.
    """
    from fontTools.ttLib import TTFont
    from fontTools.subset import Subsetter, Options

    font = TTFont(font_path)
    cmap = font.getBestCmap()
    before_glyphs = len(font.getGlyphOrder())

    opts = Options()
    opts.layout_features = keep_features
    opts.notdef_outline = True
    opts.name_legacy = True
    opts.glyph_names = False
    opts.drop_tables += ["DSIG"]

    subsetter = Subsetter(options=opts)
    subsetter.populate(unicodes=list(cmap.keys()))
    subsetter.subset(font)

    after_glyphs = len(font.getGlyphOrder())
    font.save(str(font_path))
    font.close()

    before_size = font_path.stat().st_size  # saved size
    log.info(
        "Step 4/5: Pruned features → kept %s, glyphs %d → %d (removed %d)",
        keep_features, before_glyphs, after_glyphs, before_glyphs - after_glyphs,
    )


def _fix_metrics(font_path: Path) -> None:
    """Adjust metrics and scale glyphs to match Inter's proportions.

    Inter (UPM 2816): capHeight=2048, ratio=0.727
    IBM Plex (UPM 1000): capHeight=698, ratio=0.698
    Scale factor: 0.727/0.698 = 1.042 (~4% larger)
    """
    from fontTools.ttLib import TTFont
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    font = TTFont(font_path)
    upm = font["head"].unitsPerEm  # 1000

    # Scale glyphs to match Inter's cap-height ratio
    # Inter: capHeight/UPM = 2048/2816 = 0.7273
    # Plex:  capHeight/UPM = 698/1000  = 0.698
    inter_cap_ratio = 2048 / 2816
    plex_cap_ratio = font["OS/2"].sCapHeight / upm if font["OS/2"].sCapHeight else 0.698
    scale = inter_cap_ratio / plex_cap_ratio
    log.info("Scaling glyphs by %.3f to match Inter cap-height ratio", scale)

    if "CFF " in font:
        from fontTools.pens.transformPen import TransformPen

        cff = font["CFF "]
        td = cff.cff.topDictIndex[0]
        cs = td.CharStrings
        hmtx = font["hmtx"]

        for gname in list(cs.keys()):
            old_cs = cs[gname]
            old_cs.decompile()
            width = hmtx.metrics[gname][0] if gname in hmtx.metrics else 0
            pen = T2CharStringPen(width=round(width * scale), glyphSet=None)
            tpen = TransformPen(pen, (scale, 0, 0, scale, 0, 0))
            old_cs.draw(tpen)
            new_cs = pen.getCharString()
            new_cs.private = old_cs.private
            new_cs.globalSubrs = old_cs.globalSubrs
            cs[gname] = new_cs

        # Scale horizontal metrics
        for gname in hmtx.metrics:
            width, lsb = hmtx.metrics[gname]
            hmtx.metrics[gname] = (round(width * scale), round(lsb * scale))

    # Inter's metrics (scaled to UPM 1000): ascender=969, descender=-242
    ascender = 969
    descender = -242

    os2 = font["OS/2"]
    os2.sTypoAscender = ascender
    os2.sTypoDescender = descender
    os2.sTypoLineGap = 0
    os2.usWinAscent = ascender
    os2.usWinDescent = abs(descender)
    os2.sxHeight = round(os2.sxHeight * scale) if os2.sxHeight else 0
    os2.sCapHeight = round(os2.sCapHeight * scale) if os2.sCapHeight else 0

    hhea = font["hhea"]
    hhea.ascent = ascender
    hhea.descent = descender
    hhea.lineGap = 0

    font.save(str(font_path))
    font.close()
    log.info("Fixed metrics: ascender=%d, descender=%d, scale=%.3f", ascender, descender, scale)




def _subroutinize(font_path: Path) -> None:
    """Re-subroutinize CFF outlines for smaller file size."""
    from fontTools.ttLib import TTFont
    font = TTFont(font_path)
    if "CFF " not in font:
        font.close()
        return
    try:
        import cffsubr
        before = font_path.stat().st_size
        cffsubr.subroutinize(font)
        font.save(str(font_path))
        after = font_path.stat().st_size
        log.info("Subroutinized: %.1f KB → %.1f KB (%.0f%% smaller)",
                 before / 1024, after / 1024, (before - after) / before * 100)
    except ImportError:
        log.debug("cffsubr not installed, skipping subroutinization")
    font.close()


def _count_glyphs(font_path: Path) -> int:
    from fontTools.ttLib import TTFont
    font = TTFont(font_path)
    count = len(font.getGlyphOrder())
    font.close()
    return count


def _resolve_languages_json(config: BuildConfig, languages_json: Path | None) -> Path | None:
    """Resolve languages.json: use local path, or auto-fetch from config URL."""
    if languages_json:
        return languages_json
    if config.languages_url:
        return _fetch_languages_json(config.languages_url, config.cache_dir)
    return None


def build_all(
    config: BuildConfig,
    languages_json: Path | None = None,
) -> list[Path]:
    """Build all weight variants defined in config."""
    resolved = _resolve_languages_json(config, languages_json)

    if not config.weights:
        return [build(config, languages_json=resolved)]

    # Apply language filter once before iterating weights
    if resolved:
        _apply_languages_json(config, resolved)

    outputs = []
    for weight in config.weights:
        log.info("=== Building weight: %s ===", weight)
        cfg = copy.deepcopy(config)
        cfg.style = weight
        cfg.output = f"{config.name}-{weight}.otf"
        # Replace "Regular" in static font names for this weight
        for script in cfg.scripts:
            script.noto_font = script.noto_font.replace("Regular", weight)
        outputs.append(build(cfg))
    return outputs
