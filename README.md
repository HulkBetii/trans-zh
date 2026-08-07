# AI Video Transcriber

Self-hosted web app that turns a video into a transcript, a translation, subtitles,
and optionally a dubbed audio track. It takes a video URL (anything yt-dlp supports)
or a local upload, transcribes it with Faster-Whisper, and gives you an editor for
the timed cues plus export to SRT/VTT, burned-in subtitles, or a dubbed MP4.

Runs entirely on your own machine. The only outbound calls are to the video platform,
and to whichever LLM/TTS provider you configure.

---

## What it does

- **Transcribe** — pulls existing platform subtitles when available (fast path), otherwise
  downloads the audio and runs Faster-Whisper.
- **Hard-subtitle OCR** — for videos with burned-in subtitles and no audio track worth
  transcribing, extracts frames and OCRs them (RapidOCR, with Tesseract as a fallback).
- **Translate** — chunked translation through any OpenAI-compatible API, with a name
  glossary and a speaker/pronoun profile to keep terminology and address forms stable.
- **Edit** — a cue-by-cue editor with live subtitle preview against the media.
- **Export** — SRT/VTT, or an MP4 with burned-in subtitles (single or dual-language).
- **Dub** — generates a translated voice track (VieNeu-TTS, ElevenLabs, or FPT.AI),
  time-fits each clip to its cue, and muxes it in as a replacement or voiceover.

## Requirements

| | Version | Notes |
|---|---|---|
| Python | **3.11** | Matches the Dockerfile; the installers enforce it |
| FFmpeg | any recent | **Both `ffmpeg` and `ffprobe`** must be on `PATH` |
| Tesseract | optional | Only for hard-subtitle OCR; RapidOCR is the built-in fallback |

An OpenAI-compatible API key is optional. Without one, transcription, subtitle export,
and video export still work — only summarization and translation are disabled.

## Install

### Docker (simplest)

```bash
docker compose up --build
```

