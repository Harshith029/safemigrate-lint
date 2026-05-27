#!/usr/bin/env python3
"""GitHub Action entrypoint for safemigrate-lint.

Reads INPUT_PATHS / INPUT_SEVERITY / INPUT_FORMAT env vars (set by GitHub
Actions for docker container actions), invokes the safemigrate-lint CLI via
argv (no shell), prints results to the action log, and writes the
findings-count + has-critical outputs to $GITHUB_OUTPUT.

Exit codes propagate the CLI's contract:
  0 — no findings (after severity filter)
  1 — findings present
  2 — input error (missing files, bad args, container misconfiguration)
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys

CLI_NAME = "safemigrate-lint"


def _err(msg: str) -> None:
    print(f"{CLI_NAME}: {msg}", file=sys.stderr)


def _input(name: str, default: str = "") -> str:
    return os.environ.get(f"INPUT_{name.upper()}", default).strip()


def _expand_paths(paths_input: str) -> list[str]:
    # GitHub multi-line inputs arrive as `\n`-separated; allow whitespace too
    # so single-line `migrations/*.sql other/*.sql` also works.
    tokens = [t for t in paths_input.replace("\n", " ").split() if t]
    seen: set[str] = set()
    matched: list[str] = []
    for token in tokens:
        if any(ch in token for ch in "*?["):
            hits = sorted(glob.glob(token, recursive=True))
        else:
            # Pass literals through unconditionally; the CLI's own "file not
            # found" message is the actionable surface for typos.
            hits = [token]
        for hit in hits:
            if hit not in seen:
                seen.add(hit)
                matched.append(hit)
    return matched


def _write_outputs(count: int, has_critical: bool) -> bool:
    """Append findings-count and has-critical to $GITHUB_OUTPUT.
    Returns False on I/O failure so the caller can surface an actionable
    error and abort cleanly instead of emitting a bare stack trace."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        print(
            f"[outputs] findings-count={count} "
            f"has-critical={'true' if has_critical else 'false'}",
            file=sys.stderr,
        )
        return True
    try:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"findings-count={count}\n")
            f.write(f"has-critical={'true' if has_critical else 'false'}\n")
    except OSError as e:
        _err(
            f"could not write to $GITHUB_OUTPUT ({output_file}): {e}. "
            "The runner is expected to provide this file with write access; "
            "if you see this outside CI, set GITHUB_OUTPUT to a writable path "
            "or unset it to log outputs to stderr."
        )
        return False
    return True


def _run_cli(
    files: list[str], severity: str, fmt: str
) -> subprocess.CompletedProcess[str]:
    argv = [CLI_NAME, *files, f"--severity={severity}", f"--format={fmt}"]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def main() -> int:
    if not shutil.which(CLI_NAME):
        _err(
            f"{CLI_NAME} binary not found in PATH. "
            "The Docker image is misconfigured — rebuild the action image."
        )
        return 2

    paths_input = _input("paths")
    if not paths_input:
        _err(
            "no paths provided. Set the `paths` input to a glob "
            "(e.g. 'migrations/*.sql') or a newline-separated list of files."
        )
        return 2

    severity = _input("severity", default="critical,warning")
    fmt = _input("format", default="json")
    if fmt not in ("json", "markdown"):
        _err(f"invalid format `{fmt}`. Allowed values: json, markdown.")
        return 2

    files = _expand_paths(paths_input)
    if not files:
        _err(
            f"no files matched the `paths` input: {paths_input!r}. "
            "Check the glob pattern and that the files exist in the checkout."
        )
        return 2

    # Always run JSON once — we need structured findings to compute outputs.
    json_run = _run_cli(files, severity, "json")
    if json_run.returncode not in (0, 1):
        if json_run.stderr:
            sys.stderr.write(json_run.stderr)
        return json_run.returncode

    try:
        findings = json.loads(json_run.stdout or "[]")
    except json.JSONDecodeError as e:
        _err(
            f"could not parse {CLI_NAME} JSON output: {e}. "
            "This indicates a CLI bug — please file an issue with the SQL fixture."
        )
        return 2

    count = len(findings)
    has_critical = any(f.get("severity") == "critical" for f in findings)
    if not _write_outputs(count, has_critical):
        return 2

    if fmt == "json":
        sys.stdout.write(json_run.stdout)
    else:
        md_run = _run_cli(files, severity, "markdown")
        sys.stdout.write(md_run.stdout)
        if md_run.stderr:
            sys.stderr.write(md_run.stderr)

    return json_run.returncode


if __name__ == "__main__":
    sys.exit(main())
