ALTER TABLE users DROP COLUMN email;
ALTER TABLE orders ADD COLUMN ref uuid NOT NULL DEFAULT gen_random_uuid();
