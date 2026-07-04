#!/usr/bin/env bash
set -euo pipefail

echo "== J.A.R.V.I.S. setup =="
mkdir -p scratch/setup-temp
export TMPDIR="$(pwd)/scratch/setup-temp"

DEV_MODE=0
FULL_MODE=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV_MODE=1 ;;
    --full) FULL_MODE=1 ;;
    *)
      echo "ERROR: Unknown option '$arg'. Use --dev and/or --full."
      exit 1
      ;;
  esac
done

PYTHON_CMD=""
PYTHON_VERSION=""
for candidate in python3.11 python3.12 python3 python; do
  if ! command -v "$candidate" >/dev/null 2>&1; then
    continue
  fi
  candidate_version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [ "$candidate_version" = "3.11" ] || [ "$candidate_version" = "3.12" ]; then
    PYTHON_CMD="$candidate"
    PYTHON_VERSION="$candidate_version"
    break
  fi
done

if [ -z "$PYTHON_CMD" ]; then
  echo "ERROR: Python 3.11/3.12 was not found."
  exit 1
fi
echo "Using Python $PYTHON_VERSION via: $PYTHON_CMD"

if [ ! -d "venv" ]; then
  "$PYTHON_CMD" -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
if [ "$FULL_MODE" -eq 1 ]; then
  pip install -r requirements-optional.txt
fi
if [ "$DEV_MODE" -eq 1 ]; then
  pip install -r requirements-dev.txt
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "WARNING: ffmpeg is not on PATH. Voice/audio features need it."
  echo "Ubuntu/Debian: sudo apt install ffmpeg"
  echo "macOS: brew install ffmpeg"
fi

if ! command -v git-lfs >/dev/null 2>&1; then
  echo "WARNING: Git LFS was not found. Model files may be missing."
  echo "Install Git LFS, then run: git lfs pull"
fi

required_models=(
  "models/en_GB-northern_english_male-medium.onnx"
  "models/en_GB-northern_english_male-medium.onnx.json"
  "models/es_MX-claude-high.onnx"
  "models/es_MX-claude-high.onnx.json"
)

missing=()
for model in "${required_models[@]}"; do
  if [ ! -f "$model" ]; then
    missing+=("$model")
    continue
  fi
  if [[ "$model" == *.onnx ]] && [ "$(wc -c < "$model")" -lt 1000000 ]; then
    missing+=("$model (Git LFS pointer, not full model)")
  fi
done

if [ "${#missing[@]}" -gt 0 ]; then
  echo "ERROR: required model files are missing:"
  printf '  %s\n' "${missing[@]}"
  echo "Run: git lfs pull"
  exit 1
fi

if [ "$FULL_MODE" -eq 1 ] && ! python -m playwright install chromium; then
  echo "WARNING: Playwright Chromium could not be installed. Browser automation tools may be unavailable."
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ".env created. Add your API keys before real use."
fi

echo "Setup complete."
echo "Run: python start_app.py"
if [ "$DEV_MODE" -ne 1 ]; then
  echo "For test tools run: ./setup.sh --dev"
fi
if [ "$FULL_MODE" -ne 1 ]; then
  echo "For all optional integrations run: ./setup.sh --full"
fi
