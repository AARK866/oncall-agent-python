param(
  [string]$Token = $env:PAYMENT_API_FAULT_ADMIN_TOKEN,
  [string]$BaseUrl = "http://127.0.0.1:8010"
)

if ([string]::IsNullOrWhiteSpace($Token)) {
  throw "Set PAYMENT_API_FAULT_ADMIN_TOKEN or pass -Token."
}

Invoke-RestMethod `
  -Method Post `
  -Uri "$BaseUrl/admin/fault/reset" `
  -Headers @{ "X-Admin-Token" = $Token }
