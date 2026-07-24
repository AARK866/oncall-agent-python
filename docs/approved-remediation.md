# Approved Payment Remediation

The OnCall Agent can reset controlled `payment-api` faults only after a
persisted human approval.

## Safety pipeline

```text
Alertmanager firing alert
  -> real evidence collection
  -> Milvus runbook retrieval
  -> DeepSeek diagnosis
  -> deterministic remediation policy
  -> LangGraph human_review_gate
  -> approved review record
  -> execute_approved_remediation
  -> POST payment-api /admin/fault/reset
  -> persist result and audit timeline
```

The diagnostic LLM and normal tool registry cannot invoke the reset endpoint.
The remediation controller accepts only:

- service: `payment-api`
- source: `alertmanager`
- alerts: `PaymentApiHigh5xxRatio` or `PaymentApiHighP95Latency`
- action: `reset_payment_faults`
- endpoint: `/admin/fault/reset`

`PaymentApiDown`, arbitrary tool arguments, arbitrary URLs, shell commands, and
unapproved reviews are rejected.

## Local configuration

Configure the Agent and payment-api with the same fault administration token:

```env
PAYMENT_API_REMEDIATION_ENABLED=true
PAYMENT_API_BASE_URL=http://127.0.0.1:8010
PAYMENT_API_FAULT_ADMIN_TOKEN=replace-with-a-long-random-token
```

The payment service must also use:

```env
PAYMENT_API_ENABLE_FAULT_INJECTION=true
PAYMENT_API_FAULT_ADMIN_TOKEN=replace-with-the-same-token
```

Restart both processes after changing their environment.

## Real diagnosis acceptance

Required background services:

- payment-api on port `8010`
- OnCall Agent on port `8000`
- Prometheus on port `9090`
- Alertmanager on port `9093`
- Loki on port `3100`
- Milvus on port `19530`
- Ollama bge-m3 on port `11434`
- Loki log shipper following `app/data/payment-api.log`

Run:

```powershell
.\.venv\Scripts\python.exe `
  scripts\check_real_incident_remediation.py
```

The script stops at `waiting_review`. Open `http://127.0.0.1:8000/console`,
approve the pending review, and keep the script running. It then verifies the
approved reset and the final payment fault state.

For non-interactive CI acceptance only:

```powershell
.\.venv\Scripts\python.exe `
  scripts\check_real_incident_remediation.py `
  --auto-approve `
  --reviewer release-gate
```

The fully automatic Prometheus-rule acceptance is documented in
`docs/full-payment-incident-drill.md`.
