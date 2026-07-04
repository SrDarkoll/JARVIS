# J.A.R.V.I.S. setup for Windows / PowerShell
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Full
)

$ErrorActionPreference = "Stop"

Write-Host "== J.A.R.V.I.S. setup =="

$repoTemp = Join-Path (Get-Location) "scratch\setup-temp"
New-Item -ItemType Directory -Force -Path $repoTemp | Out-Null
$env:TEMP = $repoTemp
$env:TMP = $repoTemp

$pythonCmd = $null
$pythonArgs = @()
$pythonCandidates = @(
    @{ Cmd = "py"; Args = @("-3.11") },
    @{ Cmd = "py"; Args = @("-3.12") },
    @{ Cmd = "python"; Args = @() },
    @{ Cmd = "py"; Args = @() }
)

foreach ($candidate in $pythonCandidates) {
    if (Get-Command $candidate.Cmd -ErrorAction SilentlyContinue) {
        try {
            & $candidate.Cmd @($candidate.Args) --version | Out-Null
            $candidatePath = (& $candidate.Cmd @($candidate.Args) -c "import sys; print(sys.executable)").Trim()
        } catch {
            continue
        }
        if ($candidatePath -like "*\WindowsApps\*") {
            continue
        }
        $pythonCmd = $candidate.Cmd
        $pythonArgs = @($candidate.Args)
        break
    }
}

if (-not $pythonCmd) {
    Write-Host "ERROR: Python 3.11/3.12 was not found." -ForegroundColor Red
    exit 1
}

$pythonVersion = (& $pythonCmd @pythonArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($pythonVersion -notin @("3.11", "3.12")) {
    Write-Host "ERROR: Python $pythonVersion is not supported. Use Python 3.11 or 3.12." -ForegroundColor Red
    exit 1
}
Write-Host "Using Python $pythonVersion via: $pythonCmd $($pythonArgs -join ' ')"

if (-not (Test-Path "venv")) {
    & $pythonCmd @pythonArgs -m venv venv
}

& ".\venv\Scripts\Activate.ps1"

python -m pip install --upgrade pip
pip install -r requirements.txt
if ($Full) {
    pip install -r requirements-optional.txt
}
if ($Dev) {
    pip install -r requirements-dev.txt
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "WARNING: ffmpeg is not on PATH. Voice/audio features need it." -ForegroundColor Yellow
    Write-Host "Install with: winget install Gyan.FFmpeg" -ForegroundColor Yellow
}

if (-not (Get-Command git-lfs -ErrorAction SilentlyContinue)) {
    Write-Host "WARNING: Git LFS was not found. Model files may be missing." -ForegroundColor Yellow
    Write-Host "Install with: winget install GitHub.GitLFS" -ForegroundColor Yellow
}

$requiredModels = @(
    "models\en_GB-northern_english_male-medium.onnx",
    "models\en_GB-northern_english_male-medium.onnx.json",
    "models\es_MX-claude-high.onnx",
    "models\es_MX-claude-high.onnx.json"
)

$missingModels = @()
foreach ($model in $requiredModels) {
    if (-not (Test-Path $model)) {
        $missingModels += $model
        continue
    }
    if ($model.EndsWith(".onnx") -and (Get-Item $model).Length -lt 1000000) {
        $missingModels += "$model (Git LFS pointer, not full model)"
    }
}

if ($missingModels.Count -gt 0) {
    Write-Host "ERROR: required model files are missing:" -ForegroundColor Red
    $missingModels | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host "Run: git lfs pull" -ForegroundColor Yellow
    exit 1
}

if ($Full) {
    python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Playwright Chromium could not be installed. Browser automation tools may be unavailable." -ForegroundColor Yellow
    }
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created. Add your API keys before real use." -ForegroundColor Green
}

Write-Host "Setup complete."
Write-Host "Run: python start_app.py"
if (-not $Dev) {
    Write-Host "For test tools run: .\setup.ps1 -Dev"
}
if (-not $Full) {
    Write-Host "For all optional integrations run: .\setup.ps1 -Full"
}
