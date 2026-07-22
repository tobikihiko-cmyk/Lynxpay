\set ON_ERROR_STOP on

-- Run as the cluster administrator before Alembic. Password values are psql
-- variables so they never belong in source control:
-- psql ... -v migrator_password=... -v api_password=... -v worker_password=...
--   -v admin_password=... -v metrics_password=... -v readonly_password=... -f this-file

SELECT 'CREATE ROLE lynxpay_owner NOLOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lynxpay_owner') \gexec
SELECT 'CREATE ROLE lynxpay_migrator LOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lynxpay_migrator') \gexec
SELECT 'CREATE ROLE lynxpay_api LOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lynxpay_api') \gexec
SELECT 'CREATE ROLE lynxpay_worker LOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lynxpay_worker') \gexec
SELECT 'CREATE ROLE lynxpay_admin LOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lynxpay_admin') \gexec
SELECT 'CREATE ROLE lynxpay_metrics LOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lynxpay_metrics') \gexec
SELECT 'CREATE ROLE lynxpay_readonly LOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lynxpay_readonly') \gexec

SELECT format('ALTER ROLE lynxpay_migrator PASSWORD %L', :'migrator_password') \gexec
SELECT format('ALTER ROLE lynxpay_api PASSWORD %L', :'api_password') \gexec
SELECT format('ALTER ROLE lynxpay_worker PASSWORD %L', :'worker_password') \gexec
SELECT format('ALTER ROLE lynxpay_admin PASSWORD %L', :'admin_password') \gexec
SELECT format('ALTER ROLE lynxpay_metrics PASSWORD %L', :'metrics_password') \gexec
SELECT format('ALTER ROLE lynxpay_readonly PASSWORD %L', :'readonly_password') \gexec

GRANT lynxpay_owner TO lynxpay_migrator;
GRANT CONNECT ON DATABASE :"database_name" TO
    lynxpay_migrator, lynxpay_api, lynxpay_worker, lynxpay_admin,
    lynxpay_metrics, lynxpay_readonly;
ALTER SCHEMA public OWNER TO lynxpay_owner;
GRANT USAGE ON SCHEMA public TO
    lynxpay_api, lynxpay_worker, lynxpay_admin, lynxpay_metrics, lynxpay_readonly;
ALTER ROLE lynxpay_migrator IN DATABASE :"database_name" SET role TO 'lynxpay_owner';
