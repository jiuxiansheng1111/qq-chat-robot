param(
    [string]$BotQQ = "3503565007",
    [int]$ApiPort = 3001
)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

$envPath = Join-Path (Get-Location) ".env"
$examplePath = Join-Path (Get-Location) ".env.example"

if (-not (Test-Path $envPath)) {
    if (-not (Test-Path $examplePath)) {
        throw "找不到 .env 和 .env.example"
    }
    Copy-Item $examplePath $envPath
    Write-Host "已从 .env.example 创建 .env" -ForegroundColor Green
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = "$envPath.bak-$stamp"
Copy-Item $envPath $backupPath -Force
Write-Host "已备份 .env -> $backupPath" -ForegroundColor DarkGray

function New-RandomToken {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes)
    }
    finally {
        $rng.Dispose()
    }
}

function Set-EnvValue {
    param(
        [string]$Name,
        [string]$Value
    )

    $lines = @()
    if (Test-Path $envPath) {
        $lines = Get-Content $envPath -Encoding UTF8
    }

    $escaped = [Regex]::Escape($Name)
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^$escaped=") {
            $found = $true
            "$Name=$Value"
        }
        else {
            $line
        }
    }

    if (-not $found) {
        $updated += "$Name=$Value"
    }

    Set-Content -Path $envPath -Value $updated -Encoding UTF8
}

$accessToken = New-RandomToken
$webhookToken = New-RandomToken
$apiBase = "http://127.0.0.1:$ApiPort"

Set-EnvValue "ONEBOT_SELF_ID_2" $BotQQ
Set-EnvValue "ONEBOT_API_BASE_2" $apiBase
Set-EnvValue "ONEBOT_ACCESS_TOKEN_2" $accessToken
Set-EnvValue "ONEBOT_WEBHOOK_TOKEN_2" $webhookToken

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "第二个 QQ 机器人配置已生成" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "QQ号:              $BotQQ"
Write-Host "HTTP Server:       $apiBase"
Write-Host "Access Token:      $accessToken"
Write-Host "Webhook Token:     $webhookToken"
Write-Host ""
Write-Host "NapCat 第二个账号请这样填：" -ForegroundColor Yellow
Write-Host ""
Write-Host "[HTTP 服务端]"
Write-Host "Host: 127.0.0.1"
Write-Host "Port: $ApiPort"
Write-Host "Token: $accessToken"
Write-Host "Message post format: array"
Write-Host ""
Write-Host "[HTTP 客户端]"
Write-Host "URL: http://127.0.0.1:8000/onebot/webhook"
Write-Host "Token: $webhookToken"
Write-Host "Message post format: array"
Write-Host "Report self message: false"
Write-Host ""
Write-Host "已写入: $envPath" -ForegroundColor Green
Write-Host "旧配置没有删除。" -ForegroundColor Green
Write-Host ""
Write-Host "完成后重启机器人服务即可。" -ForegroundColor Cyan
