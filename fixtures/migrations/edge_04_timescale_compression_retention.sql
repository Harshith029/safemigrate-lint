-- Hand-crafted from TimescaleDB's canonical "extending an existing setup"
-- patterns documented in docs.timescale.com (compression policies, retention
-- policies, continuous aggregates). Represents a realistic month-3 migration
-- for a team that started with a basic hypertable and is now layering in
-- compression + retention + a continuous aggregate for dashboard queries.
--
-- The TimescaleDB-specific functions (add_compression_policy,
-- add_retention_policy, add_continuous_aggregate_policy) are standard SQL
-- function calls from Postgres's parser perspective — they get evaluated at
-- runtime against the loaded TimescaleDB extension.

-- Step 1: enable compression on the conditions hypertable.
-- Segment by location for query-pattern-aware compression; older chunks
-- get rewritten into compressed columnar segments.
ALTER TABLE conditions SET (
    timescaledb.compress = true,
    timescaledb.compress_segmentby = 'location',
    timescaledb.compress_orderby = 'time DESC'
);

-- Step 2: auto-compress chunks older than 7 days.
SELECT add_compression_policy('conditions', INTERVAL '7 days');

-- Step 3: drop chunks older than 90 days (retention policy).
SELECT add_retention_policy('conditions', INTERVAL '90 days');

-- Step 4: continuous aggregate for hourly summaries.
-- Materialized view stays fresh via background refresh; dashboard queries
-- against the aggregate instead of scanning raw conditions.
CREATE MATERIALIZED VIEW conditions_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    location,
    AVG(temperature) AS avg_temp,
    MAX(temperature) AS max_temp,
    MIN(temperature) AS min_temp
FROM conditions
GROUP BY bucket, location;

-- Step 5: refresh the continuous aggregate every hour, processing data
-- from 3 hours ago up to 1 hour ago (gives time-zone / late-arrival buffer).
SELECT add_continuous_aggregate_policy('conditions_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
