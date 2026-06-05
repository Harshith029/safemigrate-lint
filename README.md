# safemigrate-lint

[![CI](https://github.com/Harshith029/safemigrate-lint/actions/workflows/ci.yml/badge.svg)](https://github.com/Harshith029/safemigrate-lint/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/safemigrate-lint.svg)](https://pypi.org/project/safemigrate-lint/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

A GitHub Action that lints Postgres migration SQL on every PR. Catches the operations that actually break production — written for the real shape of production migrations, not the textbook one.

- 33 safety rules + 6 opt-in style rules across CRITICAL / WARNING / STYLE tiers
- Real Postgres parser via [pglast](https://github.com/lelit/pglast) (libpg_query) — handles extension SQL (TimescaleDB, PostGIS) that other linters trip on
- Cross-statement context — suppresses FK-to-new-table and similar false positives that pile up in single-statement linters
- Posts a find-or-create PR comment with per-finding detail; creates a Check Run with severity-mapped conclusion

## Demo

On every pull request, safemigrate-lint posts a comment that groups findings by severity — each with the lock it takes and the safe rewrite — and sets a Check Run conclusion you can require in branch protection.

![safemigrate-lint comment demo](https://raw.githubusercontent.com/Harshith029/safemigrate-lint/master/docs/demo.png)

<details>
<summary><b>Example PR comment</b> (click to expand)</summary>

```text
## 🛡️ SafeMigrate Lint

**2 findings** — 1 critical, 1 warning.

### 🔴 CRITICAL — drop-column-restricted
migrations/0042_cleanup.sql:2
DROP COLUMN deleteat on threads is irreversible data loss.

### 🟡 WARNING — constraint-not-valid-required
migrations/0042_cleanup.sql:8
ADD CONSTRAINT orders_user_fk FOREIGN KEY without NOT VALID requires a full
table scan, holding AccessExclusiveLock for the duration.

Suggested fix:
  ALTER TABLE orders ADD CONSTRAINT orders_user_fk FOREIGN KEY (...) NOT VALID;
  -- then, in a separate migration:
  ALTER TABLE orders VALIDATE CONSTRAINT orders_user_fk;
```

</details>

## Why

We scanned ~700 production migrations from Cal.com, Mattermost, Supabase, Hasura, and TimescaleDB. **Zero** of them ran the textbook DANGEROUS operations the popular linters warn loudest on (raw `DROP TABLE` in app code, etc.). The real risks live one layer deeper: ADD COLUMN GENERATED triggering a table rewrite, ADD CONSTRAINT FK without `NOT VALID`, dynamic SQL the analyzer can't see, constraint drops that silently break invariants. safemigrate-lint is built around those.

Atlas Pro charges $9/dev + $59/CI + $39/db per month for many of these checks. This action ships them free, MIT.

## Quickstart

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
      - uses: Harshith029/safemigrate-lint@v1
        continue-on-error: true
        with:
          paths: 'migrations/**/*.sql'
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

For maximum reproducibility, pin to a commit SHA (`@<full-sha>`) instead of `@v1`.

### Why `continue-on-error: true`?

The action's step exits non-zero whenever the lint finds anything (so workflows that don't set this turn red on every PR with findings). Use the **Check Run** as the semantic signal instead — it maps severity to conclusion:

| findings              | check conclusion  | meaning                                  |
| --------------------- | ----------------- | ---------------------------------------- |
| none                  | `success`         | safe to merge                            |
| warnings / style only | `neutral`         | review, but doesn't block                |
| any critical          | `action_required` | look at this before merging              |

In branch protection, require `safemigrate-lint` (the Check Run name) as a status check. The PR will be blocked on critical findings while warnings stay non-blocking.

## Other ways to run it

The same engine ships three ways — use whichever fits your workflow.

### CLI

```bash
# from PyPI
pipx install safemigrate-lint           # or: uv tool install safemigrate-lint
# …or straight from source
pipx install git+https://github.com/Harshith029/safemigrate-lint

safemigrate-lint migrations/*.sql       # exit 0 clean · 1 findings · 2 input error
safemigrate-lint migrations/*.sql --severity=critical,warning,style --format=markdown
```

### pre-commit

Catch dangerous migrations before they're even committed:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Harshith029/safemigrate-lint
    rev: v1.0.0
    hooks:
      - id: safemigrate-lint
```

Runs on staged `*.sql` files and blocks the commit on any finding.

## Reference

### Inputs

| name       | default              | description                                                          |
| ---------- | -------------------- | -------------------------------------------------------------------- |
| `paths`    | (required)           | Glob or newline-separated list of SQL files to lint                  |
| `severity` | `critical,warning`   | Comma-separated severity levels to include: `critical,warning,style` |
| `format`   | `json`               | Output format for the action log: `json` or `markdown`               |

### Outputs

| name             | type                | description                                       |
| ---------------- | ------------------- | ------------------------------------------------- |
| `findings-count` | integer             | Total findings emitted after severity filter      |
| `has-critical`   | `"true"` / `"false"`| Whether any critical-severity finding was emitted |

### Required permissions

| scope                   | needed for                                |
| ----------------------- | ----------------------------------------- |
| `contents: read`        | checking out migration files              |
| `pull-requests: write`  | posting / editing the PR comment          |
| `checks: write`         | creating the Check Run                    |

### Inline suppression

For a one-off justified exception, prefix the statement with an ignore comment:

```sql
-- safemigrate:ignore=drop-column-restricted reason="column archived to data warehouse before drop"
ALTER TABLE users DROP COLUMN legacy_referrer;
```

### Configuration via `.safemigrate.toml`

Optional repo-level config. Walks upward from the first linted file to find it.

```toml
[rules]
disabled = ["timestamptz-over-timestamp-preferred"]    # hard-disable, never fires

[rules.style]
enabled = ["bigint-over-int-preferred"]                # promote STYLE -> WARNING in default mode
```

## How it compares to squawk

[squawk](https://github.com/sbdchd/squawk) is the closest other free OSS option. Both lint Postgres migrations, both are MIT.

|                                          | safemigrate-lint                                | squawk                          |
| ---------------------------------------- | ----------------------------------------------- | ------------------------------- |
| Parser                                   | pglast (libpg_query — actual Postgres parser)   | Rust reimplementation           |
| Extension SQL (TimescaleDB / PostGIS)    | parses cleanly                                  | known parser gaps on newer SQL  |
| Cross-statement context                  | yes — suppresses FK / index / constraint rules on same-migration tables | per-statement only |
| Out-of-the-box GitHub Action             | yes (this repo)                                 | shipped binary + DIY workflow   |
| PR comments + Check Run                  | built-in                                        | DIY                             |
| Rule count                               | 33 safety + 6 opt-in style                      | 37 rules                        |
| Default-mode signal on a 23-fixture corpus | 39 findings, all actionable                   | 205 findings                    |

> Measured with squawk 2.56.0, both tools in their default configuration, on this repo's `fixtures/migrations/`. Most of squawk's extra findings are its style/opinion rules (`prefer-robust-stmts`, `prefer-bigint-over-int`, `prefer-identity`, …), which safemigrate-lint ships as opt-in STYLE rules rather than firing by default.

If you want the broadest rule catalog and you're comfortable wiring the action yourself, squawk is mature and well-maintained. If you want a one-paste install plus FK-to-new-table suppression by default, this is the trade.

## Contributing

Contributions welcome — especially new rules and false-positive reports. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup and rule philosophy, and
[docs/writing-a-rule.md](docs/writing-a-rule.md) for a step-by-step rule walkthrough.

## License

MIT — see [LICENSE](LICENSE).
