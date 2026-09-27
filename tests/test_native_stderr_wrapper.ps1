[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Assert-NativeExitCode {
    param([int]$ExpectedExitCode)

    # Exercise the trainer's direct native invocation pattern, including an
    # argument with spaces and a warning on stderr under Stop preference.
    $pythonPath = Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw "Test Python not found: $pythonPath"
    }
    # Avoid quote characters inside -c: Windows PowerShell 5.1 can strip
    # them when it marshals arguments to a native executable.
    $program = 'import sys; print(sys.argv[1]); print(sys.argv[1], file=sys.stderr); sys.exit(int(sys.argv[2]))'
    $originalErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $pythonPath -c $program "c10d warning path with spaces" $ExpectedExitCode 2>&1 |
            Tee-Object -Variable captured)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $originalErrorActionPreference
    }
    if ($exitCode -ne $ExpectedExitCode) {
        throw "Expected native exit code $ExpectedExitCode, got $exitCode. Output: $output"
    }
    if (-not (($output -join "`n") -match "c10d warning path with spaces")) {
        throw "The spaced argument was not preserved by native invocation."
    }
    if (-not (($output -join "`n") -match "c10d warning")) {
        throw "Native stderr was not retained in the merged log output."
    }
}

Assert-NativeExitCode -ExpectedExitCode 0
Assert-NativeExitCode -ExpectedExitCode 7
Write-Host "PASS: native stderr logging preserves actual exit codes"
