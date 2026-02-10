"""fontTools.subset wrapper with Unicode range parsing."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

log = logging.getLogger(__name__)


def parse_unicode_ranges(ranges: list[str]) -> list[int]:
    """Parse Unicode range strings like 'U+0600-06FF' into a sorted list of codepoints."""
    codepoints: set[int] = set()
    for r in ranges:
        r = r.strip().upper()
        if not r.startswith("U+"):
            raise ValueError(f"Invalid Unicode range: {r!r}")
        r = r[2:]  # strip U+
        if "-" in r:
            start_s, end_s = r.split("-", 1)
            start = int(start_s, 16)
            end = int(end_s, 16)
            codepoints.update(range(start, end + 1))
        else:
            codepoints.add(int(r, 16))
    return sorted(codepoints)


def subset_font(
    font_path: Path,
    unicode_ranges: list[str] | None = None,
    output_path: Path | None = None,
    codepoints: list[int] | None = None,
) -> Path:
    """Subset a font to only the glyphs covering the given Unicode ranges or codepoints.

    Returns the path to the subset font (a temp file if output_path is None).
    """
    if codepoints is None:
        if not unicode_ranges:
            raise ValueError("Either unicode_ranges or codepoints must be provided")
        codepoints = parse_unicode_ranges(unicode_ranges)
    if not codepoints:
        raise ValueError(f"No codepoints resolved from ranges: {unicode_ranges}")

    font = TTFont(font_path)

    # Check how many requested codepoints exist in the font
    cmap = font.getBestCmap() or {}
    present = [cp for cp in codepoints if cp in cmap]
    if not present:
        log.warning(
            "No glyphs found in %s for any of the %d requested codepoints",
            font_path.name, len(codepoints),
        )
        font.close()
        raise ValueError(f"No matching glyphs in {font_path.name} for given ranges")

    if len(present) < len(codepoints):
        log.debug(
            "%s: %d/%d requested codepoints have glyphs",
            font_path.name, len(present), len(codepoints),
        )

    options = Options()
    options.layout_features = ["*"]  # keep all GSUB/GPOS features
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.recalc_timestamp = False
    options.drop_tables = []  # don't drop anything yet — merge step handles this

    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".ttf", delete=False)
        output_path = Path(tmp.name)
        tmp.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(output_path))
    font.close()

    log.info(
        "Subset %s → %s (%d codepoints, %.1f KB)",
        font_path.name, output_path.name,
        len(present), output_path.stat().st_size / 1024,
    )
    return output_path
