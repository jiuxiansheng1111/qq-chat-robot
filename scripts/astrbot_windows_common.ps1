# 运行时只放在项目 data/astrbot 下。
# 2026-10-04 核对的最新正式版是 AstrBot 4.28.2。

$script:AstrBotPinnedVersion = '4.28.2'
$script:UvPinnedVersion = '0.12.23'
$script:UvPinnedSha256X64 = '75d05de6762778c31ee183398de7dd15093fad0ed90b1f236d8205ea5ec00c90'

function Get-AstrbotLayout {
    [CmdletBinding()]
    param([switch]$Create)

    $projectCandidate = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $projectRoot = (Resolve-Path -LiteralPath $projectCandidate -ErrorAction Stop).Path.TrimEnd('\')
    $dataRoot = Join-Path $projectRoot 'data'
    if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) {
        if (-not $Create) { throw "找不到 AstrBot 数据目录：$dataRoot" }
        New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    }
    $dataItem = Get-Item -LiteralPath $dataRoot -Force -ErrorAction Stop
    if (($dataItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "data 目录是链接，已停止：$dataRoot"
    }

    $managedRoot = [IO.Path]::GetFullPath((Join-Path $dataRoot 'astrbot'))
    $layout = [ordered]@{
        ProjectRoot = $projectRoot
        DataRoot = $dataRoot
        Root = $managedRoot
        Runtime = (Join-Path $managedRoot 'runtime')
        Downloads = (Join-Path $managedRoot 'runtime\downloads')
        Uv = (Join-Path $managedRoot 'runtime\tools\uv.exe')
        UvDir = (Join-Path $managedRoot 'runtime\tools')
        UvCache = (Join-Path $managedRoot 'runtime\cache')
        UvToolDir = (Join-Path $managedRoot 'runtime\tool-envs')
        UvToolBin = (Join-Path $managedRoot 'runtime\bin')
        UvPythonDir = (Join-Path $managedRoot 'runtime\python')
        Instance = (Join-Path $managedRoot 'instance')
        PluginDir = (Join-Path $managedRoot 'instance\data\plugins')
        Logs = (Join-Path $managedRoot 'logs')
    }

    if ($Create -and -not (Test-Path -LiteralPath $managedRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $managedRoot -Force | Out-Null
    }
    if (Test-Path -LiteralPath $managedRoot) {
        $rootItem = Get-Item -LiteralPath $managedRoot -Force -ErrorAction Stop
        if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "AstrBot 目录是链接，已停止：$managedRoot"
        }
    }

    foreach ($key in @($layout.Keys | Where-Object { $_ -notin @('ProjectRoot', 'DataRoot') })) {
        Assert-AstrbotManagedPath -Layout $layout -Path ([string]$layout[$key])
    }
    if ($Create) {
        foreach ($directory in @($layout.Runtime, $layout.Downloads, $layout.UvDir, $layout.UvCache,
                $layout.UvToolDir, $layout.UvToolBin, $layout.UvPythonDir, $layout.Instance,
                $layout.PluginDir, $layout.Logs)) {
            Ensure-AstrbotDirectory -Layout $layout -Path $directory
        }
    }
    return [pscustomobject]$layout
}

function Assert-AstrbotManagedPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Layout,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath([string]$Layout.Root).TrimEnd('\')
    $prefix = $root + '\'
    if (-not $fullPath.Equals($root, [StringComparison]::OrdinalIgnoreCase) -and
        -not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "路径不在 data/astrbot 内，已停止：$fullPath"
    }

    $relative = if ($fullPath.Equals($root, [StringComparison]::OrdinalIgnoreCase)) {
        ''
    } else {
        $fullPath.Substring($prefix.Length)
    }
    $cursor = $root
    if (Test-Path -LiteralPath $cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "路径包含链接，已停止：$cursor"
        }
    }
    foreach ($part in @($relative -split '[\\/]+' | Where-Object { $_ })) {
        $cursor = Join-Path $cursor $part
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "路径包含链接，已停止：$cursor"
            }
        }
    }
}

