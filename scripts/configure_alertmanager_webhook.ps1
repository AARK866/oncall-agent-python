param(
  [Parameter(Mandatory = $true)]
  [string]$Token
)

if ($Token.Length -lt 16) {
  throw "Alertmanager webhook token must contain at least 16 characters."
}

$observabilityDirectory = Join-Path $PSScriptRoot "..\deploy\observability"
$observabilityDirectory = [System.IO.Path]::GetFullPath(
  $observabilityDirectory
)
$secretsDirectory = Join-Path $observabilityDirectory "secrets"
$tokenPath = Join-Path $secretsDirectory "alertmanager-webhook-token"

[System.IO.Directory]::CreateDirectory($secretsDirectory) | Out-Null
$encoding = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($tokenPath, $Token, $encoding)

Write-Host "Alertmanager credential file synchronized:"
Write-Host $tokenPath
Write-Host "Set the same value as ALERTMANAGER_WEBHOOK_TOKEN and restart the Agent."
