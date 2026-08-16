"""CLI entry point: safemigrate-lint <files>... [--severity=...] [--format=...]."""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

from .core.config import load_config
from .core.engine import analyze
from .core.finding import DEFAULT_LEVELS, Finding, Severity
from .core.parser import parse_file
from .core.reporter import render_json, render_markdown
from .core.state import StateBuilder
from .rules import RULES


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
        choices=["json", "markdown"],
        default="json",
        help="Output format (default: json)",
    )
    return p


def _fail(message: str) -> int:
    """Report an operational problem and return the input-error exit code.

    Exit 2 is reserved for "the tool could not do its job". Exit 1 means the
    tool ran and found something. Keeping them distinct matters: the Action
    treats 1 as a normal result, so letting a config or read error escape as a
    traceback (exit 1) reported broken input as a clean lint.
    """
    print(f"safemigrate-lint: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Find .safemigrate.toml relative to the first file argument (typical usage),
    # falling back to CWD. The lookup walks upward; rules from this config apply
    # to every file in this run.
    first_path = Path(args.files[0]) if args.files else Path.cwd()
    try:
        config = load_config(first_path)
    except tomllib.TOMLDecodeError as exc:
        return _fail(f"invalid TOML in .safemigrate.toml: {exc}")
    except ValueError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(f"could not read .safemigrate.toml: {exc}")

    unknown = config.disabled_rules | config.style_enabled
    unknown -= set(RULES)
    if unknown:
        return _fail(
            f".safemigrate.toml names unknown rule(s): {', '.join(sorted(unknown))}. "
            f"A typo here silently does nothing, so it's rejected rather than ignored."
        )

    all_findings: list[Finding] = []
    source_by_file: dict[str, str] = {}
    for file_arg in args.files:
        path = Path(file_arg)
        if path.is_dir():
            return _fail(f"{file_arg} is a directory; pass SQL files or a glob")
        if not path.exists():
            return _fail(f"file not found: {file_arg}")
        try:
            result = parse_file(path)
        except UnicodeDecodeError:
            return _fail(f"{file_arg} is not valid UTF-8")
        except OSError as exc:
            return _fail(f"could not read {file_arg}: {exc}")
        source_by_file[result.file] = result.sql
        state = StateBuilder.build(result.statements or [])
        all_findings.extend(analyze(result, state))

    # Apply config: hard-disable rules from [rules].disabled.
    if config.disabled_rules:
        all_findings = [f for f in all_findings if f.rule_id not in config.disabled_rules]

    # Severity filter: build the effective level set. STYLE rules listed in
    # [rules.style].enabled are treated as WARNING-tier (so they show in
    # default mode without --severity=style).
    levels = args.severity
    if config.style_enabled:
        # Promote configured-enabled STYLE findings into the effective set.
        all_findings = [
            _maybe_promote_style(f, config.style_enabled) for f in all_findings
        ]

    filtered = [f for f in all_findings if f.severity in levels]

    if args.format == "json":
        print(render_json(filtered))
    elif args.format == "markdown":
        print(render_markdown(filtered, source_by_file))
    else:
        raise NotImplementedError(args.format)

    return 1 if filtered else 0


def _maybe_promote_style(f: Finding, enabled_ids: frozenset[str]) -> Finding:
    """If `f` is a STYLE finding for an opt-in rule, present it as WARNING so
    the severity filter includes it in default mode. We do NOT mutate the
    severity in JSON output (audit trail intent); we promote a copy."""
    if f.severity != Severity.STYLE or f.rule_id not in enabled_ids:
        return f
    # `replace` rather than rebuilding field by field: the hand-written version
    # silently dropped every field added after it was written (lock_impact went
    # missing from promoted findings this way).
    return replace(f, severity=Severity.WARNING)


if __name__ == "__main__":
    sys.exit(main())
