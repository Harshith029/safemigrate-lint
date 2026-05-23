"""Output reporters.

JSON is the v1 default — structured for CI integration and easy diffing against
squawk's `--reporter=json`. Markdown lands in week 2 for the GitHub Action
wrapper to consume in week 4.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from .finding import Finding


def render_json(findings: Iterable[Finding]) -> str:
    """Serialize findings as a JSON array, sorted for stable output."""
    items = sorted(
        (f.to_dict() for f in findings),
        key=lambda d: (d["file"], d["line"], d["column"], d["rule_id"]),
    )
    return json.dumps(items, indent=2, default=str)
