# Contributing to safemigrate-lint

Thanks for helping make Postgres migrations safer. This project has a specific
point of view — read the philosophy first, it's why the linter exists.

## Philosophy: fewer findings, all actionable

safemigrate-lint was built after reading ~700 real production migrations
(Cal.com, Mattermost, Supabase, Hasura, TimescaleDB). The finding that shaped
the whole project: the textbook "dangerous" operations linters shout about
barely appear in real migrations, and blanket warnings produce alert fatigue
that gets the linter turned off.

So every rule must clear a higher bar than "this is technically a smell":

1. **It catches a real production risk** — a lock that blocks traffic, a table
   rewrite, data loss, a broken invariant — not just a style opinion. Style
   opinions go in the `STYLE` tier (opt-in only).
2. **It minimizes false positives.** If the risk's premise doesn't hold (e.g. an
   FK validation scan on a table that was *created in the same migration* and is
   therefore empty), the rule must suppress itself using the cross-statement
   [`MigrationState`](src/safemigrate_lint/core/state.py).
3. **Its message is specific and its `help` teaches the fix** — name the object,
   the lock, the non-blocking alternative. Look at existing rules for the bar.

If you're unsure whether something belongs, open an issue before writing code.

## Project layout

```
src/safemigrate_lint/
  cli.py              # argv entry point, severity filtering, exit codes
  core/
    parser.py         # pglast (libpg_query) parse → statements
    state.py          # cross-statement MigrationState + StateBuilder
    engine.py         # dispatch each statement to applicable rules
    finding.py        # Finding dataclass + Severity enum
    reporter.py       # JSON + markdown output
    suppressor.py     # `-- safemigrate:ignore=` inline comments
    config.py         # .safemigrate.toml loader
  rules/
    _registry.py      # @register_rule decorator + RULES dict + RuleContext
    __init__.py       # imports every rule module (fires registration)
    <one file per rule>
docker/entrypoint.py  # GitHub Action wrapper around the CLI
fixtures/migrations/  # corpus *.sql (dangerous_/subtle_/edge_/safe_ prefixes)
tests/regression/     # golden JSON + corpus harness
```

## Dev setup

Uses [uv](https://docs.astral.sh/uv/). On Linux/macOS (or WSL — **not** a
Windows venv shared over a mount):

```bash
git clone https://github.com/Harshith029/safemigrate-lint
cd safemigrate-lint
uv sync                       # creates .venv, installs deps + dev tools
```

## The checks (all must pass)

```bash
uv run pytest                 # unit + golden-corpus regression
uv run ruff check .           # lint
uv run mypy src               # type-check
```

CI runs the same three on every PR.

## Adding or changing a rule

Full walkthrough: **[docs/writing-a-rule.md](docs/writing-a-rule.md)**. In short:

1. Create `src/safemigrate_lint/rules/<your_rule>.py` with a `check` function
   decorated by `@register_rule(...)`.
2. Add its import to `src/safemigrate_lint/rules/__init__.py` (the decorator
   only fires on import).
3. Add at least one fixture to `fixtures/migrations/` — a `*_dangerous`/`*_subtle`
   case that should fire **and** a `safe_*` case that must not.
4. Regenerate goldens and review the diff:
   ```bash
   uv run pytest --update-golden
   git diff tests/regression/golden     # eyeball every change — this is the review
   ```
5. Run the three checks above; open a PR.

Changing an existing rule's output is expected to move goldens — that diff is the
proof of what changed, so keep it tight and explain it in the PR.

## Commit & PR conventions

- Conventional-commit prefixes: `feat(rules):`, `fix(action):`, `docs:`, `chore:`.
- One logical change per PR. A new rule = rule file + `__init__` import +
  fixtures + golden diff, together.
- Describe the production scenario the rule prevents, and note any false-positive
  cases you deliberately suppress (or accept).

## Reporting issues

False positives and false negatives are the most valuable reports — include the
exact SQL, the Postgres version, and what you expected. A failing fixture in the
issue is even better.
