# Payment API

`services/payment_api` is the real HTTP business service monitored by the
OnCall Agent. It stores payment records, enforces `order_id` idempotency,
exports Prometheus metrics, writes JSON logs, and supports controlled fault
drills.

It does not process real bank cards or production payment credentials.

## Run locally

Use a separate PowerShell terminal:

```powershell
$env:PAYMENT_API_ENV="local"
$env:PAYMENT_API_DATABASE_PATH="app/data/payment-api.db"
$env:PAYMENT_API_LOG_FILE="app/data/payment-api.log"
$env:PAYMENT_API_ENABLE_FAULT_INJECTION="true"
$env:PAYMENT_API_FAULT_ADMIN_TOKEN="replace-with-a-long-local-token"

.\.venv\Scripts\python.exe -m uvicorn `
  services.payment_api.main:app `
  --host 127.0.0.1 `
  --port 8010
```

Verify the service:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
Invoke-RestMethod http://127.0.0.1:8010/ready
```

The Swagger page is at `http://127.0.0.1:8010/docs`.

## Send a payment

```powershell
$payment = @{
  order_id = "order-1001"
  user_id = "user-1001"
  amount = 9900
  currency = "CNY"
  channel = "card"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8010/pay `
  -ContentType "application/json" `
  -Body $payment
```

`amount` uses the currency's smallest unit. For CNY, `9900` means CNY 99.00.
Sending the same `order_id` again returns the original payment.

## Generate traffic

```powershell
.\.venv\Scripts\python.exe -m `
  services.payment_api.traffic_generator `
  --rps 3
```

Use `--requests 20` for a finite smoke test.

## Ship logs to Loki

```powershell
.\.venv\Scripts\python.exe scripts\ship_logs_to_loki.py `
  --log-file app/data/payment-api.log `
  --follow
```

The local Prometheus configuration scrapes
`host.docker.internal:8010/metrics`. Reload the running Prometheus container
after this change:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:9090/-/reload
```

## Run a controlled fault drill

The service refuses all fault administration calls unless
`PAYMENT_API_ENABLE_FAULT_INJECTION=true` and a token of at least 16
characters is configured.

```powershell
.\scripts\inject_payment_5xx.ps1 -Ratio 0.70
.\scripts\inject_payment_latency.ps1 -DelayMs 2500
.\scripts\reset_payment_faults.ps1
```

Fault injection is rejected when `PAYMENT_API_ENV=production`.
