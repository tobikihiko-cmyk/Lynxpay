# Observability operations

- Scrape `/metrics` with `Authorization: Bearer $METRICS_BEARER_TOKEN` in production.
- Load `ops/prometheus-alerts.yml` into Prometheus-compatible alerting.
- Set `OTEL_EXPORTER_OTLP_ENDPOINT` to send FastAPI, HTTPX/Daraja/webhook, and SQLAlchemy traces to the approved collector.
- Restrict trace attributes: never attach raw callback bodies, authorization headers, API keys, phone numbers, email payloads, or decrypted credentials.
- Retain immutable audit/ledger evidence longer than operational traces and ordinary application logs.

Dashboards should show API rate/error/latency, callback intake, reconciliation outcomes, payment states, webhook/email queue age and dead letters, PostgreSQL locks/connections, Redis health, and worker lease recovery.

The application exports bounded counters/histograms for payment creation, STK acceptance/rejection/uncertainty, callback receipt/outcome/rate rejection, webhook delivery outcome, reconciliation outcome, Daraja operation latency, and OAuth cache hits. Database gauges expose payment states, explicit pending/unknown counts, callback states, stale submitting attempts, reconciliation backlog/oldest age, webhook queue/oldest age, paused endpoints, worker heartbeat age, and checked-out metrics-pool connections.

The checked-out pool alert assumes a 20-connection operational threshold. Change that expression alongside any deployed pool-size change; do not treat it as an auto-discovered capacity percentage.
