"""CJK charset generation from Unicode Unihan database + charset file I/O."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

UNIHAN_URL = "https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip"
UNIHAN_MAPPINGS_FILE = "Unihan_OtherMappings.txt"

# Unihan fields that identify common-use CJK ideographs per locale.
# We take the UNION of all three to build one unified charset.
LOCALE_FIELDS = {
    "kGB0":     "SC (GB 2312)",
    "kBigFive": "TC (Big5 Level 1)",
    "kJis0":    "JP (JIS X 0208)",
}


def load_charset_file(path: Path) -> list[int]:
    """Read a charset file (one hex codepoint per line) → sorted codepoint list."""
    codepoints: set[int] = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            codepoints.add(int(line, 16))
    log.debug("Loaded %d codepoints from %s", len(codepoints), path)
    return sorted(codepoints)


def save_charset_file(path: Path, codepoints: set[int], header: str = "") -> None:
    """Write codepoints to a charset file (one hex codepoint per line)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        if header:
            for line in header.splitlines():
                f.write(f"# {line}\n")
            f.write("\n")
        for cp in sorted(codepoints):
            f.write(f"{cp:04X}\n")
    log.info("Wrote %d codepoints to %s", len(codepoints), path)


def generate_charsets(output_dir: Path, cache_dir: Path) -> dict[str, Path]:
    """Download Unihan and generate a unified CJK charset file.

    The unified charset is the UNION of common characters across SC, TC, and JP.
    Returns dict of charset name → file path.
    """
    unihan_data = _download_unihan(cache_dir)
    per_locale = _parse_unihan_mappings(unihan_data)

    # Build unified set
    unified: set[int] = set()
    for field_name, label in LOCALE_FIELDS.items():
        locale_cps = per_locale.get(field_name, set())
        log.info("  %s: %d codepoints", label, len(locale_cps))
        unified |= locale_cps

    log.info("Unified CJK: %d unique ideographs (from %d summed across locales)",
             len(unified), sum(len(v) for v in per_locale.values()))

    out_path = output_dir / "cjk_unified.txt"
    header = (
        f"Unified CJK common ideographs — {len(unified)} chars\n"
        f"Union of: GB 2312 (SC) + Big5 Level 1 (TC) + JIS X 0208 (JP)\n"
        f"Generated from Unicode Unihan database"
    )
    save_charset_file(out_path, unified, header)

    return {"cjk_unified": out_path}


def _download_unihan(cache_dir: Path) -> str:
    """Download Unihan.zip and extract Unihan_OtherMappings.txt content."""
    zip_path = cache_dir / "Unihan.zip"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not zip_path.exists():
        log.info("Downloading Unihan database from %s", UNIHAN_URL)
        req = Request(UNIHAN_URL, headers={"User-Agent": "op_fonts/0.1"})
        with urlopen(req, timeout=120) as resp:
            zip_path.write_bytes(resp.read())
        log.info("Saved %s (%.1f KB)", zip_path, zip_path.stat().st_size / 1024)

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(UNIHAN_MAPPINGS_FILE) as f:
            return f.read().decode("utf-8")


def _parse_unihan_mappings(data: str) -> dict[str, set[int]]:
    """Parse Unihan_OtherMappings.txt → {field_name: set of codepoints}."""
    fields_we_want = set(LOCALE_FIELDS.keys())
    result: dict[str, set[int]] = {f: set() for f in fields_we_want}

    for line in data.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        cp_str, field, value = parts[0], parts[1], parts[2]
        if field not in fields_we_want:
            continue
        cp = int(cp_str[2:], 16)

        # Filter Big5 to Level 1 only (常用字, A440-C67E)
        if field == "kBigFive":
            hex_part = value.strip()[:4]
            try:
                big5_code = int(hex_part, 16)
            except ValueError:
                continue
            if big5_code < 0xA440 or big5_code > 0xC67E:
                continue

        result[field].add(cp)

    return result
