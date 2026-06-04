-- Refreshing an existing dashboard matview the blocking way. The matview was
-- created in an earlier migration, so it has live readers — a plain REFRESH
-- holds AccessExclusiveLock and stalls every dashboard query until it finishes.
-- Should fire: refresh-matview-blocks-reads (WARNING).
REFRESH MATERIALIZED VIEW sales_daily;
