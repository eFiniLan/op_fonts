"""Language code → script name mapping for openpilot's supported languages."""

from __future__ import annotations

# Maps openpilot language codes to the script name(s) used in [[scripts]] config.
# A language may require multiple scripts (e.g. Japanese needs latin + CJK).
LANG_TO_SCRIPTS: dict[str, list[str]] = {
    "en":     ["latin"],
    "es":     ["latin"],
    "fr":     ["latin"],
    "pt":     ["latin"],
    "pt-BR":  ["latin"],
    "de":     ["latin"],
    "tr":     ["latin"],
    "uk":     ["latin", "cyrillic"],
    "ru":     ["latin", "cyrillic"],
    "th":     ["latin", "thai"],
    "ko":     ["latin", "cjk", "hangul"],
    "ja":     ["latin", "cjk"],
    "zh-CHS": ["latin", "cjk"],
    "zh-CHT": ["latin", "cjk"],
}

# All known script names (superset of what any language needs).
ALL_SCRIPT_NAMES: set[str] = {s for scripts in LANG_TO_SCRIPTS.values() for s in scripts}


def scripts_for_languages(lang_codes: list[str]) -> set[str]:
    """Return the set of script names needed to cover the given language codes."""
    result: set[str] = set()
    unknown: list[str] = []
    for code in lang_codes:
        if code in LANG_TO_SCRIPTS:
            result.update(LANG_TO_SCRIPTS[code])
        else:
            unknown.append(code)
    if unknown:
        import logging
        logging.getLogger(__name__).warning("Unknown language codes (ignored): %s", unknown)
    return result
