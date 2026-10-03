#requires -Version 5.1
[CmdletBinding()]
param([switch]$LinkProject)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'astrbot_windows_common.ps1')

function Get-UvAssetForCurrentArchitecture {
    $architecture = $env:PROCESSOR_ARCHITECTURE
    if ($architecture -eq 'AMD64') {
        return [pscustomobject]@{
            Name = 'uv-x86_64-pc-windows-msvc.zip'
            Sha256 = $script:UvPinnedSha256X64
        }
    }
    throw "安装脚本目前只支持 x64 Windows，当前架构是 $architecture。"
}

function Assert-NoGpuRequirements {
    param([Parameter(Mandatory = $true)][string]$RequirementsPath)

    foreach ($rawLine in Get-Content -LiteralPath $RequirementsPath) {
        $line = ($rawLine -split '#', 2)[0].Trim()
        if (-not $line) { continue }
        if ($line.StartsWith('-')) {
            throw "依赖文件含有不支持的安装选项：$line"
        }
        $match = [regex]::Match($line, '^([A-Za-z0-9][A-Za-z0-9_.-]*)')
        if (-not $match.Success) { throw "无法识别依赖名称：$line" }
        $name = $match.Groups[1].Value.ToLowerInvariant() -replace '[-_.]', ''
        if ($name.StartsWith('torch') -or $name.StartsWith('nvidia') -or
            $name -in @('triton', 'cupy', 'xformers', 'bitsandbytes', 'onnxruntimegpu')) {
            throw "依赖文件要求 GPU 包，已停止安装：$line"
        }
    }
}

function Install-PluginRequirements {
    param([Parameter(Mandatory = $true)]$Layout)

    $requirementsPath = Join-Path $Layout.ProjectRoot 'requirements.txt'
    Assert-AstrbotProjectPath -Layout $Layout -Path $requirementsPath
    if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
        throw "找不到插件依赖文件：$requirementsPath"
    }
    Assert-NoGpuRequirements -RequirementsPath $requirementsPath

    $pythonPath = Join-Path $Layout.UvToolDir 'astrbot\Scripts\python.exe'
    Assert-AstrbotManagedPath -Layout $Layout -Path $pythonPath
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw "找不到 AstrBot 独立 Python：$pythonPath"
    }

    $installOptions = @()
    $redisWheel = Get-ChildItem -LiteralPath $Layout.Downloads -Filter 'redis-5.3.1-*.whl' -File |
        Select-Object -First 1
    if ($redisWheel) {
        # 本地 wheel 已齐时走离线安装，避免重复访问 PyPI。
        $installOptions = @('--offline', '--find-links', $Layout.Downloads)
    } else {
        $installOptions = @('--find-links', $Layout.Downloads)
    }

    $dryRunArguments = @('pip', 'install') + $installOptions + @(
        '--dry-run', '--python', $pythonPath, '-r', $requirementsPath
    )
    $dryRun = Invoke-AstrbotLoggedCommand -FilePath $Layout.Uv -ArgumentList $dryRunArguments `
        -WorkingDirectory $Layout.Root -LogPrefix 'plugin-deps-check'
    $plan = Get-Content -LiteralPath $dryRun.Stdout -Raw
    if ($plan -match '(?m)^\s*-\s+[A-Za-z0-9_.-]+==') {
        throw "安装插件依赖会移除 AstrBot 当前包；已停止。请检查：$($dryRun.Stdout)"
    }
    if ($plan -match '(?im)^\s*\+\s+(?:torch\S*|nvidia\S*|triton\S*|cupy\S*|xformers\S*|bitsandbytes\S*)==') {
        throw "依赖计划包含 GPU 包；已停止。请检查：$($dryRun.Stdout)"
    }

    $installArguments = @('pip', 'install') + $installOptions + @(
        '--python', $pythonPath, '-r', $requirementsPath
    )
    Invoke-AstrbotLoggedCommand -FilePath $Layout.Uv -ArgumentList $installArguments `
        -WorkingDirectory $Layout.Root -LogPrefix 'plugin-deps-install' | Out-Null
    Invoke-AstrbotLoggedCommand -FilePath $Layout.Uv -ArgumentList @(
        'pip', 'check', '--python', $pythonPath
    ) -WorkingDirectory $Layout.Root -LogPrefix 'plugin-deps-pip-check' | Out-Null

    $importCheck = @'
