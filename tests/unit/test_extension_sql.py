"""Extension SQL must parse — it's a claim the README makes.

TimescaleDB and PostGIS ship DDL that isn't in core Postgres grammar but is
still ordinary Postgres syntax (function calls, custom types, storage
parameters). libpg_query handles it, and nothing verified that until now.

Note what this file deliberately does NOT assert: that competing linters fail
here. squawk parses all of this too, so any claim of an advantage on extension
SQL would be false. This pins our own behavior, nothing about anyone else's.
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
