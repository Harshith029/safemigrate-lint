-- Creating a matview and refreshing it in the same migration. It has no readers
-- yet, so the blocking refresh is harmless — the cross-statement state machine
-- must suppress the warning. Must NOT fire (proves the in-migration suppression).
CREATE MATERIALIZED VIEW sales_daily AS
SELECT date_trunc('day', created_at) AS day, count(*) AS n
FROM orders
GROUP BY 1;

REFRESH MATERIALIZED VIEW sales_daily;
