-- Adding an audit mirror of an existing table, then tightening the real one.
-- The new table is `audit.users`; the ALTER targets the long-populated
-- `public.users`. Matching relations on bare name alone made the CREATE vouch
-- for the ALTER and silenced the hazard — a migration that fails on any
-- populated table shipped looking clean.
-- Should fire: add-non-nullable-without-default (CRITICAL) on public.users.
CREATE TABLE audit.users (
    id          bigint PRIMARY KEY,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL
);

ALTER TABLE public.users ADD COLUMN onboarding_stage text NOT NULL;
