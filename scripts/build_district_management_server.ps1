[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$OutputRoot = "",
    [string]$ArtifactRoot = "",
    [switch]$Clean,
    [string]$SignToolPath = "",
    [string]$CertificateSha1 = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot "release\district-build"
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot "release"))
$releasePrefix = $releaseRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $OutputRoot.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputRoot must be a dedicated directory inside the repository release directory."
}
if ($Clean -and (Test-Path -LiteralPath $OutputRoot)) {
    Remove-Item -LiteralPath $OutputRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$requirements = Join-Path $repoRoot "district_management_server\requirements-lock.txt"
& $Python (Join-Path $repoRoot "scripts\verify_build_environment.py") `
    --profile district --requirements $requirements
if ($LASTEXITCODE -ne 0) { throw "The district build environment does not satisfy its lock file." }

& $Python (Join-Path $repoRoot "scripts\fetch_cloudflared.py") | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not fetch or verify the pinned tunnel component." }

$versionInfo = Join-Path $repoRoot "packaging\pyinstaller\district_version_info.txt"
& $Python (Join-Path $repoRoot "scripts\generate_version_info.py") `
    --output $versionInfo `
    --product-name "School CSM District Management Server" `
    --original-filename "School CSM District Management Server.exe" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not generate district Windows version information." }

$workRoot = Join-Path $OutputRoot "pyinstaller-work"
$distRoot = Join-Path $OutputRoot "app"
$spec = Join-Path $repoRoot "packaging\pyinstaller\DistrictManagementServer.spec"
& $Python -m PyInstaller --noconfirm --clean --workpath $workRoot --distpath $distRoot $spec
if ($LASTEXITCODE -ne 0) { throw "District Management Server PyInstaller build failed." }

$pyinstallerWarnings = Join-Path $workRoot "DistrictManagementServer\warn-DistrictManagementServer.txt"
& $Python (Join-Path $repoRoot "scripts\verify_build_environment.py") `
    --profile district --warnings-only --pyinstaller-warnings $pyinstallerWarnings
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller omitted a required district runtime dependency."
}

$appDirectory = Join-Path $distRoot "School CSM District Management Server"
$executable = Join-Path $appDirectory "School CSM District Management Server.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Expected compiled district executable was not created: $executable"
}
$bundledTunnel = Join-Path $appDirectory "vendor\cloudflared\cloudflared.exe"
$sourceTunnel = Join-Path $repoRoot "vendor\cloudflared\cloudflared.exe"
if (-not (Test-Path -LiteralPath $bundledTunnel -PathType Leaf)) {
    throw "The compiled district application is missing cloudflared."
}
if ((Get-FileHash $bundledTunnel -Algorithm SHA256).Hash -ne (Get-FileHash $sourceTunnel -Algorithm SHA256).Hash) {
    throw "The bundled district tunnel component does not match the verified input."
}

$selfTestProcess = Start-Process -FilePath $executable -ArgumentList '--runtime-self-test' `
    -WindowStyle Hidden -PassThru
if (-not $selfTestProcess.WaitForExit(60000)) {
    $selfTestProcess.Kill()
    throw "The compiled district runtime self-test did not finish within 60 seconds."
}
if ($selfTestProcess.ExitCode -ne 0) {
    throw "The compiled district registration service failed its frozen-runtime self-test."
}

if (-not [string]::IsNullOrWhiteSpace($CertificateSha1)) {
    if ([string]::IsNullOrWhiteSpace($SignToolPath)) {
        $found = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($null -eq $found) { throw "signtool.exe was not found." }
        $SignToolPath = $found.Source
    }
    & $SignToolPath sign /sha1 $CertificateSha1 /fd SHA256 /tr $TimestampUrl /td SHA256 $executable
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed for the district executable." }
}

if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $artifactRoot = Join-Path $OutputRoot "artifacts"
} else {
    $artifactRoot = [IO.Path]::GetFullPath($ArtifactRoot)
    if (-not $artifactRoot.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "ArtifactRoot must be inside the repository release directory."
    }
}
New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null
& $Python (Join-Path $repoRoot "scripts\package_district_management_server.py") `
    --app-dir $appDirectory --output $artifactRoot
if ($LASTEXITCODE -ne 0) { throw "Could not package the district application." }

Write-Output $appDirectory
Write-Output $artifactRoot
