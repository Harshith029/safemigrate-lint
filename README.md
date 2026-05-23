# safemigrate-lint

Open-source GitHub Action for Postgres migration linting. Free forever, MIT.

**Status:** Phase 1 — analyzer engine under active build (weeks 1–2). Not yet ready for use.

## What it does (planned for v1)

- Lints Postgres migration SQL on every PR
- 34 safety rules + 6 opt-in style rules
- Real Postgres parser via [pglast](https://github.com/lelit/pglast) (libpg_query)
- Cross-statement context (suppresses FK false-positives on tables created in the same migration)
- Severity tiers: CRITICAL / WARNING / STYLE — default mode shows only the first two
- Posts findings as PR comments + check runs (week 4+)

## Why this exists

Existing free Postgres migration linters fail in three ways: their parsers break on extension SQL (TimescaleDB, PostGIS), they have no cross-statement context (false positives on FK-to-new-table cases), and stylistic noise drowns out real safety signals. The Atlas Pro rules that catch real lock and table-rewrite hazards cost $9/dev + $59/CI + $39/db per month. safemigrate-lint ships those rules free.

See `analysis/deliverable_2_positioning.md` for the full positioning.

## License

MIT.
