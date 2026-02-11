# op_fonts

Font builder for [openpilot](https://github.com/commaai/openpilot). Produces optimized multi-language OTF fonts by merging [IBM Plex Sans](https://github.com/IBM/plex) with [Noto Symbols](https://github.com/notofonts/notofonts.github.io), subsetting to only the glyphs needed for openpilot's UI.

**Output:** ~1.9 MB per weight (Regular, Medium, SemiBold, Bold)

## Why

openpilot's UI supports 5+ languages including CJK, Thai, and Cyrillic. A naive approach — bundling full CJK fonts — would be 20+ MB per weight. Most of those glyphs are never used on the UI.

This tool solves three problems:

1. **Size**: Full IBM Plex Sans CJK fonts contain 30,000+ glyphs each. openpilot's UI only needs the characters that actually appear in translations. We subset to official government standard character lists (3,500 SC + 4,808 TC + 2,136 JA + 2,350 KO Hangul), which cover all characters used in openpilot's `.po` translation files with room to spare. CJK scripts are deduplicated across languages — SC goes first, TC only adds traditional-only characters, JA only adds remaining kanji — so shared ideographs aren't stored twice.

2. **Unused features**: CJK fonts ship with thousands of alternate glyphs for stylistic sets, vertical writing, width variants, and legacy encoding forms (jp78, jp83, jp90, etc.). None of these are needed for openpilot's UI, which rasterizes fonts into BMFont atlases at build time. The pipeline drops all OpenType layout tables (GSUB/GPOS/GDEF) and their associated alternate glyphs entirely.

3. **Metric consistency**: openpilot's primary font is Inter. IBM Plex Sans has a slightly smaller cap-height ratio (0.698 vs Inter's 0.727), so text rendered in OpFont would appear ~4% shorter than Inter at the same font size. The pipeline scales all glyphs by 1.042x and sets ascender/descender to match Inter's proportions, so switching between Latin and CJK text doesn't cause visible size jumps.

## Language coverage