function Ensure-AstrbotDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Layout,
        [Parameter(Mandatory = $true)][string]$Path
    )
    Assert-AstrbotManagedPath -Layout $Layout -Path $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
    Assert-AstrbotManagedPath -Layout $Layout -Path $Path
}

function Remove-AstrbotManagedPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Layout,
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$Recurse
    )
    Assert-AstrbotManagedPath -Layout $Layout -Path $Path
    if (-not (Test-Path -LiteralPath $Path)) { return }
    if ($Recurse) {
        $nestedReparsePoint = Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop |
            Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 } |
            Select-Object -First 1
        if ($nestedReparsePoint) {
            throw "目录里有链接，不能安全清理：$($nestedReparsePoint.FullName)"
        }
    }
    Remove-Item -LiteralPath $Path -Force -Recurse:$Recurse
}

function Set-AstrbotUvEnvironment {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Layout)
    $env:UV_CACHE_DIR = [string]$Layout.UvCache
    $env:UV_TOOL_DIR = [string]$Layout.UvToolDir
    $env:UV_TOOL_BIN_DIR = [string]$Layout.UvToolBin
    $env:UV_PYTHON_INSTALL_DIR = [string]$Layout.UvPythonDir
    Remove-Item Env:UV_PYTHON_PREFERENCE -ErrorAction SilentlyContinue
}

function Assert-AstrbotProjectPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Layout,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $fullPath = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath([string]$Layout.ProjectRoot).TrimEnd('\')
    $prefix = $root + '\'
    if (-not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "项目文件不在仓库目录内：$fullPath"
    }
    $cursor = $root
    foreach ($part in @($fullPath.Substring($prefix.Length) -split '[\\/]+' | Where-Object { $_ })) {
        $cursor = Join-Path $cursor $part
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "项目文件路径包含链接：$cursor"
            }
        }
    }
}

