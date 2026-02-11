"""CLI entry point for op_fonts."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .pipeline import build, build_all, dry_run


def _find_config() -> Path:
    """Look for op_fonts.toml in common locations."""
    candidates = [
        Path("op_fonts.toml"),
        Path(__file__).resolve().parent.parent / "op_fonts.toml",
    ]
    for p in candidates:
        if p.exists():
            return p
    print("Error: op_fonts.toml not found", file=sys.stderr)
    sys.exit(1)


def _list_scripts(config_path: Path) -> None:
    config = load_config(config_path)
    print(f"Available scripts ({len(config.scripts)}):")
    for s in config.scripts:
        status = "enabled" if s.enabled else "disabled"
        charset_tag = f" charset={s.charset_file}" if s.charset_file else ""
        print(f"  {s.name:<12} {status:<10} {s.font}{charset_tag}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="op_fonts",
        description="Minimal font builder for openpilot",
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="Path to op_fonts.toml (default: auto-detect)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show build plan without executing",
    )
    parser.add_argument(
        "--list-scripts",
        action="store_true",
        help="List available scripts and exit",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="count",
        default=1,
        help="Increase verbosity (-v for INFO, -vv for DEBUG)",
    )

    args = parser.parse_args(argv)

    # Logging setup
    level = logging.WARNING
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose >= 1:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    config_path = args.config or _find_config()

    if args.list_scripts:
        _list_scripts(config_path)
        return

    config = load_config(config_path)

    if args.dry_run:
        dry_run(config)
        return

    if config.weights:
        outputs = build_all(config)
        for output in outputs:
            print(f"Built: {output} ({output.stat().st_size / 1024:.1f} KB)")
    else:
        output = build(config)
        print(f"Built: {output} ({output.stat().st_size / 1024:.1f} KB)")
