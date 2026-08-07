#!/usr/bin/env bash
#
# AI Video Transcriber installer for Linux and macOS.
# Mirrors install.ps1: Python 3.11 venv, FFmpeg + FFprobe, Tesseract with the
# eng/vie/chi_sim language data, and a final import/compile check.
#
# Usage:
#   ./install.sh                       # create .venv and install everything
#   ./install.sh --venv-path /opt/env  # use a different venv location
#   ./install.sh --skip-venv           # install into the active interpreter

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

VENV_PATH=".venv"
SKIP_VENV=0

while [ $# -gt 0 ]; do
    case "$1" in
        --venv-path) VENV_PATH="${2:-}"; shift 2 ;;
        --skip-venv) SKIP_VENV=1; shift ;;
        -h|--help)   sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

step() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

# SHA256 pins for the Tesseract language data. The URL below is pinned to the
# immutable 4.1.0 tag (not the moving main branch) so these stay valid.
expected_hash() {
    case "$1" in
        eng)     echo "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2" ;;
        vie)     echo "79df64caf7bcfb2a27df5042ecb6121e196eada34da774956995747636d5bfa1" ;;
        chi_sim) echo "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730" ;;
        *)       echo "" ;;
    esac
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        fail "Neither sha256sum nor shasum is available; cannot verify downloads."
    fi
}

assert_hash() {
    local path="$1" key="$2" expected actual
    expected="$(expected_hash "$key")"
    [ -n "$expected" ] || fail "No pinned hash is configured for '$key'."
    actual="$(sha256_of "$path")"
    if [ "$actual" != "$expected" ]; then
        rm -f "$path"
        fail "Checksum mismatch for '$key'.
  expected: $expected
  actual:   $actual
The download was deleted. Do not use it."
    fi
    echo "  verified SHA256 $key: $actual"
}

download() {
    local url="$1" out="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$out"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$out" "$url"
    else
        fail "Neither curl nor wget is available; cannot download $url"
    fi
}

# ---------------------------------------------------------------- privileges
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    fi
fi

run_pkg_install() {
    # Announce before escalating so an interactive password prompt is expected.
    echo "  running: ${SUDO:+$SUDO }$*"
    if [ -n "$SUDO" ]; then
        $SUDO "$@"
    else
        "$@"
    fi
}

# ------------------------------------------------------------------- Python
step "Locating Python 3.11"
PYTHON=""
for candidate in python3.11 python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
    if [ "$version" = "3.11" ]; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || fail "Python 3.11 was not found.
This project targets Python 3.11 (see Dockerfile: python:3.11-slim-bookworm).
Install it, then rerun:
  Debian/Ubuntu : sudo apt-get install -y python3.11 python3.11-venv
  Fedora/RHEL   : sudo dnf install -y python3.11
  Arch          : sudo pacman -S python311
  macOS         : brew install python@3.11"
echo "  using $($PYTHON --version) ($(command -v "$PYTHON"))"

# --------------------------------------------------------------------- venv
if [ "$SKIP_VENV" -eq 0 ]; then
    step "Creating virtual environment"
    if [ ! -d "$VENV_PATH" ]; then
        "$PYTHON" -m venv "$VENV_PATH" \
            || fail "Failed to create the venv. On Debian/Ubuntu install python3.11-venv first."
    fi
    VENV_PY="$VENV_PATH/bin/python"
    [ -x "$VENV_PY" ] || fail "Virtual environment Python was not found at $VENV_PY"
    echo "  venv: $VENV_PATH"
else
    VENV_PY="$PYTHON"
    echo "  --skip-venv: installing into $(command -v "$PYTHON")"
fi

# ------------------------------------------------------------- dependencies
step "Installing Python dependencies"
"$VENV_PY" -m pip install --upgrade pip setuptools wheel
"$VENV_PY" -m pip install -r requirements.txt
"$VENV_PY" -m pip check

# ------------------------------------------------------------------- FFmpeg
step "Checking FFmpeg and FFprobe"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "  FFmpeg/FFprobe missing, attempting to install..."
    if   command -v apt-get >/dev/null 2>&1; then run_pkg_install apt-get update -qq && run_pkg_install apt-get install -y ffmpeg
    elif command -v dnf     >/dev/null 2>&1; then run_pkg_install dnf install -y ffmpeg
    elif command -v yum     >/dev/null 2>&1; then run_pkg_install yum install -y ffmpeg
    elif command -v pacman  >/dev/null 2>&1; then run_pkg_install pacman -S --noconfirm ffmpeg
    elif command -v zypper  >/dev/null 2>&1; then run_pkg_install zypper install -y ffmpeg
    elif command -v brew    >/dev/null 2>&1; then brew install ffmpeg
    else fail "Could not detect a package manager. Install FFmpeg manually, then rerun."
    fi
