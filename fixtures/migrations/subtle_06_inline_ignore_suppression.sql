-- Inline-ignore suppression across multiple statements: the second DROP COLUMN
-- is intentionally suppressed by the preceding `-- safemigrate:ignore=` comment;
-- the first DROP COLUMN has no comment and must still fire. Regression guard for
-- suppression on a statement that follows another statement (pglast reports its
-- location at the end of the previous statement).
ALTER TABLE users DROP COLUMN legacy_referrer;
-- safemigrate:ignore=drop-column-restricted reason="archived to warehouse before drop"
ALTER TABLE users DROP COLUMN deprecated_flag;
