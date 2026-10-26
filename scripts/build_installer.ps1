[CmdletBinding()]
param(
    [string]$Repository = $env:GITHUB_REPOSITORY,
    [string]$OutputRoot = "",
    [string]$InstallerVersion = "1.0.1.0",
    [ValidateSet("School", "District")]
    [string]$Product = "School",
    [string]$CSharpCompiler = "",
    [string]$SignToolPath = "",
    [string]$CertificateSha1 = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "Repository must be configured as owner/repository. Example: -Repository MoSSLab/School-CSM-Control-Center"
}
if ($InstallerVersion -notmatch '^\d+\.\d+\.\d+\.\d+$') {
    throw "InstallerVersion must contain four numeric components, for example 1.0.0.0."
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot $(if ($Product -eq 'District') { 'release\build\district-installer' } else { 'release\build\installer' })
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

if ([string]::IsNullOrWhiteSpace($CSharpCompiler)) {
    $CSharpCompiler = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path -LiteralPath $CSharpCompiler -PathType Leaf)) {
    throw ".NET Framework C# compiler was not found: $CSharpCompiler"
}

$installerSource = Join-Path $repoRoot "packaging\installer"
$template = Get-Content -LiteralPath (Join-Path $installerSource "GeneratedConfig.cs.in") -Raw
$generatedConfig = $template.Replace("@@REPOSITORY@@", $Repository).Replace("@@INSTALLER_VERSION@@", $InstallerVersion)
$displayName = if ($Product -eq 'District') { 'School CSM District Management Server' } else { 'School CSM Control Center' }
$applicationId = if ($Product -eq 'District') { 'MoSSLab.SchoolCSMDistrictManagementServer' } else { 'MoSSLab.SchoolCSMControlCenter' }
$setupName = if ($Product -eq 'District') { 'School-CSM-District-Management-Server-Setup.exe' } else { 'School-CSM-Control-Center-Setup.exe' }
$manifestName = if ($Product -eq 'District') { 'district-release.json' } else { 'release.json' }
$generatedConfig = $generatedConfig.Replace('@@APPLICATION_ID@@', $applicationId).Replace('@@DISPLAY_NAME@@', $displayName).Replace('@@APPLICATION_EXE@@', ($displayName + '.exe')).Replace('@@SETUP_EXE@@', $setupName).Replace('@@MANIFEST_NAME@@', $manifestName).Replace('@@IS_DISTRICT@@', $(if ($Product -eq 'District') { 'true' } else { 'false' }))
$generatedPath = Join-Path $OutputRoot "GeneratedConfig.cs"
[System.IO.File]::WriteAllText($generatedPath, $generatedConfig, [System.Text.UTF8Encoding]::new($false))
$assemblyTemplate = Get-Content -LiteralPath (Join-Path $installerSource "AssemblyInfo.cs.in") -Raw
$generatedAssembly = $assemblyTemplate.Replace("@@INSTALLER_VERSION@@", $InstallerVersion)
$generatedAssembly = $generatedAssembly.Replace('School CSM Control Center', $displayName)
$generatedAssemblyPath = Join-Path $OutputRoot "AssemblyInfo.cs"
[System.IO.File]::WriteAllText($generatedAssemblyPath, $generatedAssembly, [System.Text.UTF8Encoding]::new($false))

$frameworkRoot = Split-Path -Parent $CSharpCompiler
$outputExe = Join-Path $OutputRoot $setupName
$manifestPath = Join-Path $installerSource 'app.manifest'
if ($Product -eq 'District') {
    $manifestText = (Get-Content -LiteralPath $manifestPath -Raw).Replace('MoSSLab.SchoolCSMControlCenter.Setup', ($applicationId + '.Setup')).Replace('School CSM Control Center', $displayName)
    $manifestPath = Join-Path $OutputRoot 'district-app.manifest'
    [IO.File]::WriteAllText($manifestPath, $manifestText, [Text.UTF8Encoding]::new($false))
}
$sourceFiles = @(
    (Join-Path $installerSource "Program.cs"),
    (Join-Path $installerSource "ReleaseManifest.cs"),
    (Join-Path $installerSource "ProviderConfiguration.cs"),
    (Join-Path $installerSource "WindowsIntegration.cs"),
    (Join-Path $installerSource "ResilientDownloader.cs"),
    (Join-Path $installerSource "InstallerEngine.cs"),
    (Join-Path $installerSource "MaintenanceForm.cs"),
    $generatedPath,
    $generatedAssemblyPath
)
$references = @(
    "System.dll",
    "System.Core.dll",
    "System.Drawing.dll",
    "System.Windows.Forms.dll",
    "System.Runtime.Serialization.dll",
    "System.IO.Compression.dll",
    "System.IO.Compression.FileSystem.dll"
)
$compilerArgs = @(
    "/nologo",
    "/target:winexe",
    "/platform:x86",
    "/optimize+",
    "/debug-",
    "/utf8output",
    "/nowarn:0649",
    "/warnaserror+",
    ("/win32manifest:" + $manifestPath),
    ("/win32icon:" + (Join-Path $repoRoot "School CSM Control Center Icon.ico")),
    ("/out:" + $outputExe)
)
foreach ($reference in $references) {
    $referencePath = Join-Path $frameworkRoot $reference
    if (-not (Test-Path -LiteralPath $referencePath -PathType Leaf)) {
        throw "Required .NET Framework assembly was not found: $referencePath"
    }
    $compilerArgs += ("/reference:" + $referencePath)
}
$compilerArgs += $sourceFiles

& $CSharpCompiler @compilerArgs
if ($LASTEXITCODE -ne 0) { throw "The online maintenance installer did not compile." }
if (-not (Test-Path -LiteralPath $outputExe -PathType Leaf)) {
    throw "Expected setup executable was not created: $outputExe"
}

if (-not [string]::IsNullOrWhiteSpace($CertificateSha1)) {
    if ([string]::IsNullOrWhiteSpace($SignToolPath)) {
        $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($null -eq $command) { throw "signtool.exe was not found." }
        $SignToolPath = $command.Source
    }
    & $SignToolPath sign /sha1 $CertificateSha1 /fd SHA256 /tr $TimestampUrl /td SHA256 $outputExe
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed for the setup executable." }
}

Write-Output $outputExe