| Script | Source | Characters | Standard |
|--------|--------|-----------|----------|
| Latin + IPA + Vietnamese | IBM Plex Sans | Full ranges | — |
| Cyrillic | IBM Plex Sans | U+0400–052F | — |
| Thai | IBM Plex Sans Thai | U+0E00–0E7F | — |
| Simplified Chinese | IBM Plex Sans SC | 3,500 | [Tongyong Guifan L1](https://www.gov.cn/zwgk/2013-08/19/content_2469793.htm) (PRC State Council, 2013) |
| Traditional Chinese | IBM Plex Sans TC | 4,808 | [MOE Edu Standard 1](https://www.cns11643.gov.tw/) (Taiwan MOE, 1982/2004) |
| Japanese | IBM Plex Sans JP | 2,136 | [Joyo Kanji](https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/pdf/joyokanjihyo_20101130.pdf) (Cabinet of Japan, 2010) |
| Korean Hangul | IBM Plex Sans KR | 2,350 | [KS X 1001](https://standard.go.kr/) (KATS, 1987) |
| Symbols | Noto Sans Symbols 1 & 2 | Arrows, math, geometric, dingbats | — |

CJK scripts are merged with pipeline deduplication: SC first, TC fills traditional-only gaps, JA fills remaining. Union: ~6,318 unique CJK ideographs + punctuation/kana/fullwidth ranges.

## Requirements

- Python 3.11+
- [fonttools](https://github.com/fonttools/fonttools) >= 4.47
- [cffsubr](https://github.com/adobe-type-tools/cffsubr) >= 0.4.0

## Usage

```bash
# install
cd op_fonts
uv sync  # or: pip install -e .

# build all weights (Regular, Medium, SemiBold, Bold)
op-fonts

# dry run — show build plan without downloading or building
op-fonts --dry-run

# extra verbose output
op-fonts -vv    # DEBUG
```

### CLI options

```
op-fonts [options]

Options:
  -c, --config PATH           Path to config TOML (auto-detects if omitted)
  -v, --verbose               Increase verbosity (default: INFO, -vv for DEBUG)
  --dry-run                   Show build plan, don't execute
  --list-scripts              List configured scripts and exit
```

## Build pipeline

```
1. Download    Fetch source fonts from URLs in config (cached in ./cache/)
2. Subset      Extract only needed codepoints per script, deduplicate across scripts
3. Merge       Convert outlines to common format, normalize UPM, merge into single font
4. Drop tables  Remove OpenType layout tables (GSUB/GPOS/GDEF) not needed for BMFont rasterization
5. Finalize    Scale glyphs to match target metrics, set metadata, CFF subroutinize
```

## Configuration

Build is driven by `op_fonts.toml`. Key sections:

```toml
[font]
name = "OpFont"
output_dir = "dist"
copyright = "Copyright 2017 IBM Corp. ..."
designer = "Rick Lan"
ascender = 969              # optional; 0 or omit to keep source font's values
descender = -242            # optional; 0 or omit to keep source font's values
target_cap_ratio = 0.7273   # optional; 0 or omit to auto-match first script

[font.weight_values]
Regular = 400
Medium = 500
SemiBold = 600
Bold = 700

[[scripts]]
name = "cjk-sc"
font = "IBMPlexSansSC-Regular.otf"
url = "https://raw.githubusercontent.com/IBM/plex/master/packages/plex-sans-sc/fonts/complete/otf/hinted/IBMPlexSansSC-Regular.otf"
charset_file = "charsets/sc_tongyong_l1.txt"
unicode_ranges = ["U+3000-303F", ...]

[[scripts]]
name = "symbols"
font = "NotoSansSymbols-Regular.ttf"
url = "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts/NotoSansSymbols/hinted/ttf/NotoSansSymbols-Regular.ttf"
unicode_ranges = ["U+2190-21FF", ...]

[merge]
drop_tables = ["MATH", "meta", "vhea", "vmtx", "GSUB", "GPOS", "GDEF"]
keep_features = []  # BMFont rasterization doesn't need OpenType layout features
```

Each script specifies its own `url`, so you can mix fonts from different sources (e.g. IBM Plex for Latin, Noto Sans for CJK).

Scripts are merged in config order. Each script can specify `unicode_ranges`, a `charset_file` (for CJK ideographs), or both.

## Charset files

Government-standard character lists in `charsets/`, one character per line (UTF-8):

| File | Characters | Source |
|------|-----------|--------|
| `sc_tongyong_l1.txt` | 3,500 | PRC State Council, 2013 |
| `tc_edu_standard_1.txt` | 4,808 | Taiwan MOE, verified against CNS11643 open data |
| `ja_joyo_kanji.txt` | 2,136 | Japanese Cabinet, 2010 |
| `ko_ksx1001_hangul.txt` | 2,350 | Generated from Python `euc-kr` codec (KS X 1001) |

## Adding missing CJK characters

If a translation uses a character not in the standard charset (e.g. a rare kanji), add it to the appropriate charset file in `charsets/`:

```bash
# append the character on a new line
echo '鑫' >> charsets/sc_tongyong_l1.txt
```

Then rebuild. The pipeline will include the new character automatically. Duplicates across charset files are handled by dedup — no need to check other files.

## Adding a new language

Two cases:

### Language uses an existing script (Latin, Cyrillic, Thai, CJK, Hangul)

Nothing to do — the script is already in the TOML and will be included in the build.

### Language needs a new script (e.g. Arabic, Hebrew, Devanagari)

1. Find the font source. IBM Plex Sans covers [many scripts](https://github.com/IBM/plex). If not available, use [Noto Sans](https://github.com/notofonts/notofonts.github.io).

2. Add a `[[scripts]]` entry in `op_fonts.toml`:

```toml
[[scripts]]
name = "arabic"
font = "IBMPlexSansArabic-Regular.otf"
url = "https://raw.githubusercontent.com/IBM/plex/master/packages/plex-sans-arabic/fonts/complete/otf/IBMPlexSansArabic-Regular.otf"
unicode_ranges = [
    "U+0600-06FF",   # Arabic
    "U+0750-077F",   # Arabic Supplement
    "U+FB50-FDFF",   # Arabic Presentation Forms-A
    "U+FE70-FEFF",   # Arabic Presentation Forms-B
]
```

3. Rebuild with `op-fonts -v`.

## Output

```
dist/
  OpFont-Regular.otf     ~1.9 MB
  OpFont-Medium.otf      ~1.9 MB
  OpFont-SemiBold.otf    ~1.9 MB
  OpFont-Bold.otf        ~1.9 MB
```

OTF with CFF outlines, UPM 1000, metrics matched to Inter (ascender=969, descender=-242).

## License

Font sources are licensed under the [SIL Open Font License 1.1](https://scripts.sil.org/OFL):
- [IBM Plex](https://github.com/IBM/plex/blob/master/LICENSE.md)
- [Noto Fonts](https://github.com/notofonts/notofonts.github.io/blob/main/LICENSE)
