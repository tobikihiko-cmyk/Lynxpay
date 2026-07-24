\set ON_ERROR_STOP on

-- Run after every migration as the migration owner. Runtime identities remain
-- NOBYPASSRLS; cross-tenant workers/metrics/admin receive explicit policies.

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO lynxpay_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO lynxpay_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO lynxpay_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO lynxpay_metrics, lynxpay_readonly;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO
    lynxpay_api, lynxpay_worker, lynxpay_admin;

REVOKE UPDATE, DELETE ON lynxpay_payment_ledger, lynxpay_audit_logs
    FROM lynxpay_api, lynxpay_worker, lynxpay_admin;

ALTER DEFAULT PRIVILEGES FOR ROLE lynxpay_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lynxpay_api, lynxpay_worker, lynxpay_admin;
ALTER DEFAULT PRIVILEGES FOR ROLE lynxpay_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO lynxpay_metrics, lynxpay_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE lynxpay_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO lynxpay_api, lynxpay_worker, lynxpay_admin;

DO $$
DECLARE
    table_name text;
    all_rls_tables text[] := ARRAY[
        'lynxpay_merchant_accounts', 'lynxpay_api_keys',
        'lynxpay_catalog_items', 'lynxpay_invoices',
        'lynxpay_invoice_line_items', 'lynxpay_payments',
        'lynxpay_payment_status_checks', 'lynxpay_webhook_endpoints',
        'lynxpay_payment_ledger', 'lynxpay_audit_logs',
        'lynxpay_daraja_credentials', 'lynxpay_payment_attempts',
        'lynxpay_mpesa_callbacks', 'lynxpay_webhook_deliveries',
        'lynxpay_webhook_delivery_attempts', 'lynxpay_reversal_requests',
        'lynxpay_reversal_callbacks'
    ];
    worker_tables text[] := ARRAY[
        'lynxpay_merchant_accounts', 'lynxpay_invoices', 'lynxpay_payments',
        'lynxpay_payment_status_checks', 'lynxpay_webhook_endpoints',
        'lynxpay_payment_ledger', 'lynxpay_audit_logs',
        'lynxpay_daraja_credentials', 'lynxpay_payment_attempts',
        'lynxpay_mpesa_callbacks',
        'lynxpay_webhook_deliveries', 'lynxpay_webhook_delivery_attempts',
        'lynxpay_reversal_requests', 'lynxpay_reversal_callbacks'
    ];
    metrics_tables text[] := ARRAY[
        'lynxpay_payments', 'lynxpay_mpesa_callbacks',
        'lynxpay_webhook_endpoints', 'lynxpay_webhook_deliveries',
        'lynxpay_reversal_requests'
    ];
BEGIN
    FOREACH table_name IN ARRAY all_rls_tables LOOP
        IF table_name = ANY(worker_tables) AND NOT EXISTS (
            SELECT 1 FROM pg_policies p
            WHERE p.schemaname = 'public'
              AND p.tablename = table_name
              AND p.policyname = 'lynxpay_worker_cross_tenant'
        ) THEN
            EXECUTE format(
                'CREATE POLICY lynxpay_worker_cross_tenant ON %I '
                'TO lynxpay_worker USING (true) WITH CHECK (true)', table_name
            );
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies p
            WHERE p.schemaname = 'public'
              AND p.tablename = table_name
              AND p.policyname = 'lynxpay_admin_cross_tenant'
        ) THEN
            EXECUTE format(
                'CREATE POLICY lynxpay_admin_cross_tenant ON %I '
                'TO lynxpay_admin USING (true) WITH CHECK (true)', table_name
            );
        END IF;
        IF table_name = ANY(metrics_tables) AND NOT EXISTS (
            SELECT 1 FROM pg_policies p
            WHERE p.schemaname = 'public'
              AND p.tablename = table_name
              AND p.policyname = 'lynxpay_metrics_cross_tenant'
        ) THEN
            EXECUTE format(
                'CREATE POLICY lynxpay_metrics_cross_tenant ON %I '
                'FOR SELECT TO lynxpay_metrics USING (true)', table_name
            );
        END IF;
    END LOOP;
END $$;
