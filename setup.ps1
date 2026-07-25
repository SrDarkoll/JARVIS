# J.A.R.V.I.S. setup for Windows / PowerShell
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Full,
    [switch]$CreateShortcut,
    [ValidateSet("3.11", "3.12")]
    [string]$PythonVersion = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
Set-Location -LiteralPath $repoRoot

Write-Host "== J.A.R.V.I.S. setup =="

function Require-Command {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$InstallCommand
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: '$Name' was not found." -ForegroundColor Red
        Write-Host "Install with: $InstallCommand" -ForegroundColor Yellow
        exit 1
    }
}

Require-Command -Name "ffmpeg" -InstallCommand "winget install Gyan.FFmpeg"

$requiredModels = @(
    "models\en_GB-northern_english_male-medium.onnx",
    "models\en_GB-northern_english_male-medium.onnx.json",
    "models\es_MX-claude-high.onnx",
    "models\es_MX-claude-high.onnx.json"
)

function Get-ModelIssues {
    $issues = @()
    foreach ($model in $requiredModels) {
        if (-not (Test-Path -LiteralPath $model -PathType Leaf)) {
            $issues += $model
            continue
        }
        if ($model.EndsWith(".onnx") -and
            (Get-Item -LiteralPath $model).Length -lt 1000000) {
            $issues += "$model (Git LFS pointer, not full model)"
        }
    }
    return $issues
}

$missingModels = @(Get-ModelIssues)
if ($missingModels.Count -gt 0) {
    Require-Command -Name "git-lfs" -InstallCommand "winget install GitHub.GitLFS"
    Write-Host "Required voice models are missing; downloading Git LFS files..."
    & git lfs pull
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Git LFS could not download the voice models." -ForegroundColor Red
        exit 1
    }
    $missingModels = @(Get-ModelIssues)
}

if ($missingModels.Count -gt 0) {
    Write-Host "ERROR: required model files are missing:" -ForegroundColor Red
    $missingModels | ForEach-Object {
        Write-Host "  $_" -ForegroundColor Red
    }
    Write-Host "Run 'git lfs pull' and retry setup." -ForegroundColor Yellow
    exit 1
}

$webViewRoots = @(
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
)
$webViewAvailable = $false
foreach ($root in $webViewRoots) {
    if (-not (Test-Path -LiteralPath $root)) {
        continue
    }
    $webViewAvailable = [bool](
        Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue |
            Get-ItemProperty -ErrorAction SilentlyContinue |
            Where-Object {
                $_.name -like "*WebView2*" -or
                $_.DisplayName -like "*WebView2*"
            } |
            Select-Object -First 1
    )
    if ($webViewAvailable) {
        break
    }
}
if (-not $webViewAvailable) {
    Write-Host "WARNING: WebView2 Runtime was not detected; the desktop shell may be degraded." -ForegroundColor Yellow
}

if (-not (Get-Command "espeak-ng" -ErrorAction SilentlyContinue) -and
    -not (Get-Command "espeak" -ErrorAction SilentlyContinue)) {
    Write-Host "WARNING: eSpeak was not detected; voices that require it will be unavailable." -ForegroundColor Yellow
}

$repoTemp = Join-Path $repoRoot "scratch\setup-temp"
New-Item -ItemType Directory -Force -Path $repoTemp | Out-Null
$env:TEMP = $repoTemp
$env:TMP = $repoTemp

$pythonCmd = $null
$pythonArgs = @()
$versions = if ($PythonVersion) {
    @($PythonVersion)
} else {
    @("3.11", "3.12")
}
$pythonCandidates = @(
    foreach ($version in $versions) {
        @{ Cmd = "py"; Args = @("-$version") }
    }
)
$pythonCandidates += @(
    @{ Cmd = "python"; Args = @() },
    @{ Cmd = "py"; Args = @() }
)

foreach ($candidate in $pythonCandidates) {
    if (Get-Command $candidate.Cmd -ErrorAction SilentlyContinue) {
        try {
            & $candidate.Cmd @($candidate.Args) --version | Out-Null
            if ($LASTEXITCODE -ne 0) {
                continue
            }
            $candidateOutput = & $candidate.Cmd @($candidate.Args) -c "import sys; print(sys.executable)"
            if ($LASTEXITCODE -ne 0) {
                continue
            }
            $candidatePath = ($candidateOutput | Out-String).Trim()
        } catch {
            continue
        }
        if (-not $candidatePath -or -not (Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
            continue
        }
        $pythonCmd = $candidate.Cmd
        $pythonArgs = @($candidate.Args)
        break
    }
}

if (-not $pythonCmd) {
    Write-Host "ERROR: Python 3.11 or 3.12 was not found." -ForegroundColor Red
    Write-Host "Install with: winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}

$pythonVersionOutput = & $pythonCmd @pythonArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to inspect the selected Python interpreter." -ForegroundColor Red
    exit 1
}
$pythonVersion = ($pythonVersionOutput | Out-String).Trim()
if ($pythonVersion -notin @("3.11", "3.12")) {
    Write-Host "ERROR: Python $pythonVersion is not supported. Use Python 3.11 or 3.12." -ForegroundColor Red
    exit 1
}
Write-Host "Using Python $pythonVersion via: $pythonCmd $($pythonArgs -join ' ')"

if (-not (Test-Path "venv")) {
    & $pythonCmd @pythonArgs -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create the project virtual environment." -ForegroundColor Red
        exit 1
    }
}

$venvPython = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython -PathType Leaf)) {
    Write-Host "ERROR: The project virtual environment is incomplete. Remove 'venv' and rerun setup." -ForegroundColor Red
    exit 1
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to upgrade pip in the project virtual environment." -ForegroundColor Red
    exit 1
}

& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install core dependencies." -ForegroundColor Red
    exit 1
}

if ($Full) {
    & $venvPython -m pip install -r requirements-optional.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to install optional dependencies." -ForegroundColor Red
        exit 1
    }
}

if ($Dev) {
    & $venvPython -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to install development dependencies." -ForegroundColor Red
        exit 1
    }
}

if ($Full) {
    & $venvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Playwright Chromium could not be installed. Browser automation tools may be unavailable." -ForegroundColor Yellow
    }
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created. Add your API keys before real use." -ForegroundColor Green
}

if ($CreateShortcut) {
    $shortcutTargets = @(
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "JARVIS.lnk"),
        (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\JARVIS.lnk")
    )
    $shortcutShell = New-Object -ComObject WScript.Shell
    foreach ($shortcutPath in $shortcutTargets) {
        $shortcut = $shortcutShell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $repoRoot "Start-JARVIS.bat"
        $shortcut.WorkingDirectory = $repoRoot
        $shortcut.Description = "Start J.A.R.V.I.S."
        $shortcut.Save()
    }
    Write-Host "Desktop and Start Menu shortcuts created." -ForegroundColor Green
}

Write-Host "Setup complete."
Write-Host "Run: .\Start-JARVIS.bat"
if (-not $Dev) {
    Write-Host "For test tools run: .\setup.ps1 -Dev"
}
if (-not $Full) {
    Write-Host "For all optional integrations run: .\setup.ps1 -Full"
}
