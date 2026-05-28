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
import urllib.error
import urllib.request

CLI_NAME = "safemigrate-lint"

# First-line HTML marker that identifies this action's PR comment so re-runs
# edit the existing comment instead of posting a duplicate. Invisible in the
# rendered view; detectable via the issues-comments API.
COMMENT_MARKER = "<!-- safemigrate-lint:comment-id -->"

# Check Run name shown in the PR's checks list. Tied to head_sha per run, so
# each push gets a fresh check (no find-or-create needed).
CHECK_RUN_NAME = "safemigrate-lint"


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


def _api_request(
    method: str, url: str, token: str, body: dict | None = None
) -> tuple[int, object, str]:
    """Make a GitHub REST API request via stdlib urllib (no new deps).
    Returns (status, parsed_body_or_None, raw_text). On a transport-level
    failure (DNS, connection refused, timeout) returns (0, None, message)
    rather than raising."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "safemigrate-lint-action",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else None
            return resp.status, parsed, raw
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        return e.code, None, raw
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, None, f"network error: {e}"


def _pr_context() -> dict | None:
    """Detect whether this run is in a pull_request event with enough context
    to post a comment. Returns {token, owner, repo, pr_number} or None.
    Emits actionable stderr messages when the event is a PR but a piece is
    missing — silent skip would hide misconfiguration."""
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    if event_name != "pull_request":
        return None

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()

    if not token:
        _err(
            "GITHUB_TOKEN is not set; cannot post PR comment. Add "
            "`permissions: { pull-requests: write }` to the workflow job so "
            "GitHub Actions injects the token, then pass it via "
            "`env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }`."
        )
        return None
    if not repo or "/" not in repo:
        _err("GITHUB_REPOSITORY is missing or malformed; cannot post PR comment.")
        return None
    if not event_path or not os.path.exists(event_path):
        _err("GITHUB_EVENT_PATH is missing; cannot determine PR number.")
        return None

    try:
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _err(f"could not read GitHub event payload at {event_path}: {e}.")
        return None

    pr = event.get("pull_request") or {}
    pr_number = pr.get("number")
    if not isinstance(pr_number, int):
        _err("pull_request.number not found in event payload; skipping comment.")
        return None

    # head.sha is optional in this context — comment posting doesn't need it;
    # only Check Run creation does. The Check Run path surfaces its own error
    # when this is missing so the comment can still go through.
    head_sha = pr.get("head", {}).get("sha")
    if not isinstance(head_sha, str) or not head_sha:
        head_sha = None

    owner, name = repo.split("/", 1)
    return {
        "token": token,
        "owner": owner,
        "repo": name,
        "pr_number": pr_number,
        "head_sha": head_sha,
    }


def _find_existing_comment(ctx: dict) -> int | None:
    """Walk pages of the PR's comments looking for one whose body begins with
    COMMENT_MARKER. Returns the comment id, or None if not found / on error."""
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{ctx['owner']}/{ctx['repo']}"
            f"/issues/{ctx['pr_number']}/comments?per_page=100&page={page}"
        )
        status, comments, raw = _api_request("GET", url, ctx["token"])
        if status != 200 or not isinstance(comments, list):
            _err(
                f"GitHub API returned {status} listing comments on PR "
                f"#{ctx['pr_number']}: {raw[:200]}"
            )
            return None
        for c in comments:
            body = c.get("body", "") or ""
            if body.startswith(COMMENT_MARKER):
                return int(c["id"])
        if len(comments) < 100:
            return None
        page += 1


def _post_or_edit_comment(ctx: dict, body_md: str) -> None:
    """Find-or-create the marker-tagged comment on the PR. API failures are
    logged to stderr but do not change the action's exit code — the lint
    result stays the actionable signal even if commenting fails."""
    body_with_marker = f"{COMMENT_MARKER}\n{body_md}"
    existing = _find_existing_comment(ctx)
    if existing is not None:
        url = (
            f"https://api.github.com/repos/{ctx['owner']}/{ctx['repo']}"
            f"/issues/comments/{existing}"
        )
        status, _, raw = _api_request(
            "PATCH", url, ctx["token"], {"body": body_with_marker}
        )
        if status in (200, 201):
            print(f"[comment] edited existing comment #{existing}", file=sys.stderr)
        else:
            _err(
                f"GitHub API returned {status} editing comment "
                f"#{existing}: {raw[:200]}"
            )
        return

    url = (
        f"https://api.github.com/repos/{ctx['owner']}/{ctx['repo']}"
        f"/issues/{ctx['pr_number']}/comments"
    )
    status, parsed, raw = _api_request(
        "POST", url, ctx["token"], {"body": body_with_marker}
    )
    if status in (200, 201):
        new_id = parsed.get("id") if isinstance(parsed, dict) else "?"
        print(f"[comment] posted new comment #{new_id}", file=sys.stderr)
    else:
        _err(
            f"GitHub API returned {status} posting comment on PR "
            f"#{ctx['pr_number']}: {raw[:200]}"
        )


def _check_run_conclusion(count: int, has_critical: bool) -> str:
    """Map lint severity to a GitHub Check Run conclusion.

      0 findings           -> success
      any critical         -> action_required  (semantic "please look at this")
      warnings/style only  -> neutral          (surfaced but non-blocking)
    """
    if count == 0:
        return "success"
    if has_critical:
        return "action_required"
    return "neutral"


def _check_run_output(count: int, findings: list[dict]) -> dict:
    """Build the Check Run's {title, summary} object — concise, since the PR
    comment carries the per-finding detail."""
    if count == 0:
        return {
            "title": "No findings",
            "summary": "No migration safety findings. :white_check_mark:",
        }
    by_sev = {"critical": 0, "warning": 0, "style": 0}
    for f in findings:
        sev = f.get("severity")
        if sev in by_sev:
            by_sev[sev] += 1
    parts = [f"{n} {s}" for s, n in by_sev.items() if n]
    breakdown = ", ".join(parts) or f"{count} findings"
    return {
        "title": f"{count} findings — {breakdown}",
        "summary": (
            f"**{count} migration safety findings** — {breakdown}.\n\n"
            "See the PR conversation for per-finding detail."
        ),
    }


def _create_check_run(
    ctx: dict, count: int, has_critical: bool, findings: list[dict]
) -> None:
    """POST a Check Run for the PR head commit. Logs to stderr; API failures
    do not change the action's exit code. 403 gets a specific actionable
    message since missing `checks: write` permission is the common cause."""
    head_sha = ctx.get("head_sha")
    if not head_sha:
        _err(
            "pull_request.head.sha not found in event payload; "
            "skipping Check Run creation."
        )
        return

    body = {
        "name": CHECK_RUN_NAME,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": _check_run_conclusion(count, has_critical),
        "output": _check_run_output(count, findings),
    }
    url = f"https://api.github.com/repos/{ctx['owner']}/{ctx['repo']}/check-runs"
    status, parsed, raw = _api_request("POST", url, ctx["token"], body)
    if status in (200, 201):
        cr_id = parsed.get("id") if isinstance(parsed, dict) else "?"
        print(
            f"[check-run] created #{cr_id} (conclusion={body['conclusion']})",
            file=sys.stderr,
        )
    elif status == 403:
        _err(
            "GitHub API returned 403 creating Check Run — the workflow job "
            "needs `permissions: { checks: write }` (in addition to "
            "`pull-requests: write` for the comment). "
            f"Response: {raw[:200]}"
        )
    else:
        _err(
            f"GitHub API returned {status} creating Check Run for "
            f"{head_sha[:7]}: {raw[:200]}"
        )


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

    # Detect whether this run can post a PR comment.
    ctx = _pr_context()

    # Render markdown once if we need it — either for the action log display
    # or for the PR comment body. Avoids a third CLI invocation.
    md_body: str | None = None
    if fmt == "markdown" or ctx is not None:
        md_run = _run_cli(files, severity, "markdown")
        md_body = md_run.stdout
        if md_run.stderr:
            sys.stderr.write(md_run.stderr)

    if fmt == "json":
        sys.stdout.write(json_run.stdout)
    else:
        sys.stdout.write(md_body or "")

    if ctx is not None:
        if md_body is not None:
            _post_or_edit_comment(ctx, md_body)
        _create_check_run(ctx, count, has_critical, findings)

    return json_run.returncode


if __name__ == "__main__":
    sys.exit(main())
