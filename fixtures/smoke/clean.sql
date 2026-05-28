BEGIN;
CREATE INDEX idx_test ON users(email);
COMMIT;
DROP TABLE legacy_orders;