import importlib.util
import aiosqlite, faiss, fastapi, httpx, jwt, numpy, PIL, pydantic_settings, redis
from app.astrbot_runtime import ChatRuntime
assert importlib.util.find_spec("torch") is None, "unexpected PyTorch install"
import app.main
print("AstrBot 插件依赖、faiss、Pillow、app.main 导入通过")
'@
    Invoke-AstrbotLoggedCommand -FilePath $pythonPath -ArgumentList @('-c', $importCheck) `
        -WorkingDirectory $Layout.ProjectRoot -LogPrefix 'plugin-deps-import-check' | Out-Null
}

function Install-PrivateUv {
    param([Parameter(Mandatory = $true)]$Layout)

    if (Test-Path -LiteralPath $Layout.Uv -PathType Leaf) {
        $versionResult = Invoke-AstrbotLoggedCommand -FilePath $Layout.Uv -ArgumentList @('--version') `
            -WorkingDirectory $Layout.Root -LogPrefix 'uv-version'
        $installedVersion = (Get-Content -LiteralPath $versionResult.Stdout -Raw).Trim()
        if ($installedVersion -match ('^uv\s+' + [regex]::Escape($script:UvPinnedVersion) + '\b')) {
            return $installedVersion
        }
        throw "私有 uv 版本不对：$($Layout.Uv)（$installedVersion）。请先核对该文件再更新。"
    }

    $asset = Get-UvAssetForCurrentArchitecture
    $archive = Join-Path $Layout.Downloads $asset.Name
    $stage = Join-Path $Layout.Downloads "uv-$($script:UvPinnedVersion)-extract"
    Assert-AstrbotManagedPath -Layout $Layout -Path $archive
    Assert-AstrbotManagedPath -Layout $Layout -Path $stage
    $url = "https://github.com/astral-sh/uv/releases/download/$($script:UvPinnedVersion)/$($asset.Name)"
    Write-Host "正在下载私有 uv $($script:UvPinnedVersion)…"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $asset.Sha256) {
        throw "uv 压缩包校验失败，文件保留在：$archive"
    }
    if (Test-Path -LiteralPath $stage) {
        throw "发现旧的 uv 解压目录，请先检查：$stage"
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $stage
    $candidate = Get-ChildItem -LiteralPath $stage -Filter 'uv.exe' -File -Recurse | Select-Object -First 1
    if (-not $candidate) { throw "uv 压缩包中没有 uv.exe：$archive" }
    Copy-Item -LiteralPath $candidate.FullName -Destination $Layout.Uv
    Remove-AstrbotManagedPath -Layout $Layout -Path $stage -Recurse
    Remove-AstrbotManagedPath -Layout $Layout -Path $archive

    $versionResult = Invoke-AstrbotLoggedCommand -FilePath $Layout.Uv -ArgumentList @('--version') `
        -WorkingDirectory $Layout.Root -LogPrefix 'uv-version'
    $installedVersion = (Get-Content -LiteralPath $versionResult.Stdout -Raw).Trim()
    if ($installedVersion -notmatch ('^uv\s+' + [regex]::Escape($script:UvPinnedVersion) + '\b')) {
        throw "下载的 uv 版本不符：$installedVersion"
    }
    return $installedVersion
}

try {
    $layout = Get-AstrbotLayout -Create
    foreach ($path in @($layout.Uv, $layout.UvCache, $layout.UvToolDir, $layout.UvToolBin,
            $layout.UvPythonDir, $layout.Instance, $layout.Logs)) {
        Assert-AstrbotManagedPath -Layout $layout -Path $path
    }
    Set-AstrbotUvEnvironment -Layout $layout
    $uvVersion = Install-PrivateUv -Layout $layout

    Write-Host "正在准备独立 Python 3.12…"
    Invoke-AstrbotLoggedCommand -FilePath $layout.Uv -ArgumentList @('python', 'install', '3.12') `
        -WorkingDirectory $layout.Root -LogPrefix 'python-install' | Out-Null
    $pythonDirectories = @(Get-ChildItem -LiteralPath $layout.UvPythonDir -Directory -Force |
        Where-Object { $_.Name -match '^cpython-3\.12\.\d+-windows-x86_64-none$' })
    if ($pythonDirectories.Count -lt 1) {
        throw "私有运行时里没有 Python 3.12：$($layout.UvPythonDir)"
    }
    $latestPythonDirectory = $pythonDirectories | Sort-Object {
        [version]($_.Name -replace '^cpython-', '' -replace '-windows-x86_64-none$', '')
    } -Descending | Select-Object -First 1
    $managedPython = Join-Path $latestPythonDirectory.FullName 'python.exe'
    if (-not (Test-Path -LiteralPath $managedPython -PathType Leaf)) {
        throw "找不到私有 Python 3.12：$managedPython"
    }
    Assert-AstrbotManagedPath -Layout $layout -Path $managedPython
    Write-Host "正在安装 AstrBot $($script:AstrBotPinnedVersion)…"
    Invoke-AstrbotLoggedCommand -FilePath $layout.Uv -ArgumentList @(
        'tool', 'install', '--force', '--python', $managedPython, "astrbot==$($script:AstrBotPinnedVersion)"
    ) -WorkingDirectory $layout.Root -LogPrefix 'astrbot-install' | Out-Null

    $astrbotExe = Join-Path $layout.UvToolBin 'astrbot.exe'
    if (-not (Test-Path -LiteralPath $astrbotExe -PathType Leaf)) {
        throw "没有找到 AstrBot 启动程序：$astrbotExe"
    }
    Write-Host '正在安装插件依赖并检查兼容性…'
    Install-PluginRequirements -Layout $layout
    Invoke-AstrbotLoggedCommand -FilePath $astrbotExe -ArgumentList @('--version') `
        -WorkingDirectory $layout.Instance -LogPrefix 'astrbot-version' | Out-Null

    $initLog = Invoke-AstrbotLoggedCommand -FilePath $astrbotExe -ArgumentList @('init', '-y') `
        -WorkingDirectory $layout.Instance -LogPrefix 'astrbot-init'
    $projectLinkPath = $null
    if ($LinkProject) {
        $projectLinkPath = Set-AstrbotProjectJunction -Layout $layout
    }

    $manifest = [ordered]@{
        astrbot_version = $script:AstrBotPinnedVersion
        astrbot_release_url = "https://github.com/AstrBotDevs/AstrBot/releases/tag/v$($script:AstrBotPinnedVersion)"
        python_constraint = '3.12'
        uv_version = $uvVersion
        installed_utc = [DateTime]::UtcNow.ToString('o')
        instance_directory = $layout.Instance
        initialization_stdout_log = $initLog.Stdout
        initialization_stderr_log = $initLog.Stderr
        project_junction_path = $projectLinkPath
        runtime_started = $false
        note = 'AstrBot 已安装初始化；服务启动和 NapCat 配置由主任务单独处理。'
    }
    $manifestPath = Join-Path $layout.Root 'install-state.json'
    Assert-AstrbotManagedPath -Layout $layout -Path $manifestPath
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

    Write-Host "AstrBot $($script:AstrBotPinnedVersion) 已安装并初始化。"
    Write-Host "实例目录：$($layout.Instance)"
    if ($projectLinkPath) { Write-Host "插件已接入：$projectLinkPath" }
    Write-Host '未启动 AstrBot、NapCat、旧机器人、桥接服务或 GPU 服务。'
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
