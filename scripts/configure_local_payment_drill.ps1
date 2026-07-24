param(
  [string]$EnvPath = (Join-Path $PSScriptRoot "..\.env")
)

$resolvedEnvPath = [System.IO.Path]::GetFullPath($EnvPath)
if (-not (Test-Path -LiteralPath $resolvedEnvPath)) {
  throw "Environment file not found: $resolvedEnvPath"
}

$lines = [System.Collections.Generic.List[string]]::new()
Get-Content -LiteralPath $resolvedEnvPath | ForEach-Object {
  $lines.Add($_)
}

function Get-EnvValue {
  param([string]$Name)
  $prefix = "$Name="
  foreach ($line in $lines) {
    if ($line.StartsWith($prefix)) {
      return $line.Substring($prefix.Length).Trim()
    }
  }
  return $null
}

function Set-EnvValue {
  param(
    [string]$Name,
    [string]$Value
  )
  $prefix = "$Name="
  for ($index = 0; $index -lt $lines.Count; $index++) {
    if ($lines[$index].StartsWith($prefix)) {
      $lines[$index] = "$prefix$Value"
      return
    }
  }
  $lines.Add("$prefix$Value")
}

function New-Secret {
  $bytes = New-Object byte[] 32
  $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $generator.GetBytes($bytes)
  } finally {
    $generator.Dispose()
  }
  $secret = [Convert]::ToBase64String($bytes)
  $secret = $secret.TrimEnd("=")
  $secret = $secret.Replace("+", "-")
  return $secret.Replace("/", "_")
}

$alertmanagerToken = Get-EnvValue "ALERTMANAGER_WEBHOOK_TOKEN"
if ([string]::IsNullOrWhiteSpace($alertmanagerToken)) {
  $alertmanagerToken = New-Secret
}

$paymentToken = Get-EnvValue "PAYMENT_API_FAULT_ADMIN_TOKEN"
if ([string]::IsNullOrWhiteSpace($paymentToken)) {
  $paymentToken = New-Secret
}

Set-EnvValue "ALERTMANAGER_WEBHOOK_TOKEN" $alertmanagerToken
Set-EnvValue "PAYMENT_API_ENV" "local"
Set-EnvValue "PAYMENT_API_DATABASE_PATH" "app/data/payment-api.db"
Set-EnvValue "PAYMENT_API_LOG_FILE" "app/data/payment-api.log"
Set-EnvValue "PAYMENT_API_ENABLE_FAULT_INJECTION" "true"
Set-EnvValue "PAYMENT_API_REMEDIATION_ENABLED" "true"
Set-EnvValue "PAYMENT_API_BASE_URL" "http://127.0.0.1:8010"
Set-EnvValue "PAYMENT_API_FAULT_ADMIN_TOKEN" $paymentToken
Set-EnvValue "OPS_GRAPH_RUNTIME" "langgraph"
Set-EnvValue "OPS_GRAPH_CHECKPOINTER" "sqlite"
Set-EnvValue "OPS_GRAPH_CHECKPOINT_DB_PATH" "app/data/langgraph_checkpoints.sqlite"

$encoding = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines(
  $resolvedEnvPath,
  $lines,
  $encoding
)

& "$PSScriptRoot\configure_alertmanager_webhook.ps1" `
  -Token $alertmanagerToken

Write-Host "Local payment drill configuration is ready."
Write-Host "- .env updated without printing secrets"
Write-Host "- Alertmanager Docker secret synchronized"
Write-Host "- LangGraph runtime set to sqlite checkpointing"
