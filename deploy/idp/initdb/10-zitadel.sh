#!/bin/sh
# Runs once, when the database is first created (docker-entrypoint-initdb.d): the role Zitadel connects with and its
# database. Not a superuser, as Zitadel asks for production; it owns its database and nothing else. The password
# comes from the environment of the container and goes to psql as a variable, quoted by psql itself.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -v pw="$POSTGRES_ZITADEL_PASSWORD" <<'SQL'
CREATE ROLE zitadel LOGIN PASSWORD :'pw';
CREATE DATABASE zitadel OWNER zitadel;
SQL
