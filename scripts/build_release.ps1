[CmdletBinding()]
param(
    [string]$Repository = $env:GITHUB_REPOSITORY,
    [string]$Tag = "",
    [string]$Python = "python",
    [string]$OutputRoot = "",
    [switch]$Clean,
    [string]$SignToolPath = "",
    [string]$CertificateSha1 = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "Repository must be configured as owner/repository before an online release can be built."
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot "release"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$releaseRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "release"))
$releasePrefix = $releaseRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not (
    $OutputRoot.Equals($releaseRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $OutputRoot.StartsWith($releasePrefix, [System.StringComparison]::OrdinalIgnoreCase)
)) {
    throw "OutputRoot must be the repository's release directory or one of its child directories."
}
if ($Clean -and (Test-Path -LiteralPath $OutputRoot)) {
    Remove-Item -LiteralPath $OutputRoot -Recurse -Force
}
$buildRoot = Join-Path $OutputRoot "build"
$artifactRoot = Join-Path $OutputRoot "artifacts"
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
if (Test-Path -LiteralPath $artifactRoot) {
    Remove-Item -LiteralPath $artifactRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null

$signArguments = @{}
if (-not [string]::IsNullOrWhiteSpace($CertificateSha1)) {
    $signArguments.CertificateSha1 = $CertificateSha1
    $signArguments.SignToolPath = $SignToolPath
    $signArguments.TimestampUrl = $TimestampUrl
}

& (Join-Path $PSScriptRoot "build_app.ps1") -Python $Python -OutputRoot $buildRoot @signArguments | Out-Null
$installerOutput = Join-Path $buildRoot "installer"
& (Join-Path $PSScriptRoot "build_installer.ps1") -Repository $Repository -OutputRoot $installerOutput @signArguments | Out-Null

$appDirectory = Join-Path $buildRoot "app\School CSM Control Center"
$installer = Join-Path $installerOutput "School-CSM-Control-Center-Setup.exe"
$releaseArguments = @(
    (Join-Path $PSScriptRoot "create_release.py"),
    "--app-dir", $appDirectory,
    "--installer", $installer,
    "--output", $artifactRoot,
    "--repository", $Repository
)
if (-not [string]::IsNullOrWhiteSpace($Tag)) {
    $releaseArguments += @("--tag", $Tag)
}
& $Python @releaseArguments
if ($LASTEXITCODE -ne 0) { throw "Release asset creation failed." }

& $Python (Join-Path $PSScriptRoot "verify_release.py") $artifactRoot
if ($LASTEXITCODE -ne 0) { throw "Release verification failed." }

Write-Output $artifactRoot
