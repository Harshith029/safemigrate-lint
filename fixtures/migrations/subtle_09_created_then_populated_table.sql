-- Backfill-style migration: stage a table, load it, then tighten the schema.
-- "Created in this migration" was treated as "empty", but the INSERT in between
-- means the NOT NULL has rows to satisfy and the ALTER fails at deploy time.
-- The FK on the same table is a real validation scan for the same reason.
-- Should fire: add-non-nullable-without-default (CRITICAL),
-- constraint-not-valid-required (WARNING).
CREATE TABLE order_backfill (
    id           bigint PRIMARY KEY,
    order_id     bigint,
    processed_at timestamptz
);

INSERT INTO order_backfill (id, order_id)
SELECT id, id FROM orders WHERE created_at < now();

ALTER TABLE order_backfill ADD COLUMN region text NOT NULL;

ALTER TABLE order_backfill
    ADD CONSTRAINT order_backfill_order_fk FOREIGN KEY (order_id) REFERENCES orders (id);
