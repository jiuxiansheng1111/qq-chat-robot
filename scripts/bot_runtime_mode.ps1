# 本机选择入口，避免旧脚本重新拉起另一份机器人。
function Get-QQChatRobotRuntime {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $modePath = Join-Path $ProjectRoot 'data\astrbot\active-mode.txt'
    if (-not (Test-Path -LiteralPath $modePath -PathType Leaf)) { return 'fastapi' }
    $mode = [IO.File]::ReadAllText($modePath).Trim()
    if ($mode -notin @('astrbot', 'fastapi')) {
        throw '机器人启动模式无效，请检查 data/astrbot/active-mode.txt。'
    }
    return $mode
}
