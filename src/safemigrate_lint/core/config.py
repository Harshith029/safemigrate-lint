"""`.safemigrate.toml` configuration loader.

Schema (all sections optional):

    [rules]
    # Hard-disable these rules — they never fire regardless of --severity.
    disabled = ["bigint-over-int-preferred", "another-rule"]

    [rules.style]
    # Opt-in STYLE rules. By default all STYLE rules are filtered out.
    # Listing an ID here lets it fire in default mode (CRITICAL+WARNING),
    # treating it as if it were a WARNING.
    enabled = ["timestamptz-over-timestamp-preferred"]

The config file is looked up by walking from the migration file's directory
upward to the filesystem root, stopping at the first `.safemigrate.toml`. The
CLI uses the working directory for the lookup (sufficient for most CI setups).

Parsing uses `tomllib` (stdlib in 3.11+).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Config:
    """Parsed config. Defaults if no .safemigrate.toml is found."""

    disabled_rules: frozenset[str] = field(default_factory=frozenset)
    style_enabled: frozenset[str] = field(default_factory=frozenset)


def find_config_file(start: Path) -> Path | None:
    """Walk from `start` (file or directory) upward to find .safemigrate.toml."""
    current = start if start.is_dir() else start.parent
    for ancestor in [current, *current.parents]:
        candidate = ancestor / ".safemigrate.toml"
        if candidate.exists():
            return candidate
    return None


def load_config(start: Path) -> Config:
    """Load config from the nearest `.safemigrate.toml` ancestor of `start`,
    or return defaults if none found."""
    path = find_config_file(start)
    if path is None:
        return Config()
    return parse_config_file(path)


def parse_config_file(path: Path) -> Config:
    """Parse the given .safemigrate.toml. Raises ValueError on malformed input."""
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    rules_section = data.get("rules", {})
    if not isinstance(rules_section, dict):
        raise ValueError(f"{path}: [rules] must be a table")

    disabled = rules_section.get("disabled", [])
    if not isinstance(disabled, list) or not all(isinstance(x, str) for x in disabled):
        raise ValueError(f"{path}: [rules].disabled must be a list of strings")

    style_section = rules_section.get("style", {})
    if not isinstance(style_section, dict):
        raise ValueError(f"{path}: [rules.style] must be a table")
    style_enabled = style_section.get("enabled", [])
    if not isinstance(style_enabled, list) or not all(isinstance(x, str) for x in style_enabled):
        raise ValueError(f"{path}: [rules.style].enabled must be a list of strings")

    return Config(
        disabled_rules=frozenset(disabled),
        style_enabled=frozenset(style_enabled),
    )
