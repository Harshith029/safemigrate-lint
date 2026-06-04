# Changelog

All notable changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] — 2026-06-04

No behavior change — same findings as 1.1.0 (golden corpus unchanged).

### Changed

- Performance: the inline-ignore suppressor now finds a statement's line via binary search instead of a per-statement linear scan, and the engine dispatches statements to rules by node type (O(statements × rules) → O(statements)). ~50× faster analysis on large migration sets; linting is now parse-bound.

### Internal

- Consolidated duplicated AST helpers (`table_name`, `qualified_name`, `bare`) into `core/ast_utils.py`, removing ~130 lines of copy-paste.
- Removed stale internal planning references from module/rule docstrings.

## [1.1.0] — 2026-06-04

### Added

- `refresh-matview-blocks-reads` (WARNING) — flags `REFRESH MATERIALIZED VIEW` without `CONCURRENTLY`, which takes AccessExclusiveLock and blocks all reads of the view until the refresh completes. Suppressed when the matview was created earlier in the same migration.
- `StateBuilder` now records `CREATE TABLE AS` / `CREATE MATERIALIZED VIEW` (`CreateTableAsStmt`) objects in `tables_created`, enabling cross-statement suppression for matview rules.
- Distribution: `.pre-commit-hooks.yaml` (use as a pre-commit hook) and PyPI metadata (keywords, classifiers, project URLs).
- Docs: `CONTRIBUTING.md` and `docs/writing-a-rule.md` (rule-authoring walkthrough).

## [1.0.0] — 2026-05-28

Initial public release.

### Action wrapper

- GitHub Action triggered on `pull_request` events with a `paths:` filter
- Inputs: `paths` (required glob / newline list), `severity` (default `critical,warning`), `format` (`json` | `markdown`)
- Outputs: `findings-count` (integer), `has-critical` (`"true"` / `"false"`)
- Posts a find-or-create PR comment with per-finding markdown detail; an HTML marker on the first line keeps re-runs idempotent (edits the existing comment instead of duplicating)
- Creates a Check Run named `safemigrate-lint` with severity-mapped conclusion: `success` (no findings), `neutral` (warnings or style only), `action_required` (any critical)
- Docker container action — `python:3.11-slim` base, `uv sync --frozen` install; cold-start image build under 20s on hosted runners
- All errors actionable (no bare stack traces): missing inputs, glob misses, malformed JSON, GitHub API failures, missing `checks: write` permission, missing `GITHUB_TOKEN`, missing event payload fields

### Lint engine

- 32 safety rules + 6 opt-in style rules, organised in CRITICAL / WARNING / STYLE severity tiers
- Default mode emits CRITICAL + WARNING; STYLE rules are opt-in via `--severity=style` or `.safemigrate.toml`
- Real Postgres parser via [pglast](https://github.com/lelit/pglast) (libpg_query) — handles extension SQL (TimescaleDB, PostGIS) that single-language reimplementations miss
- Cross-statement state machine — suppresses 11 rules' false positives on tables, indexes, and constraints created earlier in the same migration
- Inline suppression: `-- safemigrate:ignore=<rule-id>[,<rule-id>]* reason="..."` on the line preceding the statement
- Configuration via `.safemigrate.toml` walked upward from the first linted file: hard-disable rules under `[rules].disabled`; promote STYLE → WARNING under `[rules.style].enabled`

### Rule highlights vs other OSS linters

Where safemigrate-lint adds coverage Atlas Pro paywalls or squawk doesn't ship:

- `stored-generated-column-rewrites`, `volatile-default-rewrites-table`, `table-logging-mode-rewrites`, `pk-constraint-exclusive-lock`, `unique-constraint-exclusive-lock`, `identity-column-add-rewrites`, `access-method-change-rewrites`, `trigger-add-blocks-writes` — Atlas-equivalent table-rewrite / lock-duration warnings
- `constraint-dropped-warning` — single rule absorbing Atlas CD101 (FK) + CD102 (CHECK) + CD103 (PK) constraint-drop coverage
- `analyzer-blind-on-dynamic-sql` — honest framing for DO blocks / EXECUTE / CREATE FUNCTION bodies the analyzer cannot inspect
- `update-delete-row-scope` — flags unbounded UPDATE/DELETE that risks replication lag

### CLI

- `safemigrate-lint <files>... [--severity=critical,warning,style] [--format=json|markdown]`
- Exit codes: `0` clean, `1` findings present, `2` input error
- JSON output is squawk-compatible for ease of integration diffing
- Markdown output uses GitHub emoji shortcodes — safe on Windows terminals (no Unicode), renders correctly in GitHub PR comments

### Output formats

- JSON: sorted findings array with `rule_id`, `severity`, `file`, `line`, `column`, `message`, `help`, `suggested_fix`
- Markdown: severity-grouped sections with code excerpts, help text, and suggested-fix blocks

[1.1.1]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.1
[1.1.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.0
[1.0.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.0.0
