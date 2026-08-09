[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$OutputRoot = "",
    [switch]$Clean,
    [string]$SignToolPath = "",
    [string]$CertificateSha1 = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot "release\build"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$repoPrefix = $repoRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $OutputRoot.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputRoot must be a dedicated directory inside the repository."
}
$workRoot = Join-Path $OutputRoot "pyinstaller-work"
$distRoot = Join-Path $OutputRoot "app"
$specPath = Join-Path $repoRoot "packaging\pyinstaller\SchoolCSMControlCenter.spec"

if ($Clean -and (Test-Path -LiteralPath $OutputRoot)) {
    Remove-Item -LiteralPath $OutputRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

& $Python (Join-Path $repoRoot "scripts\generate_version_info.py") | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not generate Windows version information." }

& $Python -m PyInstaller --noconfirm --clean --workpath $workRoot --distpath $distRoot $specPath
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$appDirectory = Join-Path $distRoot "School CSM Control Center"
$appExecutable = Join-Path $appDirectory "School CSM Control Center.exe"
if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
    throw "Expected executable was not created: $appExecutable"
}

$allowedOpenCvRuntimeModules = @(
    "cv2/__init__.py",
    "cv2/config-3.py",
    "cv2/config.py",
    "cv2/data/__init__.py",
    "cv2/gapi/__init__.py",
    "cv2/load_config_py3.py",
    "cv2/mat_wrapper/__init__.py",
    "cv2/misc/__init__.py",
    "cv2/misc/version.py",
    "cv2/typing/__init__.py",
    "cv2/utils/__init__.py",
    "cv2/version.py"
)
$forbiddenExtensions = @(".py", ".pyw", ".pyc", ".pyo", ".cmd", ".bat", ".vbs", ".ps1", ".psm1", ".sh")
$forbidden = Get-ChildItem -LiteralPath $appDirectory -Recurse -File | Where-Object {
    if ($_.Extension -notin $forbiddenExtensions) { return $false }
    $relative = $_.FullName.Substring($appDirectory.Length + 1).Replace("\", "/")
    return $relative -notin $allowedOpenCvRuntimeModules
}
if ($forbidden) {
    $names = ($forbidden | ForEach-Object FullName) -join [Environment]::NewLine
    throw "End-user package contains development launch files:`n$names"
}

if (-not [string]::IsNullOrWhiteSpace($CertificateSha1)) {
    if ([string]::IsNullOrWhiteSpace($SignToolPath)) {
        $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($null -eq $command) { throw "signtool.exe was not found." }
        $SignToolPath = $command.Source
    }
    & $SignToolPath sign /sha1 $CertificateSha1 /fd SHA256 /tr $TimestampUrl /td SHA256 $appExecutable
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed for the application executable." }
}

Write-Output $appDirectory
