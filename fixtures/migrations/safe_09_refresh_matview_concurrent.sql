-- The non-blocking way to refresh a matview with live readers. CONCURRENTLY
-- rebuilds without taking AccessExclusiveLock. Must NOT fire.
REFRESH MATERIALIZED VIEW CONCURRENTLY sales_daily;
