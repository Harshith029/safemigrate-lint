-- The single most common migration there is: adding a timestamp column with a
-- current-time default. now() is STABLE, not VOLATILE — one value for the whole
-- transaction — so Postgres 11+ stores the default in the catalog and skips the
-- rewrite entirely. The ALTER TABLE docs use exactly this as their example of a
-- default that does not rewrite.
-- Flagging it CRITICAL was the false positive most likely to make a team switch
-- the linter off. Must NOT fire volatile-default-rewrites-table.
ALTER TABLE users ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE users ADD COLUMN updated_at timestamptz DEFAULT current_timestamp;
