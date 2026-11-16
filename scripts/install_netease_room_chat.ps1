[CmdletBinding()]
param([switch]$NativeSdk)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtime = [IO.Path]::GetFullPath((Join-Path $projectRoot 'data\netease\nim-runtime'))
if (-not $runtime.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Room chat dependency path is outside the project'
}
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
foreach ($name in @('package.json', 'package-lock.json')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ('integrations\netease\realtime\' + $name)) -Destination (Join-Path $runtime $name) -Force
}
Push-Location -LiteralPath $runtime
try {
    & npm.cmd ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=30000
    if ($LASTEXITCODE -ne 0) { throw 'Room chat dependency download failed' }
    if ($NativeSdk) {
        $sdkRoot = [IO.Path]::GetFullPath((Join-Path $runtime 'node_modules\node-nim'))
        foreach ($name in @('temporary', 'build\Release')) {
            $sdkTarget = [IO.Path]::GetFullPath((Join-Path $sdkRoot $name))
            if (-not $sdkTarget.StartsWith($sdkRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Invalid native SDK install target'
            }
        }
        & node.exe -e 'process.env.npm_config_nimsdkversion="10.10.13";require("axios").defaults.timeout=45000;require("node-nim/script/download-sdk.js").downloadSDK().catch(()=>{process.exitCode=1})'
        if ($LASTEXITCODE -ne 0) { throw 'Official native room chat SDK install failed' }
    }
    Write-Host '[NETEASE] Room chat JS dependencies ready; native SDK install is a separate step.'
} finally { Pop-Location }
