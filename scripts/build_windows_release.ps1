# Build a sanitized Windows release archive from version-controlled runtime files.
[CmdletBinding()]
param(
    [string]$Version = "0.1.0-alpha.3",
    [string]$SourceRoot = "",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

if (-not $SourceRoot) {
    $SourceRoot = Split-Path -Parent $PSScriptRoot
}
$SourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $SourceRoot "dist"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)

$packageName = "JARVIS-v$Version-windows"
$buildRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $SourceRoot "scratch\release-build")
)
$packageRoot = Join-Path $buildRoot $packageName
$archivePath = Join-Path $OutputDirectory "$packageName.zip"
$checksumPath = "$archivePath.sha256"

$expectedBuildPrefix = $SourceRoot.TrimEnd("\", "/") +
    [System.IO.Path]::DirectorySeparatorChar
if (-not $buildRoot.StartsWith(
    $expectedBuildPrefix,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Release build directory must stay inside the source root."
}

$runtimeFiles = @(
    ".env.example",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "Install-JARVIS.bat",
    "jarvis_settings.py",
    "LICENSE",
    "README.md",
    "requirements.txt",
    "requirements-optional.txt",
    "setup.bat",
    "setup.ps1",
    "Start-JARVIS.bat",
    "start_app.py",
    "THIRD_PARTY_NOTICES.md"
)
$runtimeDirectories = @(
    "media",
    "models",
    "src",
    "third_party"
)
$runtimePaths = @($runtimeFiles + $runtimeDirectories)
$requiredDistributionFiles = @(
    "third_party\licenses\Apache-2.0.txt",
    "third_party\licenses\CC-BY-SA-4.0.txt",
    "third_party\licenses\GPL-3.0.txt",
    "third_party\model_cards\en_GB-northern_english_male-medium.md",
    "third_party\model_cards\es_MX-claude-high.md",
    "third_party\PIPER_VOICES_REPOSITORY_NOTICE.md"
)

if (Test-Path -LiteralPath $buildRoot) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$trackedFiles = @(
    & git -C $SourceRoot ls-files --cached -- @runtimePaths
)
if ($LASTEXITCODE -ne 0 -or -not $trackedFiles) {
    throw "Could not enumerate version-controlled runtime files."
}

foreach ($requiredFile in $runtimeFiles) {
    if ($requiredFile -notin $trackedFiles) {
        throw "Required release file is not tracked: $requiredFile"
    }
}
foreach ($requiredFile in $requiredDistributionFiles) {
    $gitPath = $requiredFile.Replace("\", "/")
    if ($gitPath -notin $trackedFiles) {
        throw "Required third-party notice is not tracked: $requiredFile"
    }
}

foreach ($relativePath in $trackedFiles) {
    if ([System.IO.Path]::IsPathRooted($relativePath) -or
        $relativePath -match '(^|/)\.\.(/|$)') {
        throw "Unsafe tracked path rejected: $relativePath"
    }

    $sourcePath = Join-Path $SourceRoot $relativePath
    $destinationPath = Join-Path $packageRoot $relativePath
    $destinationParent = Split-Path -Parent $destinationPath
    New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
}

$requiredModels = @(
    "models\en_GB-northern_english_male-medium.onnx",
    "models\es_MX-claude-high.onnx"
)
foreach ($relativeModel in $requiredModels) {
    $modelPath = Join-Path $packageRoot $relativeModel
    if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf) -or
        (Get-Item -LiteralPath $modelPath).Length -lt 1000000) {
        throw "Release model is missing or still a Git LFS pointer: $relativeModel"
    }
}

@"
J.A.R.V.I.S. v$Version for Windows

1. Extract the complete archive.
2. Run Install-JARVIS.bat.
3. Add an API key to .env.
4. Launch Start-JARVIS.bat or the generated shortcut.

This alpha package still requires Python 3.11/3.12, FFmpeg, eSpeak NG, and
Microsoft Edge WebView2 Runtime. The installer validates these prerequisites.
Third-party voice and runtime terms are documented in THIRD_PARTY_NOTICES.md.
"@ | Set-Content -LiteralPath (Join-Path $packageRoot "RELEASE.txt") -Encoding utf8

if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
if (Test-Path -LiteralPath $checksumPath) {
    Remove-Item -LiteralPath $checksumPath -Force
}

Compress-Archive -Path $packageRoot -DestinationPath $archivePath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $([System.IO.Path]::GetFileName($archivePath))" |
    Set-Content -LiteralPath $checksumPath -Encoding ascii -NoNewline

Write-Host "Release archive: $archivePath"
Write-Host "SHA-256: $hash"
