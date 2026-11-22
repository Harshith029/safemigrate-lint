-- Reshaping an index by dropping and re-creating it under the same name. Both
-- halves block: the DROP takes AccessExclusiveLock on the table, and the
-- non-concurrent CREATE blocks writes for the whole build.
-- Reading whole-file state made the later CREATE suppress the DROP warning,
-- because the index name was "created in this migration" — from a statement
-- that hadn't run yet.
-- Should fire: concurrent-index-drop-required AND concurrent-index-create-required.
DROP INDEX idx_orders_customer;

CREATE INDEX idx_orders_customer ON orders (customer_id, created_at DESC);
