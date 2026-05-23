"""CLI entry point: safemigrate-lint <files>... [--severity=...] [--format=...]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.engine import analyze
from .core.finding import DEFAULT_LEVELS, Severity
from .core.parser import parse_file
from .core.reporter import render_json
from .core.state import StateBuilder


def _parse_severity(s: str) -> frozenset[Severity]:
    tokens = {token.strip().lower() for token in s.split(",") if token.strip()}
    valid = {level.value for level in Severity}
    invalid = tokens - valid
    if invalid:
        raise argparse.ArgumentTypeError(
            f"invalid severity values: {sorted(invalid)} (valid: {sorted(valid)})"
        )
    levels = frozenset(Severity(tok) for tok in tokens)
    return levels or DEFAULT_LEVELS


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="safemigrate-lint",
        description="Static analyzer for Postgres migrations.",
    )
    p.add_argument("files", nargs="+", help="SQL files to analyze")
    p.add_argument(
        "--severity",
        type=_parse_severity,
        default=DEFAULT_LEVELS,
        help=(
            "Comma-separated severity levels to include: critical,warning,style "
            "(default: critical,warning)"
        ),
    )
    p.add_argument(
        "--format",
        choices=["json"],  # markdown lands week 2
        default="json",
        help="Output format (default: json)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    all_findings = []
    for file_arg in args.files:
        path = Path(file_arg)
        if not path.exists():
            print(f"safemigrate-lint: file not found: {file_arg}", file=sys.stderr)
            return 2
        result = parse_file(path)
        state = StateBuilder.build(result.statements or [])
        all_findings.extend(analyze(result, state))

    filtered = [f for f in all_findings if f.severity in args.severity]

    if args.format == "json":
        print(render_json(filtered))
    else:
        raise NotImplementedError(args.format)

    return 1 if filtered else 0


if __name__ == "__main__":
    sys.exit(main())
