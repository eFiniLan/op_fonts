"""fontTools.merge wrapper — merges multiple font files into one."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fontTools.merge import Merger
from fontTools.ttLib import TTFont

log = logging.getLogger(__name__)

_TTF_GLYPH_LIMIT = 65535


def _is_cff(font_path: Path) -> bool:
    """Check if a font has CFF outlines."""
    font = TTFont(font_path)
    result = "CFF " in font or "CFF2" in font
    font.close()
    return result


def _ensure_cff(font_path: Path) -> Path:
    """If font has glyf outlines (TTF), convert to CFF. Quadratic→cubic is lossless."""
    font = TTFont(font_path)
    if "CFF " in font or "CFF2" in font:
        font.close()
        return font_path

    log.info("Converting glyf → CFF: %s", font_path.name)

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    upm = font["head"].unitsPerEm
    glyph_order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    cmap = dict(font.getBestCmap() or {})
    hmtx = font["hmtx"]
    metrics = {gname: hmtx[gname] for gname in glyph_order}

    # Build CFF charstrings by drawing each glyph through T2Pen.
    # fontTools >= 4.38 BasePen.qCurveTo auto-decomposes to curveTo,
    # so TTF quadratic outlines are converted to cubic losslessly.
    charstrings: dict[str, object] = {}
    for gname in glyph_order:
        width = hmtx[gname][0]
        pen = T2CharStringPen(width, glyphSet=glyph_set)
        glyph_set[gname].draw(pen)
        charstrings[gname] = pen.getCharString()

    fb = FontBuilder(upm, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupCFF(
        psName=font_path.stem,
        fontInfo={},
        charStringsDict=charstrings,
        privateDict={},
    )
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(
        ascent=font["hhea"].ascent,
        descent=font["hhea"].descent,
    )
    fb.setupNameTable({"familyName": font_path.stem, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    # Copy over OT layout tables if present
    new_font = fb.font
    for tag in ("GSUB", "GPOS", "GDEF"):
        if tag in font:
            new_font[tag] = font[tag]

    out_path = font_path.with_suffix(".cff.otf")
    new_font.save(str(out_path))
    font.close()

    log.info("glyf→CFF done: %s (%d glyphs)", out_path.name, len(glyph_order))
    return out_path


def _ensure_quadratic(font_path: Path) -> Path:
    """If font has CFF outlines (OTF), convert to quadratic TrueType outlines."""
    font = TTFont(font_path)
    if "CFF " not in font and "CFF2" not in font:
        font.close()
        return font_path

    log.info("Converting CFF → quadratic: %s", font_path.name)

    from fontTools.pens.cu2quPen import Cu2QuPointPen
    from fontTools.pens.ttGlyphPen import TTGlyphPointPen
    from fontTools.fontBuilder import FontBuilder

    upm = font["head"].unitsPerEm
    glyph_order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    cmap = dict(font.getBestCmap() or {})

    # Convert each glyph: cubic → quadratic via pen protocol
    glyphs = {}
    for gname in glyph_order:
        pen = TTGlyphPointPen(None)
        cu2qu_pen = Cu2QuPointPen(pen, max_err=1.0, reverse_direction=True)
        glyph_set[gname].drawPoints(cu2qu_pen)
        glyphs[gname] = pen.glyph()

    # Collect horizontal metrics
    hmtx = font["hmtx"]
    metrics = {gname: hmtx[gname] for gname in glyph_order}

    # Build new TTF via FontBuilder
    fb = FontBuilder(upm, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(
        ascent=font["hhea"].ascent,
        descent=font["hhea"].descent,
    )
    fb.setupNameTable({"familyName": font_path.stem, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    # Copy over OT layout tables if present
    new_font = fb.font
    for tag in ("GSUB", "GPOS", "GDEF"):
        if tag in font:
            new_font[tag] = font[tag]

    out_path = font_path.with_suffix(".conv.ttf")
    new_font.save(str(out_path))
    font.close()

    log.info("CFF→TTF done: %s (%d glyphs)", out_path.name, len(glyph_order))
    return out_path


def _decid_cff(font_path: Path) -> Path:
    """Convert CID-keyed CFF to name-keyed CFF (required for fontTools merger).

    Rebuilds the font from scratch via T2CharStringPen to ensure clean name-keyed output.
    """
    font = TTFont(font_path)
    if "CFF " not in font:
        font.close()
        return font_path

    cff = font["CFF "].cff
    top_dict = cff.topDictIndex[0]
    if not hasattr(top_dict, "ROS"):
        font.close()
        return font_path

    log.info("Converting CID-keyed → name-keyed CFF: %s", font_path.name)

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    upm = font["head"].unitsPerEm
    glyph_order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    cmap = dict(font.getBestCmap() or {})
    hmtx = font["hmtx"]
    metrics = {gname: hmtx[gname] for gname in glyph_order}

    charstrings: dict[str, object] = {}
    for gname in glyph_order:
        width = hmtx[gname][0]
        pen = T2CharStringPen(width, glyphSet=glyph_set)
        glyph_set[gname].draw(pen)
        charstrings[gname] = pen.getCharString()

    fb = FontBuilder(upm, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupCFF(
        psName=font_path.stem,
        fontInfo={},
        charStringsDict=charstrings,
        privateDict={},
    )
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(
        ascent=font["hhea"].ascent,
        descent=font["hhea"].descent,
    )
    fb.setupNameTable({"familyName": font_path.stem, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    new_font = fb.font
    for tag in ("GSUB", "GPOS", "GDEF"):
        if tag in font:
            new_font[tag] = font[tag]

    out_path = font_path.with_suffix(".nk.otf")
    new_font.save(str(out_path))
    font.close()

    log.info("CID→name-keyed done: %s (%d glyphs)", out_path.name, len(glyph_order))
    return out_path


def _strip_tables(font_path: Path, table_tags: list[str]) -> None:
    """Remove specified tables from a font file in-place."""
    font = TTFont(font_path)
    changed = False
    for tag in table_tags:
        if tag in font:
            del font[tag]
            changed = True
    if changed:
        font.save(str(font_path))
    font.close()


def _normalize_upm(font_path: Path, target_upm: int) -> Path:
    """Scale font to target UPM if different."""
    font = TTFont(font_path)
    current_upm = font["head"].unitsPerEm
    if current_upm == target_upm:
        font.close()
        return font_path

    log.info("Scaling %s UPM %d → %d", font_path.name, current_upm, target_upm)
    from fontTools.ttLib.scaleUpem import scale_upem
    scale_upem(font, target_upm)
    out_path = font_path.with_suffix(".scaled.otf")
    font.save(str(out_path))
    font.close()
    return out_path


def _ensure_gsub(font_path: Path) -> None:
    """Ensure the font has a GSUB table (add empty one if missing)."""
    font = TTFont(font_path)
    if "GSUB" not in font:
        log.debug("Adding empty GSUB to %s", font_path.name)
        from fontTools.ttLib.tables import G_S_U_B_
        from fontTools.ttLib.tables.otTables import GSUB as GSUBTable

        gsub = G_S_U_B_.table_G_S_U_B_()
        gsub_table = GSUBTable()
        gsub_table.Version = 0x00010000
        gsub_table.ScriptList = None
        gsub_table.FeatureList = None
        gsub_table.LookupList = None
        gsub.table = gsub_table
        font["GSUB"] = gsub
        font.save(str(font_path))
    font.close()


def _rebuild_cmap(font: TTFont) -> None:
    """Rebuild cmap using format 12 to avoid format 4 overflow with large glyph sets."""
    from fontTools.ttLib.tables._c_m_a_p import cmap_format_12

    cmap = font["cmap"]
    # Collect all mappings from existing subtables
    all_mappings: dict[int, str] = {}
    for table in cmap.tables:
        if hasattr(table, "cmap") and table.cmap:
            all_mappings.update(table.cmap)

    fmt12 = cmap_format_12(12)
    fmt12.platEncID = 10
    fmt12.platformID = 3
    fmt12.format = 12
    fmt12.reserved = 0
    fmt12.length = 0
    fmt12.language = 0
    fmt12.cmap = all_mappings

    cmap.tables = [fmt12]
    log.info("Rebuilt cmap: %d mappings", len(all_mappings))


def merge_fonts(
    font_paths: list[Path],
    output_path: Path,
    drop_tables: list[str] | None = None,
) -> Path:
    """Merge multiple font files into one.

    Auto-detects outline format: if majority are CFF, converts outliers
    to CFF and outputs OTF. Otherwise converts to TTF.
    The first font in the list defines baseline metrics.
    """
    if not font_paths:
        raise ValueError("No fonts to merge")

    if len(font_paths) == 1:
        log.info("Only one font — copying directly to output")
        shutil.copy2(font_paths[0], output_path)
        return output_path

    # Determine dominant outline format
    cff_count = sum(1 for p in font_paths if _is_cff(p))
    use_cff = cff_count > len(font_paths) // 2
    convert_fn = _ensure_cff if use_cff else _ensure_quadratic
    fmt_name = "CFF" if use_cff else "TTF"
    log.info("Outline format: %s (%d/%d CFF inputs)", fmt_name, cff_count, len(font_paths))

    target_upm = TTFont(font_paths[0])["head"].unitsPerEm
    TTFont(font_paths[0]).close()

    processed: list[Path] = []
    for p in font_paths:
        converted = _decid_cff(p)
        converted = convert_fn(converted)
        converted = _normalize_upm(converted, target_upm)
        _ensure_gsub(converted)
        if drop_tables:
            _strip_tables(converted, drop_tables)
        processed.append(converted)

    log.info("Merging %d fonts (base: %s, UPM: %d)", len(processed), processed[0].name, target_upm)

    merger = Merger()
    merged = merger.merge([str(p) for p in processed])

    glyph_count = len(merged.getGlyphOrder())
    if glyph_count > _TTF_GLYPH_LIMIT:
        raise RuntimeError(
            f"Merged font has {glyph_count} glyphs, exceeding TTF limit of {_TTF_GLYPH_LIMIT}"
        )
    log.info("Merged glyph count: %d", glyph_count)

    _rebuild_cmap(merged)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(str(output_path))
    merged.close()

    log.info("Merged font saved: %s (%.1f KB)", output_path, output_path.stat().st_size / 1024)
    return output_path
