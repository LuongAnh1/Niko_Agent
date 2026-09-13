$ErrorActionPreference = "Stop"

$repoScript = Join-Path $PSScriptRoot "claude_context_usage.py"
$fccTools = Join-Path $env:USERPROFILE ".fcc\tools"
$localBin = Join-Path $env:USERPROFILE ".local\bin"
$targetScript = Join-Path $fccTools "fcc_context_usage.py"
$wrapper = Join-Path $localBin "fcc-context.cmd"

if (!(Test-Path -LiteralPath $repoScript)) {
    throw "Cannot find $repoScript"
}

New-Item -ItemType Directory -Force -Path $fccTools | Out-Null
New-Item -ItemType Directory -Force -Path $localBin | Out-Null
Copy-Item -LiteralPath $repoScript -Destination $targetScript -Force

$cmd = "@echo off" + [Environment]::NewLine + "python `"%USERPROFILE%\.fcc\tools\fcc_context_usage.py`" %*" + [Environment]::NewLine
[System.IO.File]::WriteAllText($wrapper, $cmd, [System.Text.UTF8Encoding]::new($false))

Write-Host "Installed fcc-context -> $wrapper"
Write-Host "Try: fcc-context"
Write-Host "For bot JSON: fcc-context --json"