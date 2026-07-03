# Order API Timeout Runbook

## Symptom

order-api requests are slow or timeout. Users may see delayed order creation,
stale order status, or repeated retries from upstream payment and checkout
services.

## Common causes

- A recent deployment changed database queries or cache keys.
- The order database is slow or has lock contention.
- Upstream traffic is higher than usual.
- Downstream inventory or delivery services are degraded.

## Investigation steps

1. Query order-api P95 and P99 latency for the last 30 minutes.
2. Query ERROR and WARN logs for timeout, slow query, lock wait, and retry.
3. Check whether order-api had a deployment in the last hour.
4. Check topology alerts for database, cache, inventory, and delivery services.
5. If latency matches a deployment window, evaluate rollback first.

## Mitigation

- Roll back the latest release if the issue clearly follows deployment.
- Increase cache TTL or enable read degradation for non-critical fields.
- Contact the database owner if lock wait or slow query errors keep growing.
