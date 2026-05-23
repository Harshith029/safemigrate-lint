-- Adapted from TimescaleDB's official NYC Taxi getting-started example
-- (docs/getting-started/nyc-taxi/nyc-taxi-schema.sql). Represents a typical
-- "starting to use TimescaleDB" migration: enable extension, define schema,
-- declare hypertable via the modern WITH (tsdb.hypertable, ...) API,
-- add query-pattern indexes.
--
-- Psql client directives (\timing, \echo) stripped — migration tools (Flyway,
-- Liquibase, Atlas, Sqitch, raw psycopg) execute via libpq, not the psql
-- client, so backslash directives are not part of real-world migration SQL.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE trips (
    vendor_id TEXT,
    pickup_boroname VARCHAR,
    pickup_datetime TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    dropoff_datetime TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    passenger_count NUMERIC,
    trip_distance NUMERIC,
    pickup_longitude NUMERIC,
    pickup_latitude NUMERIC,
    rate_code INTEGER,
    dropoff_longitude NUMERIC,
    dropoff_latitude NUMERIC,
    payment_type VARCHAR,
    fare_amount NUMERIC,
    extra NUMERIC,
    mta_tax NUMERIC,
    tip_amount NUMERIC,
    tolls_amount NUMERIC,
    improvement_surcharge NUMERIC,
    total_amount NUMERIC
) WITH (
    tsdb.hypertable,
    tsdb.partition_column='pickup_datetime',
    tsdb.enable_columnstore=true,
    tsdb.segmentby='pickup_boroname',
    tsdb.orderby='pickup_datetime DESC'
);

CREATE INDEX idx_trips_pickup_time ON trips (pickup_datetime DESC);
CREATE INDEX idx_trips_borough_time ON trips (pickup_boroname, pickup_datetime DESC);
