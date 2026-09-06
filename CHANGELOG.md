# Changelog

All notable changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`not-null-dropped-warning`** — `ALTER COLUMN … DROP NOT NULL`. The statement
  is catalog-only and instant; the cost is that readers which treated the column
  as always-present can start receiving nulls, and restoring the constraint later
  needs a full scan that fails if any null arrived meanwhile. WARNING: visible in
  the diff and deliberate.
- **`identifier-too-long`** — names over Postgres's 63-byte `NAMEDATALEN` limit.
  Postgres doesn't reject them, it silently truncates, so the object exists under
  a name the migration never wrote: a later reference to the full name misses,
  and two names sharing a 63-byte prefix collide.

  Detecting this needed a detour worth recording. libpg_query applies the
  truncation itself while parsing, so by the time a rule sees the AST the
  evidence is gone — a real cost of using Postgres's own parser. The check reads
  the raw SQL instead: a name arriving at exactly the limit, with more identifier
  characters after it in the source, was truncated. A legitimately 63-byte name
  has nothing following and is left alone.

  Both rules came from measuring recall rather than guessing. Running this and
  squawk over 2,497 real migrations (cal.com, Mattermost, Supabase, Windmill)
  showed only **0.38% of squawk's 13,951 findings** fell in a category with no
  equivalent here — these two were that gap. Verified against the same corpus
  afterwards: `DROP NOT NULL` now matches squawk on all 29 files.

  `identifier-too-long` matches on 2 of squawk's 3 files by design. The third
  contains only `DROP FUNCTION IF EXISTS <long name>` — a *reference*, which
  truncates to exactly the 63 bytes the CREATE stored and therefore resolves to
  the right object. Flagging it would be noise, so only names a statement
  **creates** are checked.

### Changed

- **CRITICAL now means "a production incident the diff doesn't reveal."**
  `drop-column-restricted`, `drop-table-restricted`,
  `add-non-nullable-without-default` and `index-concurrent-in-transaction-banned`
  move to WARNING. Nothing stops being reported — the same findings appear, at a
  tier that reflects what they are.

  Measured, not guessed: running 1.3.0 over 2,497 real migrations from cal.com,
  Mattermost, Supabase and Windmill produced 1,370 CRITICAL findings, **89% of
  them "you dropped something"** (967 DROP COLUMN, 252 DROP TABLE). Among them
  cal.com dropping its own `old_startTime` and `old_periodType` columns — the
  correct final step of the expand-contract pattern this tool recommends
  elsewhere. A gate that blocks 1,370 times on 2,497 migrations, mostly on
  deliberate cleanup, doesn't get read; it gets `continue-on-error: true`.

  After the change: **138 CRITICAL, one per 18 migrations**, and every one of
  them a rewrite or a lock the SQL doesn't look like it takes. Statements that
  merely *fail* at deploy also move down — a failed migration is self-limiting,
  unlike an outage.

  `drop-database-restricted` and `truncate-cascade-banned` stay CRITICAL:
  the first is never legitimate in a migration, and CASCADE's reach into tables
  the statement doesn't name is exactly the kind of cost a diff hides.

## [1.3.0] — 2026-08-16

Completes the response to the external audit. **Default output changes** — see
Changed below before upgrading a pipeline that gates on finding counts. Every
finding was reproduced against the code before being acted on; two turned out to
be overstated and are noted at the end.

### Fixed

- **Cross-statement suppression no longer hides real hazards.** `StateBuilder`
  computed whole-file state before any statement was analyzed, so rules read
  "created anywhere in this file" as "created earlier, in this schema, and still
  empty" — three claims, none proven. The headline false-positive reducer was
  producing false negatives, the worse failure for a tool that gates a merge.
  State is now advanced statement by statement, so a rule sees only what
  happened strictly before the statement it judges. Concretely:
  - a `DROP INDEX` is no longer excused by a `CREATE INDEX` of the same name
    **later** in the file;
  - `CREATE TABLE audit.users` no longer vouches for an existing
    `public.users` — relations are matched on schema-qualified identity, and an
    unqualified name is left unresolved when the migration has claimed that bare
    name in another schema;
  - a table created and then populated in the same migration is no longer
    treated as empty, and `CREATE TABLE AS` counts as populated on creation
    (`WITH NO DATA` does not).