fi
# The backend shells out to both binaries, so both must exist.
command -v ffmpeg  >/dev/null 2>&1 || fail "ffmpeg is still not on PATH"
command -v ffprobe >/dev/null 2>&1 || fail "ffprobe is still not on PATH (the backend needs it for probing)"
echo "  $(ffmpeg  -version | head -1)"
echo "  $(ffprobe -version | head -1)"

# ---------------------------------------------------------------- Tesseract
step "Checking Tesseract OCR"
if ! command -v tesseract >/dev/null 2>&1; then
    echo "  Tesseract missing, attempting to install..."
    if   command -v apt-get >/dev/null 2>&1; then
        run_pkg_install apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie tesseract-ocr-chi-sim
    elif command -v dnf     >/dev/null 2>&1; then run_pkg_install dnf install -y tesseract tesseract-langpack-eng tesseract-langpack-vie tesseract-langpack-chi_sim
    elif command -v yum     >/dev/null 2>&1; then run_pkg_install yum install -y tesseract
    elif command -v pacman  >/dev/null 2>&1; then run_pkg_install pacman -S --noconfirm tesseract tesseract-data-eng tesseract-data-vie tesseract-data-chi_sim
    elif command -v zypper  >/dev/null 2>&1; then run_pkg_install zypper install -y tesseract-ocr
    elif command -v brew    >/dev/null 2>&1; then brew install tesseract tesseract-lang
    else
        echo "  WARN: no package manager detected; RapidOCR remains available as a fallback"
    fi
fi

if command -v tesseract >/dev/null 2>&1; then
    echo "  tesseract: $(command -v tesseract) ($(tesseract --version 2>&1 | head -1))"

    step "Preparing Tesseract language data"
    TESSDATA_DIR="$PROJECT_ROOT/.runtime/tessdata"
    mkdir -p "$TESSDATA_DIR"
    for language in eng vie chi_sim; do
        target="$TESSDATA_DIR/$language.traineddata"
        if [ ! -f "$target" ] || [ "$(wc -c < "$target")" -lt 100000 ]; then
            # Pinned to the 4.1.0 tag rather than main so the bytes (and hash) are stable.
            download "https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/$language.traineddata" "$target" \
                || fail "Failed to download Tesseract language: $language"
        fi
        assert_hash "$target" "$language"
    done

    listed="$(tesseract --tessdata-dir "$TESSDATA_DIR" --list-langs 2>&1 || true)"
    missing=""
    for language in eng vie chi_sim; do
        echo "$listed" | grep -qx "$language" || missing="$missing $language"
    done
    if [ -n "$missing" ]; then
        fail "Tesseract cannot see these languages in $TESSDATA_DIR:$missing"
    fi
    echo "  languages verified: eng, vie, chi_sim"
    echo "  export TESSDATA_DIR=\"$TESSDATA_DIR\"   # already the default lookup path"
else
    echo "  WARN: Tesseract is not installed; hard-subtitle OCR will fall back to RapidOCR"
fi

# ------------------------------------------------------------------- verify
step "Checking application imports"
"$VENV_PY" -c "
import importlib
modules = ['fastapi','uvicorn','multipart','yt_dlp','faster_whisper','openai',
           'pydantic','aiofiles','curl_cffi','vieneu','torch','torchaudio',
           'rapidocr_onnxruntime']
for name in modules:
    importlib.import_module(name)
print('All required Python imports are available')
"
"$VENV_PY" -m py_compile start.py backend/main.py backend/transcriber.py \
    backend/video_processor.py backend/tts_engine.py backend/summarizer.py \
    backend/translator.py

mkdir -p temp
chmod +x start.py 2>/dev/null || true

# --------------------------------------------------------------------- done
printf '\nInstall complete. Start the app with:\n'
[ "$SKIP_VENV" -eq 0 ] && printf '  source %s/bin/activate\n' "$VENV_PATH"
printf '  python start.py\n'
printf 'Development reload: python start.py --reload\n\n'
printf 'Optional configuration:\n'
printf '  export OPENAI_API_KEY=...     # enables summary and translation\n'
printf '  export APP_AUTH_TOKEN=...     # require a token on /api/* (recommended if not localhost-only)\n'
