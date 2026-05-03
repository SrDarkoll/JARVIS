# J.A.R.V.I.S. setup for Windows / PowerShell
$ErrorActionPreference = "Stop"

Write-Host "== J.A.R.V.I.S. setup =="

$pythonCmd = $null
foreach ($cmd in @("python", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $pythonCmd = $cmd
        break
    }
}

if (-not $pythonCmd) {
    Write-Host "ERROR: Python 3.11/3.12 was not found." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path "venv")) {
    & $pythonCmd -m venv venv
}

& ".\venv\Scripts\Activate.ps1"

python -m pip install --upgrade pip
pip install -r requirements.txt

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

python -m playwright install chromium

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created. Add your API keys before real use." -ForegroundColor Green
}

Write-Host "Setup complete."
Write-Host "Run: python start_app.py"