- **Transaction semantics match Postgres.** A repeated `BEGIN` is a warning that
  leaves the transaction alone, not a nested one. The old depth counter made the
  following `COMMIT` look like it left a transaction open, and let a `BEGIN`
  anywhere in the file condemn a `CREATE INDEX CONCURRENTLY` that ran before it.
- **`timestamptz-over-timestamp-preferred` described the types backwards.**
  `timestamp without time zone` returns the wall-clock fields it was given and
  converts nothing; `timestamptz` is the type tied to the session timezone. The
  real issue is ambiguity — the same reading denotes different moments in
  different zones and repeats across a DST fall-back.
- **Batched `UPDATE`/`DELETE` guidance was not executable and not sound.** It
  emitted `UPDATE FROM t`, which is invalid in every Postgres version, inside a
  `DO` block — which runs in a single transaction and so relieves none of the
  lock or replication pressure the rule warns about. It now emits one chunk to
  be driven by the caller, stating the transaction-boundary requirement.
  `FOREIGN KEY` and `CHECK` also get correct, separate templates.
- **CLI operational errors exit 2.** Malformed `.safemigrate.toml`, an
  unreadable or non-UTF-8 file, and directory arguments raised tracebacks and
  exited 1 — which the Action treats as "ran fine, found something", reporting
  broken input as a clean lint. Unknown rule ids in config are now rejected
  rather than silently doing nothing.
- Style promotion (`[rules.style].enabled`) rebuilt findings field by field and
  dropped `lock_impact`, added in 1.2.0.
- **PR comment integrity.** The marker identifying the bot's own comment is
  plain text, so anyone able to comment could post one; the Action would then
  try to edit a comment its token doesn't own, and the report never appeared.
  The author must now be a Bot, and a failed edit falls back to posting fresh.
- Comments over GitHub's 65536-character limit were rejected outright, so the
  largest reports were the ones that went missing. They are truncated at a
  finding boundary instead, never leaving an open code fence.

### Changed

- `timestamptz-over-timestamp-preferred` returns to **opt-in STYLE**. Its
  promotion to WARNING rested on the incorrect semantics above, and static
  analysis cannot tell an instant from a civil time — `timestamp` is the right
  type for a birthday or a 09:00 local appointment. This removes 11 findings
  from the fixture corpus. Re-enable with `--severity=style` or:

      [rules.style]
      enabled = ["timestamptz-over-timestamp-preferred"]

- Rule tiers are now 32 safety + 7 opt-in style (was 33 + 6).
- The action image runs as a non-root user and pins its base by digest;
  workflow actions are pinned by commit SHA.

### Added

- `POSTGRES_GRAMMAR_VERSION` and a documented supported-version matrix. pglast
  vendors one libpg_query, so this accepts **Postgres 17** syntax — PG18 virtual
  generated columns are a genuine gap, reported as a syntax error rather than
  quietly mishandled. A test pins the major so a dependency bump can't move it.
- CI builds the Docker action image and lints through the real entrypoint,
  asserting non-root and both exit codes. Nothing previously built the artifact
  users actually run.
- `workflow_dispatch` on the publish workflow: a dry run that builds and checks
  artifacts without uploading, and a way to re-drive a failed upload without
  deleting and re-pushing a published tag.

### Internal

- Tests 130 → 212, across new CLI-error, Action-comment, suggested-fix,
  parser-version, and state-semantics suites. The state suite pins both
  directions: suppression that must keep working, and suppression that must not
  apply. The `now()` fix in 1.2.1 changed no golden file — no fixture exercised
  `DEFAULT now()`, which is how the bug survived 130 green tests.
- Every `suggested_fix` is now parsed (after placeholder substitution) by a test.

### Where the audit was wrong

Recorded because both claims are plausible and would mislead a future reader.
The `now()` finding was overstated: only `now()` ever fired, not the wider
`current_timestamp` family. And the batched-`UPDATE` finding's evidence was
incorrect — pglast does **not** reject the emitted SQL, because the statement
sits inside an opaque `$$…$$` body. The SQL was still invalid; the stated
reproduction didn't show it.

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

> **Resolved in 1.3.0.** Left here as the record of what 1.2.1 shipped with.

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

[1.3.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.3.0
[1.2.1]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.2.1
[1.2.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.2.0
[1.1.3]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.3
[1.1.2]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.2
[1.1.1]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.1
[1.1.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.1.0
[1.0.0]: https://github.com/Harshith029/safemigrate-lint/releases/tag/v1.0.0
