# Build a sanitized Windows release archive from version-controlled runtime files.
[CmdletBinding()]
param(
    [string]$Version = "0.1.0-alpha.4",
    [string]$SourceRoot = "",
    [string]$OutputDirectory = "",
    [string]$BuildDirectory = ""
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

if ($Version -notmatch '^[0-9][A-Za-z0-9.+-]*$') {
    throw "Release version must be a filename-safe version identifier."
}

$packageName = "JARVIS-v$Version-windows"
if (-not $BuildDirectory) {
    $BuildDirectory = Join-Path $SourceRoot "scratch\release-build"
}
$buildRoot = [System.IO.Path]::GetFullPath($BuildDirectory).TrimEnd("\", "/")
$packageRoot = Join-Path $buildRoot $packageName
$archivePath = Join-Path $OutputDirectory "$packageName.zip"
$checksumPath = "$archivePath.sha256"

$expectedBuildPrefix = [System.IO.Path]::GetFullPath(
    (Join-Path $SourceRoot "scratch")
).TrimEnd("\", "/") +
    [System.IO.Path]::DirectorySeparatorChar
if (-not $buildRoot.StartsWith(
    $expectedBuildPrefix,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Release build directory must stay inside the source root's scratch directory."
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
$requiredRuntimeFiles = @(
    "src/backend/api/device_sync_routes.py",
    "src/backend/api/sync_routes.py",
    "src/backend/core/device_sync/__init__.py",
    "src/backend/core/device_sync/auth.py",
    "src/backend/core/device_sync/sync_hub.py",
    "src/backend/core/device_sync/sync_models.py",
    "src/backend/core/security/browser_profile.py",
    "src/backend/services/reminder_service.py",
    "src/backend/services/notification_policy.py",
    "src/frontend/static/css/mobile.css",
    "src/frontend/static/js/mobile.js",
    "src/frontend/static/js/modules/device-sync.js",
    "src/frontend/static/js/modules/mobile-pairing.js",
    "src/frontend/static/js/modules/reminders.js",
    "src/frontend/static/mobile-manifest.json",
    "src/frontend/templates/mobile.html"
)
$requiredDistributionFiles = @(
    "third_party\licenses\Apache-2.0.txt",
    "third_party\licenses\CC-BY-SA-4.0.txt",
    "third_party\licenses\GPL-3.0.txt",
    "third_party\model_cards\en_GB-northern_english_male-medium.md",
    "third_party\model_cards\es_MX-claude-high.md",
    "third_party\PIPER_VOICES_REPOSITORY_NOTICE.md"
)

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
foreach ($requiredFile in $requiredRuntimeFiles) {
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

if (Test-Path -LiteralPath $buildRoot) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

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