function ConvertTo-AstrbotWindowsArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $backslashes++; continue }
        if ($character -eq '"') {
            if ($backslashes -gt 0) { [void]$builder.Append(('\' * (2 * $backslashes))) }
            [void]$builder.Append('\')
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) { [void]$builder.Append(('\' * (2 * $backslashes))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-AstrbotDirectoryLinkTarget {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -or
        ($item.LinkType -and $item.LinkType -ne 'Junction')) {
        throw "目标位置不是目录 Junction：$Path"
    }
    $targetProperty = $item.PSObject.Properties['Target']
    if ($targetProperty -and $item.Target) {
        $target = [string](@($item.Target)[0])
        if (-not [IO.Path]::IsPathRooted($target)) {
            $target = Join-Path (Split-Path -Parent $Path) $target
        }
        return [IO.Path]::GetFullPath($target).TrimEnd('\')
    }

    # PowerShell 5.1 没有 Target 属性时，从 Windows 重解析点读取目标。
    if (-not ('AstrbotJunctionReader' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class AstrbotJunctionReader {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string path, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(
        SafeFileHandle handle, uint code, IntPtr input, uint inputSize,
        byte[] output, uint outputSize, out uint returned, IntPtr overlapped);

    public static string GetTarget(string path) {
        using (SafeFileHandle handle = CreateFile(path, 0, 7, IntPtr.Zero, 3,
                0x02000000 | 0x00200000, IntPtr.Zero)) {
            if (handle.IsInvalid) throw new IOException("无法读取目录链接", Marshal.GetLastWin32Error());
            byte[] data = new byte[16384];
            uint returned;
            if (!DeviceIoControl(handle, 0x000900A8, IntPtr.Zero, 0,
                    data, (uint)data.Length, out returned, IntPtr.Zero)) {
                throw new IOException("无法读取目录链接", Marshal.GetLastWin32Error());
            }
            if (BitConverter.ToUInt32(data, 0) != 0xA0000003 || returned < 16)
                throw new IOException("目标不是目录 Junction");
            int nameOffset = BitConverter.ToUInt16(data, 8);
            int nameLength = BitConverter.ToUInt16(data, 10);
            if (nameLength == 0 || 16 + nameOffset + nameLength > returned)
                throw new IOException("Junction 目标数据无效");
            string target = Encoding.Unicode.GetString(data, 16 + nameOffset, nameLength);
            if (target.StartsWith(@"\??\UNC\", StringComparison.OrdinalIgnoreCase))
                return @"\" + target.Substring(8);
            if (target.StartsWith(@"\??\", StringComparison.OrdinalIgnoreCase))
                return target.Substring(4);
            return target;
        }
    }
}
'@ -ErrorAction Stop
    }
    $target = [AstrbotJunctionReader]::GetTarget([IO.Path]::GetFullPath($Path))
    return [IO.Path]::GetFullPath($target).TrimEnd('\')
}

function Set-AstrbotProjectJunction {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Layout)

    $projectFiles = @('main.py', 'metadata.yaml', 'requirements.txt')
    foreach ($name in $projectFiles) {
        Assert-AstrbotProjectPath -Layout $Layout -Path (Join-Path $Layout.ProjectRoot $name)
        if (-not (Test-Path -LiteralPath (Join-Path $Layout.ProjectRoot $name) -PathType Leaf)) {
            throw "插件文件还不齐：$name"
        }
    }

    Ensure-AstrbotDirectory -Layout $Layout -Path $Layout.PluginDir
    $linkPath = Join-Path $Layout.PluginDir 'astrbot_plugin_qq_chatrobot'
    $fullLinkPath = [IO.Path]::GetFullPath($linkPath)
    $root = [IO.Path]::GetFullPath([string]$Layout.Root).TrimEnd('\')
    if (-not $fullLinkPath.StartsWith(($root + '\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw "插件链接不在 AstrBot 插件目录内：$fullLinkPath"
    }
    Assert-AstrbotManagedPath -Layout $Layout -Path $Layout.PluginDir

    $expectedTarget = [IO.Path]::GetFullPath([string]$Layout.ProjectRoot).TrimEnd('\')
    if (Test-Path -LiteralPath $fullLinkPath) {
        $actualTarget = Get-AstrbotDirectoryLinkTarget -Path $fullLinkPath
        if ($actualTarget.Equals($expectedTarget, [StringComparison]::OrdinalIgnoreCase)) {
            return $fullLinkPath
        }
        # 只删除链接本身，绝不递归跟进链接目标。
        Remove-Item -LiteralPath $fullLinkPath -Force
    } elseif (Get-Item -LiteralPath $fullLinkPath -Force -ErrorAction SilentlyContinue) {
        throw "插件链接是断开的，已停止：$fullLinkPath"
    }

    New-Item -ItemType Junction -Path $fullLinkPath -Target $expectedTarget | Out-Null
    $actualTarget = Get-AstrbotDirectoryLinkTarget -Path $fullLinkPath
    if (-not $actualTarget.Equals($expectedTarget, [StringComparison]::OrdinalIgnoreCase)) {
        throw "插件 Junction 指向意外位置：$actualTarget"
    }
    return $fullLinkPath
}

function Invoke-AstrbotLoggedCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$LogPrefix,
        [string]$LogDirectory
    )
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    if (-not $LogDirectory) { $LogDirectory = $WorkingDirectory }
    $stdoutPath = Join-Path $LogDirectory ("$LogPrefix-$stamp.stdout.log")
    $stderrPath = Join-Path $LogDirectory ("$LogPrefix-$stamp.stderr.log")
    $nativeArguments = @($ArgumentList | ForEach-Object { ConvertTo-AstrbotWindowsArgument -Value $_ }) -join ' '
    $process = Start-Process -FilePath $FilePath -ArgumentList $nativeArguments -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "命令失败（退出码 $($process.ExitCode)），日志：$stdoutPath、$stderrPath"
    }
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $stdoutPath; Stderr = $stderrPath }
}
