# LynxPay observability operations

## Collection

- Scrape `/metrics` with `Authorization: Bearer $METRICS_BEARER_TOKEN`.
- Load `ops/prometheus-alerts.yml` into the approved Prometheus-compatible ruler.
- Import `ops/grafana/lynxpay-overview.json`.
- Send OpenTelemetry traces through `OTEL_EXPORTER_OTLP_ENDPOINT`.
- Retain audit and ledger evidence longer than traces and ordinary logs.

The sample `ops/prometheus.yml` expects the metrics token at
`/run/secrets/lynxpay_metrics_token`. Restrict network access to the collector;
the bearer token is an additional control, not a substitute for private
networking.

## Correlation

Every inbound request receives an `X-Request-ID`. A valid supplied identifier is
preserved; otherwise LynxPay creates one. Payment creation stores it as the
durable `correlation_id`, and that identifier continues through:

- API audit metadata;
- Daraja STK, status-query, and reversal headers;
- callback processing metrics and logs;
- reconciliation snapshots;
- merchant webhook payloads and `X-LynxPay-Correlation-ID`.

Start an investigation with payment ID, correlation ID, merchant ID,
CheckoutRequestID, or M-PESA receipt. Never search logs by full phone number,
credential, API key, refresh token, or raw callback body.

## Primary signals

### Payments and Safaricom

- `lynxpay_payments{status=...}`
- `lynxpay_payments_pending_count`
- `lynxpay_payments_unknown_count`
- `lynxpay_oldest_unknown_payment_age_seconds`
- `lynxpay_payment_outcomes_total{status,source}`
- `lynxpay_merchant_payment_outcomes_total{merchant_id,status,source}`
- `lynxpay_mpesa_result_codes_total{operation,code}`
- `lynxpay_callback_latency_seconds`
- `lynxpay_daraja_request_duration_seconds`

Merchant labels are acceptable for the initial bounded pilot. Reassess
cardinality before thousands of merchants; aggregate or move merchant-level
analysis to logs/analytics if series count becomes material.

### Queues and workers

- `lynxpay_reconciliation_backlog`
- `lynxpay_oldest_reconciliation_age_seconds`
- `lynxpay_webhook_deliveries{status}`
- `lynxpay_oldest_webhook_age_seconds`
- `lynxpay_email_deliveries{status}`
- `lynxpay_reversal_requests{status}`
- `lynxpay_worker_mode_heartbeat_age_seconds{mode}`

Monitor every production worker mode independently. A healthy webhook worker
must not hide a failed reversal or reconciliation worker.

### API and dependencies

- `lynxpay_http_requests_total{method,route,status}`
- `lynxpay_http_request_duration_seconds`
- `lynxpay_rate_limited_total{class}`
- `lynxpay_database_pool_checked_out`
- `lynxpay_database_pool_capacity`
- `lynxpay_database_gauge_collection_errors_total`

Pool saturation uses discovered SQLAlchemy capacity. Compare it with PgBouncer
client/server pools and PostgreSQL `max_connections`; the application metric
does not replace database-side monitoring.

## Dashboard review

During the pilot, an operator reviews the dashboard at the beginning and end of
each Kenya business day:

1. Payment success rate and merchant-specific failures.
2. Unknown payment count and oldest age.
3. Callback p95 arrival delay and Safaricom result codes.
4. Reconciliation and webhook backlog age.
5. Worker heartbeat by mode.
6. Database pool usage and API error/latency rate.
7. Failed, timed-out, or unknown reversals.

Record accepted anomalies in the incident or release evidence; do not clear
alerts by mutating payment state.

## Privacy

Never attach these values to metrics, traces, or ordinary logs:

- phone numbers or customer names;
- API keys, tokens, cookies, authorization headers, or MFA secrets;
- Daraja consumer secrets, passkeys, initiator credentials, or security credentials;
- raw callback bodies or decrypted email payloads;
- full webhook response bodies.

Use bounded identifiers and redacted provider classifications. Restrict
merchant-level dashboards to support and payment operations staff.

## Alert maintenance

Every alert must have:

- a tested expression and a runbook anchor;
- a named owning team;
- page or ticket severity;
- an explicit `for` duration;
- evidence of one firing and one recovery notification in staging.

Review thresholds after every capacity test and monthly during the pilot. A
threshold change is a reviewed operational change, not an ad hoc incident fix.
