# safemigrate-lint

Open-source GitHub Action for Postgres migration linting. Free forever, MIT.

**Status:** v0 — usable from the `master` branch via `uses:` SHA pinning. First tagged release pending.

## What it does

- Lints Postgres migration SQL on every PR
- 32 safety rules + 6 opt-in style rules
- Real Postgres parser via [pglast](https://github.com/lelit/pglast) (libpg_query)
- Cross-statement context (suppresses FK false-positives on tables created in the same migration)
- Severity tiers: CRITICAL / WARNING / STYLE — default mode shows only the first two
- Posts a find-or-create PR comment with per-finding detail
- Posts a Check Run with severity-mapped conclusion (`success` / `neutral` / `action_required`)

## Usage

Drop this into `.github/workflows/lint-migrations.yml`:

```yaml
name: Lint migrations
on:
  pull_request:
    paths:
      - 'migrations/**/*.sql'

permissions:
  contents: read
  pull-requests: write
  checks: write

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Harshith029/safemigrate-lint@master
        continue-on-error: true
        with:
          paths: 'migrations/**/*.sql'
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

For production use, pin to a commit SHA (`@<full-sha>`) instead of `@master` so the action can't change underneath you.

### Why `continue-on-error: true`?

The action exits non-zero whenever the lint finds anything (critical or warning), so without `continue-on-error: true` the workflow **step** turns red on every PR that needs review. Use the **Check Run** as the semantic signal instead — it maps severity to conclusion:

| findings              | check conclusion  | meaning                                  |
| --------------------- | ----------------- | ---------------------------------------- |
| none                  | `success`         | safe to merge                            |
| warnings / style only | `neutral`         | review, but doesn't block                |
| any critical          | `action_required` | look at this before merging              |

In branch protection, make `safemigrate-lint` (the Check Run name) a required status. The PR will be blocked on critical findings while warnings stay non-blocking.

### Inputs

| name       | default              | description                                                       |
| ---------- | -------------------- | ----------------------------------------------------------------- |
| `paths`    | (required)           | Glob or newline-separated list of SQL files to lint               |
| `severity` | `critical,warning`   | Comma-separated severity levels to include: `critical,warning,style` |
| `format`   | `json`               | Output format for the action log: `json` or `markdown`            |

### Outputs

| name             | type                | description                                       |
| ---------------- | ------------------- | ------------------------------------------------- |
| `findings-count` | integer             | Total findings emitted after severity filter      |
| `has-critical`   | `"true"` / `"false"`| Whether any critical-severity finding was emitted |

### Required permissions

The workflow needs three scopes on `GITHUB_TOKEN`:
- `contents: read` — to check out the migration files
- `pull-requests: write` — to post or edit the PR comment
- `checks: write` — to create the Check Run

## Why this exists

Existing free Postgres migration linters fail in three ways: their parsers break on extension SQL (TimescaleDB, PostGIS), they have no cross-statement context (false positives on FK-to-new-table cases), and stylistic noise drowns out real safety signals. The Atlas Pro rules that catch real lock and table-rewrite hazards cost $9/dev + $59/CI + $39/db per month. safemigrate-lint ships those rules free.

## License

MIT.
