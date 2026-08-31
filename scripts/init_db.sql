-- scripts/init_db.sql
-- Runs automatically on first Postgres container start.
-- Creates extensions needed by the application.

-- UUID generation (used by server-default on id columns)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Optional: pg_stat_statements for query performance monitoring
-- CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
