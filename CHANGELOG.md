# Changelog

All notable changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] — 2026-08-13

Correctness fixes from an external audit. Each was reproduced against the code
before being changed.

### Fixed

- **`DEFAULT now()` no longer flagged as a table rewrite.** `now()` is STABLE,
  not VOLATILE — it returns one value per transaction, so
  `ADD COLUMN ... DEFAULT now()` takes the PG11 metadata-only fast path and does
  not rewrite. The Postgres ALTER TABLE docs use it as the worked example of a
  default that doesn't rewrite. This was a CRITICAL false positive on one of the
  most common migrations there is. `clock_timestamp()`, `random()`,
  `gen_random_uuid()` and friends still fire — those really do advance per row.
- **Foreign-key lock impact corrected.** Adding a `FOREIGN KEY` takes
  `SHARE ROW EXCLUSIVE` on both the referencing and referenced table, not
  `ACCESS EXCLUSIVE`. Lock impact was keyed by rule id, so one rule covering both
  FK and CHECK had to be wrong for one of them — and it was wrong for FK, the
  more common case, overstating the lock and omitting the referenced table.
  Findings now carry their own impact where the lock depends on the statement.
- **Markdown injection in the PR comment.** Identifiers and literals reached the
  comment as raw Markdown, letting a contributor render arbitrary headings and
  links in a comment posted under the repo bot's identity. Prose is now escaped
  and flattened to one line; fenced blocks use a fence longer than any backtick
  run in their content. Underscores are intentionally not escaped — intraword
  underscores don't start emphasis, and escaping them would mangle every SQL
  identifier.

### Internal

- `tests/unit/test_audit_regressions.py` approaches the rules from the opposite
  side to the existing suites: cases where the tool must **not** fire, and
  hazards that must not be suppressed. The `now()` fix changed no golden file —
  the corpus never exercised `DEFAULT now()`, which is exactly how the bug
  survived 130 green tests.

### Known issues

Five confirmed bugs share one root cause and are pinned as `xfail(strict=True)`
so they fail the suite the moment they are fixed. `StateBuilder` computes
whole-file state before any statement is analyzed, so rules read "created
anywhere in the file" as "created earlier, in this schema, and still empty" —
three distinct claims, none proven. Consequences:

- A `DROP INDEX` is suppressed by a `CREATE INDEX` of the same name **later** in
  the file.
- `CREATE TABLE audit.users` suppresses a hazard on an existing `public.users`.
- A table created and then populated in the same file is still treated as empty.
- `BEGIN; BEGIN; COMMIT;` reports uncommitted transactions; Postgres treats a
  repeated `BEGIN` as a no-op warning.
- `CREATE INDEX CONCURRENTLY` is flagged when a `BEGIN` appears anywhere in the
  file, even after it.

Fixing these needs ordered, schema-aware state and is the next piece of work.

## [1.2.0] — 2026-07-26

### Added

- **Lock impact on findings.** Each finding whose concern is a lock now carries a
  `lock_impact` object: the lock mode the operation acquires, how long it's held,
  what it blocks, and an optional note naming the safe alternative. Derived
  statically from the Postgres documentation — no database connection involved.
  25 of the 39 rules carry one.
- JSON output gains an optional `lock_impact` key. The field is **omitted** (not
  `null`) on findings without one, so existing consumers that read the documented
  keys are unaffected.
- Markdown output gains a per-finding lock line and a "Heaviest lock" summary at
  the top of the report, so a reviewer sees the migration's worst lock without
  reading every finding.

The other 14 rules are left unannotated on purpose, not as a gap: style and
type-choice rules, correctness rules (duplicate index columns, enum value
ordering), dynamic SQL (unknowable by definition), rules that take no table lock
(`DROP DATABASE`, transaction nesting, uncommitted transaction), and
`index-concurrent-in-transaction-banned` — Postgres rejects that statement before
it acquires anything.

### Internal

- `core/lock_impact.py` holds the rule-id → lock table, with tests pinning that
  every entry names a registered rule and a real Postgres lock mode — a typo or a
  renamed rule now fails CI instead of silently mis-ranking a lock.
- Reporter lock ranking uses the full Postgres lock-strength ordering.
- Test suite grew from 69 to 130 tests.

## [1.1.3] — 2026-06-05

### Fixed

- Inline `-- safemigrate:ignore=<rule>` suppression now works on a statement that **follows another statement**. pglast reports a statement's location at the end of the previous statement, so the scanner looked at the wrong line and missed an ignore comment placed between two statements — the finding fired anyway. Suppression on the first statement was unaffected. The feature now has end-to-end + unit test coverage (it was previously untested).
- README: pre-commit example pins `rev: v1.1.2` (was the stale `v1.0.0`).

### Added

- README: a "Linting only the migrations a PR changed" workflow that scopes the action to the PR diff (pure `git`, no third-party action) — the recommended setup for repos with existing migration history, so PRs aren't re-flagged on already-shipped migrations.

## [1.1.2] — 2026-06-05

Build, test, and docs hardening. No change to rule behavior — findings are identical to 1.1.1.

### Changed

- Docker action image pins uv to 0.11.16 (was `:latest`) so a tagged action version always builds reproducibly.

### Internal

- 100% rule test coverage: every rule has a per-rule trigger test, plus a guard that fails CI if a new rule ships without one (was ~15 of 39 rules covered).
- CI now runs the suite on Python 3.11, 3.12, and 3.13 (matches the package's supported-version claim).

### Docs

- README: PR-comment demo, PyPI version badge, and a verified head-to-head benchmark (squawk 2.56.0, both default config: 205 findings vs 39 on the fixture corpus).

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

[1.2.1]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.2.1
[1.2.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.2.0
[1.1.3]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.3
[1.1.2]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.2
[1.1.1]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.1
[1.1.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.0
[1.0.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.0.0
