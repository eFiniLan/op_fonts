"""Extract required Unicode codepoints from openpilot .pot / .ts files."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def extract_from_pot(source: str | Path) -> set[int]:
    """Extract non-ASCII codepoints from msgid strings in a .pot file.

    source can be a local file path or a URL.
    """
    text = _read_source(source)
    codepoints: set[int] = set()

    # Match msgid "..." (including multiline continuation "..." lines)
    in_msgid = False
    for line in text.splitlines():
        if line.startswith("msgid "):
            in_msgid = True
            _extract_from_quoted(line[6:], codepoints)
        elif line.startswith("msgstr "):
            in_msgid = False
        elif in_msgid and line.startswith('"'):
            _extract_from_quoted(line, codepoints)

    # Filter to non-ASCII only
    codepoints = {cp for cp in codepoints if cp > 0x7F}
    log.info("Extracted %d non-ASCII codepoints from .pot", len(codepoints))
    for cp in sorted(codepoints):
        log.debug("  U+%04X  %s", cp, chr(cp))
    return codepoints


def _extract_from_quoted(s: str, out: set[int]) -> None:
    """Extract codepoints from a C-style quoted string."""
    s = s.strip()
    if not s.startswith('"') or not s.endswith('"'):
        return
    s = s[1:-1]
    # Unescape basic C escapes
    s = s.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
    for ch in s:
        out.add(ord(ch))


def _read_source(source: str | Path) -> str:
    """Read from a file path or URL."""
    source_str = str(source)
    if source_str.startswith("http://") or source_str.startswith("https://"):
        log.info("Fetching %s", source_str)
        req = Request(source_str, headers={"User-Agent": "op_fonts/0.1"})
        with urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8")
    return Path(source_str).read_text(encoding="utf-8")
