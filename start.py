#!/usr/bin/env python3
"""Local launcher for AI Video Transcriber."""

import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.resolve()


def _is_windows() -> bool:
    return os.name == "nt"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        print(f"[WARN] {name} is invalid; using {default}")
        return default
    if value < minimum or value > maximum:
        print(f"[WARN] {name} must be between {minimum} and {maximum}; using {default}")
        return default
    return value


def _print_install_hint() -> None:
    print("\nInstall dependencies with PowerShell:")
    print("  .\\install.ps1")
    print("\nOr manually:")
    if _is_windows():
        print("  py -3.11 -m venv .venv")
        print("  .\\.venv\\Scripts\\Activate.ps1")
        print("  python -m pip install --upgrade pip")
        print("  python -m pip install -r requirements.txt")
    else:
        print("  python3 -m venv .venv")
        print("  source .venv/bin/activate")
        print("  python -m pip install -r requirements.txt")


def check_dependencies() -> bool:
    """Check modules without importing large ML runtimes into the launcher."""
    required_packages = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "python-multipart": "multipart",
        "yt-dlp": "yt_dlp",
        "curl-cffi": "curl_cffi",
        "faster-whisper": "faster_whisper",
        "openai": "openai",
        "aiofiles": "aiofiles",
        "pydantic": "pydantic",
        "VieNeu-TTS": "vieneu",
        "torch": "torch",
        "torchaudio": "torchaudio",
        "RapidOCR": "rapidocr_onnxruntime",
    }
    missing = [name for name, module in required_packages.items() if importlib.util.find_spec(module) is None]
    if missing:
        print("[ERROR] Missing Python packages:")
        for package in missing:
            print(f"  - {package}")
        _print_install_hint()
        return False
    print("[OK] Python dependencies are installed")
    return True


def _command_version(command: str) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    result = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    return (result.stdout or result.stderr or command).splitlines()[0]


def check_media_tools() -> bool:
    """Require FFmpeg and FFprobe because core media flows depend on both."""
    ok = True
    for command in ("ffmpeg", "ffprobe"):
        version = _command_version(command)
        if version:
            print(f"[OK] {version}")
            continue
        ok = False
        print(f"[ERROR] {command} was not found on PATH")
    if not ok:
        if _is_windows():
            print("Install FFmpeg with: winget install --id Gyan.FFmpeg --exact")
        else:
            print("Install FFmpeg with your package manager")
    return ok


def _find_tesseract() -> str | None:
    configured = os.getenv("TESSERACT_CMD", "").strip()
    candidates = [
        configured,
        str(PROJECT_ROOT / ".runtime" / "tesseract" / "tesseract.exe"),
        shutil.which("tesseract"),
    ]
    if _is_windows():
        candidates.extend([
            str(Path(os.getenv("ProgramFiles", r"C:\\Program Files")) / "Tesseract-OCR" / "tesseract.exe"),
            str(Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
        ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return None


def check_ocr_tools() -> None:
    executable = _find_tesseract()
    if not executable:
        print("[WARN] Tesseract is missing; RapidOCR remains available as a fallback")
        print("       Install it with: winget install --id UB-Mannheim.TesseractOCR --exact")
        return

    cmd = [executable]
    bundled_tessdata = PROJECT_ROOT / ".runtime" / "tessdata"
    if bundled_tessdata.is_dir():
        cmd.extend(["--tessdata-dir", str(bundled_tessdata)])
    cmd.append("--list-langs")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    languages = {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}
    missing = sorted({"eng", "vie", "chi_sim"} - languages)
    if result.returncode == 0 and not missing:
        print(f"[OK] Tesseract OCR: {executable} (eng, vie, chi_sim)")
    else:
        print(f"[WARN] Tesseract found at {executable}, but language packs are incomplete: {', '.join(missing)}")


def setup_environment() -> None:
    """Set stable local defaults without overriding user configuration."""
    if os.getenv("OPENAI_API_KEY"):
        print("[OK] OPENAI_API_KEY is set")
    else:
        print("[WARN] OPENAI_API_KEY is not set; transcription and local exports still work")

    defaults = {
        "WHISPER_MODEL_SIZE": "base",
        "WHISPER_DEVICE": "cpu",
        "WHISPER_COMPUTE_TYPE": "int8",
    }
    for name, value in defaults.items():
        if not os.getenv(name):
            os.environ[name] = value
        print(f"[OK] {name}={os.environ[name]}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start AI Video Transcriber")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reload", action="store_true", help="Enable Uvicorn hot reload for development")
    mode.add_argument("--prod", action="store_true", help="Run without hot reload")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    production_mode = args.prod or _env_bool("PRODUCTION_MODE")
    reload_enabled = not production_mode and (args.reload or _env_bool("DEV_RELOAD"))

    print("AI Video Transcriber startup check")
    print(f"Python: {platform.python_version()} ({platform.platform()})")
    print(f"Executable: {sys.executable}")
    print("Mode: development reload" if reload_enabled else "Mode: stable")
    print("=" * 50)

    if not check_dependencies() or not check_media_tools():
        sys.exit(1)
    check_ocr_tools()
    setup_environment()

    host = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _env_int("PORT", 8000, 1, 65535)

    print("\nStarting server...")
    print(f"  URL: http://{host if host not in {'0.0.0.0', '::'} else 'localhost'}:{port}")
    print("  Press Ctrl+C to stop")
    print("=" * 50)

    backend_dir = PROJECT_ROOT / "backend"
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--app-dir",
        str(backend_dir),
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload_enabled:
        cmd.extend(["--reload", "--reload-dir", str(backend_dir)])

    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)
        if result.returncode:
            sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\nServer stopped")
    except Exception as exc:
        print(f"\n[ERROR] Startup failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
