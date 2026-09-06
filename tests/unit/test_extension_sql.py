"""Extension SQL must parse — it's a claim the README makes.

TimescaleDB and PostGIS ship DDL that isn't in core Postgres grammar but is
still ordinary Postgres syntax (function calls, custom types, storage
parameters). libpg_query handles it, and nothing verified that until now.

Note what this file deliberately does NOT assert: that competing linters fail
here. squawk parses all of this too, so any claim of an advantage on extension
SQL would be false. This pins our own behavior, nothing about anyone else's.

The one measured parser difference is unrelated to extensions and lives at the
bottom of this file: a parenthesized SELECT in a CREATE VIEW body.
"""

from __future__ import annotations

import pytest

from safemigrate_lint.core.parser import parse_string

POSTGIS = [
    ("create extension", "CREATE EXTENSION IF NOT EXISTS postgis;"),
    ("geometry column", "ALTER TABLE places ADD COLUMN geom geometry(Point, 4326);"),
    ("geography column", "CREATE TABLE t (g geography(Polygon, 4326));"),
    ("AddGeometryColumn", "SELECT AddGeometryColumn('public','places','geom',4326,'POINT',2);"),
    ("GiST spatial index", "CREATE INDEX idx_geom ON places USING GIST (geom);"),
    ("spatial predicate in CHECK", "ALTER TABLE places ADD CONSTRAINT c CHECK (ST_IsValid(geom));"),
]

TIMESCALE = [
    ("hypertable", "SELECT create_hypertable('metrics','time');"),
    (
        "compression settings",
        "ALTER TABLE metrics SET (timescaledb.compress, "
        "timescaledb.compress_orderby = 'time DESC');",
    ),
    ("retention policy", "SELECT add_retention_policy('metrics', INTERVAL '90 days');"),
    (
        "continuous aggregate",
        "CREATE MATERIALIZED VIEW m WITH (timescaledb.continuous) AS "
        "SELECT time_bucket('1 day', time) AS b, avg(v) FROM metrics GROUP BY b;",
    ),
]


@pytest.mark.parametrize(("label", "sql"), POSTGIS, ids=[c[0] for c in POSTGIS])
def test_postgis_parses(label: str, sql: str) -> None:
    result = parse_string(sql)
    assert result.finding is None, f"PostGIS {label} failed to parse: {result.finding}"


@pytest.mark.parametrize(("label", "sql"), TIMESCALE, ids=[c[0] for c in TIMESCALE])
def test_timescaledb_parses(label: str, sql: str) -> None:
    result = parse_string(sql)
    assert result.finding is None, f"TimescaleDB {label} failed to parse: {result.finding}"


# --- a construct where the parsers genuinely differ ------------------------
# Measured over 2,497 real migrations from cal.com, Mattermost, Supabase and
# Windmill: squawk 2.56.0 reported a syntax error on 23 files, this on 20, and
# the 20 are a strict subset. The 3 extra are Windmill compatibility views that
# wrap the view body in parentheses. squawk's parser rejects it with
# "expected SELECT, got PAREN_SELECT"; libpg_query accepts it, because Postgres
# does.
#
# This is the whole of the measured parser advantage — 3 files in 2,497. It is
# not about extension SQL, which both tools handle.


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE OR REPLACE VIEW v AS (SELECT id FROM t);",
        "CREATE VIEW v AS (SELECT a, b FROM t WHERE a > 0);",
    ],
)
def test_parenthesised_view_body_parses(sql: str) -> None:
    result = parse_string(sql)
    assert result.finding is None, f"parenthesized view body failed: {result.finding}"
