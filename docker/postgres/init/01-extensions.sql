-- Executed once by the postgres image when the data volume is empty.
-- The Alembic migration (T03) also runs CREATE EXTENSION IF NOT EXISTS, so this is a convenience.
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Separate database for the test suite (see T03).
CREATE DATABASE law_test OWNER law;
\connect law_test
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS unaccent;
