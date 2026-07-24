# Full Payment Incident Drill

This is the final production-data-path acceptance for the local environment.
Unlike the connector checks, the drill waits for the actual Prometheus rule.

## Flow

```text
payment-api 100% controlled 5xx
  -> Prometheus PaymentApiHigh5xxRatio firing
  -> Alertmanager authenticated webhook
  -> OnCall Agent real evidence and RAG diagnosis
  -> LangGraph human review interrupt
  -> approved reset_payment_faults
  -> healthy payment traffic
  -> Prometheus alert inactive
  -> Alertmanager resolved webhook
  -> Agent alert group resolved
```

## Configure

Run once:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\configure_local_payment_drill.ps1
```

The script generates missing local secrets without printing them, updates the
gitignored `.env`, synchronizes the Alertmanager Docker secret, and enables
SQLite-backed native LangGraph checkpoints. The one-process bypass avoids
changing the machine-wide PowerShell execution policy.

## Run

Start the Agent, payment-api, observability services, Ollama, Milvus, and the
payment log shipper. Then run:

```powershell
.\.venv\Scripts\python.exe `
  scripts\run_full_payment_incident_drill.py
```

The default mode waits for approval in the workflow console. The explicit
`--auto-approve` option is intended only for release-gate automation.

The drill reports `PASS` only when the Prometheus rule fired, the real
diagnostic providers succeeded, the approved remediation reset payment-api,
and the Agent received the final resolved notification.