Then open <http://localhost:8000>. Compose binds to `127.0.0.1` only — see
[Exposing it beyond localhost](#exposing-it-beyond-localhost) before changing that.

### Windows

```powershell
.\install.ps1
```

Creates `.venv`, installs dependencies, and pulls FFmpeg (winget) and Tesseract plus the
`eng`/`vie`/`chi_sim` language data. All downloaded binaries are SHA256-verified.

### Linux / macOS

```bash
./install.sh
```

Same end state as the Windows installer. Options: `--skip-venv`, `--venv-path <dir>`.

> On Debian/Ubuntu you may need `sudo apt-get install -y python3.11 python3.11-venv` first.

## Run

```bash
python start.py
```

Open <http://localhost:8000>. `start.py` verifies dependencies before starting and exits
with a clear message if FFmpeg or FFprobe is missing.

```bash
python start.py --reload   # dev, hot reload
python start.py --prod     # explicitly disable reload
```

---

## Configuration

Everything is environment variables. All are optional — the defaults give you a working
localhost install.

### Core

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` | Bind address. See the security note before changing. |
| `PORT` | `8000` | Listen port |
| `APP_TEMP_DIR` | `./temp` | Where media, transcripts, and `tasks.json` live |
| `WORKER_THREADS` | `8` | Thread pool for blocking work (ffmpeg, Whisper, LLM calls) |
| `UPLOAD_MAX_MB` | `200` | Upload size cap |
| `CORS_ORIGINS` | *(none)* | Comma-separated allowlist; CORS is off when unset |
| `PRODUCTION_MODE` | `false` | Same as `--prod`: never enable hot reload |
| `DEV_RELOAD` | `false` | Same as `--reload`; ignored when `PRODUCTION_MODE` is set |

Accepted uploads: `.txt .mp3 .mp4 .m4a .wav .webm .mkv .ogg .flac`

### Security

| Variable | Default | Purpose |
|---|---|---|
| `APP_AUTH_TOKEN` | *(unset)* | When set, `/api/*` requires this token. `/api/health` stays open for the Docker healthcheck. |

Send it as `X-API-Token: <token>` or `Authorization: Bearer <token>`. Routes the browser
loads directly (SSE, `<audio src>`, download links) also accept `?token=<token>`, since
those cannot set headers. In the UI, paste it into **Settings → Access token**.

### LLM (summary + translation)

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | *(unset)* | Without it, summary/translation are skipped |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `OPENAI_MODEL` | — | Sets both summary and translation models |
| `OPENAI_SUMMARY_MODEL` | `gpt-3.5-turbo` / `gpt-4o` | Overrides `OPENAI_MODEL` for summaries |
| `OPENAI_TRANSLATION_MODEL` | `gpt-4o` | Overrides `OPENAI_MODEL` for translation |
| `OPENAI_SUMMARY_TIMEOUT` | `120` | Seconds |
| `OPENAI_TRANSLATION_TIMEOUT` | `120` | Seconds |

Local runtimes work: point `OPENAI_BASE_URL` at `http://127.0.0.1:11434/v1` for Ollama.
Loopback is explicitly allowed; other private/LAN addresses are rejected (see
[SSRF protection](#ssrf-protection)).

### Whisper

| Variable | Default | Purpose |
|---|---|---|
| `WHISPER_MODEL_SIZE` | `base` | `tiny` … `large` |
| `WHISPER_DEVICE` | `cpu` | `cpu`, `cuda`, or `auto` |
| `WHISPER_COMPUTE_TYPE` | `int8` (cpu) / `float16` (cuda) | |

CUDA is opt-in and falls back to CPU automatically if initialization fails.

### OCR

| Variable | Default | Purpose |
|---|---|---|
| `TESSERACT_CMD` | auto-detected | Path to the binary |
| `TESSDATA_DIR` | `./.runtime/tessdata` | Language data directory |

### TTS / dubbing

| Variable | Default | Purpose |
|---|---|---|
| `VIENEU_MODE` / `VIENEU_BACKEND` / `VIENEU_PRECISION` | `v3turbo` / `onnx` / `int8` | VieNeu-TTS runtime |
| `VIENEU_VOICE` / `VIENEU_STYLE` | — / `tu_nhien` | Default voice and style |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` / `ELEVENLABS_MODEL_ID` | — / — / `eleven_multilingual_v2` | |
| `FPT_API_KEY` / `FPT_VOICE` / `FPT_SPEED` | — / `banmai` / `0` | |
| `TTS_PREVIEW_TTL_HOURS` | `24` | Age at which cached voice previews are pruned |

### Platform cookies

Some platforms need cookies for restricted videos. Set them here or paste them into
**Settings → Platform Cookies** in the UI.

| Variable | Purpose |
|---|---|
| `DOUYIN_COOKIE` / `DOUYIN_COOKIE_FILE` | Douyin |
| `BILIBILI_COOKIE` / `BILIBILI_COOKIE_FILE` | Bilibili |
| `DOUYIN_TIKTOK_API_BASE` | Optional third-party download API fallback (alias: `DOUYIN_TIKTOK_DOWNLOAD_API_BASE`) |

### Subtitles and tuning

| Variable | Default | Purpose |
|---|---|---|
| `SUBTITLE_FONT_FAMILY` / `SUBTITLE_FONTS_DIR` | auto-detected | Burned-in subtitle font |
| `TASKS_SAVE_INTERVAL` | `2.0` | Seconds to coalesce task-store writes. `0` writes on every update. |
| `SSE_QUEUE_MAXSIZE` | `64` | Backlog per live-progress client before it is dropped |

---

## Security

This app has **no authentication by default**. That is fine for a single user on
localhost, which is what `HOST=127.0.0.1` and the compose port binding assume.

### Exposing it beyond localhost

Anyone who can reach the port can read every transcript, download your media, delete
tasks, and spend your API credit. Before exposing it:

1. Set `APP_AUTH_TOKEN` to a strong random value:
   ```bash
   export APP_AUTH_TOKEN=$(openssl rand -hex 32)
   ```
2. Put it behind TLS. The token travels in a header — or a query string for media and SSE —
   so plain HTTP leaks it.
3. Keep `CORS_ORIGINS` as tight as possible; leave it unset if the UI is same-origin.

### SSRF protection

`/api/models` proxies to a user-supplied Base URL, so the host is resolved and checked
first. Loopback is allowed (local LLM runtimes); private, link-local, reserved, and
multicast addresses are rejected. This blocks cloud metadata endpoints and LAN scanning.
It is best-effort and does not defend against DNS rebinding.

### Credential handling

API keys and cookies are **never written to disk**. They are stripped from the task store
on every save, and the frontend keeps them in `sessionStorage`, not `localStorage`. This
means a resumed or regenerated task needs the key supplied again by the caller — the
server has no copy.

`temp/` holds transcripts, media, and task state. It is gitignored; treat it as private.

---

## Tests

```bash
python -m unittest discover -s tests
```

Covers the API surface, credential redaction, subtitle timestamp handling, SSRF
allow/block, dub alignment, task-store persistence, and the auth middleware. The OCR test
skips itself when Tesseract is not installed.

## Project layout

```
backend/
  main.py            FastAPI app: routes, task store, export/dub pipelines
  transcriber.py     Faster-Whisper wrapper
  video_processor.py yt-dlp download, subtitle fetch, media normalization
  summarizer.py      Transcript cleanup and summarization
  translator.py      Translation, name glossary, speaker profile
  tts_engine.py      VieNeu-TTS / ElevenLabs / FPT.AI clients
static/              Single-page frontend (no build step)
tests/               unittest suite
```

## Troubleshooting

**`ffprobe was not found on PATH`** — install the full FFmpeg package; some builds ship
`ffmpeg` alone. The backend needs both.

**Summary/translation silently skipped** — no `OPENAI_API_KEY`. Everything else still works.

**`externally-managed-environment` from pip** — you are on a distro enforcing PEP 668. Use
`./install.sh`, which creates a venv, rather than installing into system Python.

**OCR finds nothing** — adjust crop, language, and confidence in the OCR settings. Crop
defaults assume subtitles near the bottom of the frame.

**Dub export says the translation is incomplete** — some transcript cues have no
translated text. Regenerate the translation before exporting; the check is deliberate, so
you do not get a dub that silently drops lines.
