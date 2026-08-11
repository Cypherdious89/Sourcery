-- Runs automatically on first container startup (Postgres executes every
-- *.sql file in /docker-entrypoint-initdb.d against the default database).
-- Enables the pgvector extension so vector(384) columns can be created.
CREATE EXTENSION IF NOT EXISTS vector;
