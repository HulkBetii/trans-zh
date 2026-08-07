from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import importlib.util
import logging
import platform
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
import aiofiles
import uuid
import json
import re
import hashlib
import hmac
import ipaddress
import openai
import mimetypes
import socket
import subprocess
import time
import shutil
import difflib
from concurrent.futures import ThreadPoolExecutor
from urllib import error as urlerror, request as urlrequest
from urllib.parse import urlparse

from video_processor import VideoProcessor
from transcriber import Transcriber
from summarizer import Summarizer
from translator import Translator
from tts_engine import ElevenLabsTTS, FptTTS, VieNeuTTS

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s; using %s", name, default)
        return default
    if value < minimum or value > maximum:
        logger.warning("%s must be between %s and %s; using %s", name, minimum, maximum, default)
        return default
    return value


def _resolve_optional_path(raw: str, base: Path = PROJECT_ROOT) -> Optional[Path]:
    value = (raw or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _discover_subtitle_font() -> tuple[str, Optional[Path]]:
    configured_family = (os.getenv("SUBTITLE_FONT_FAMILY") or "").strip()
    configured_dir = _resolve_optional_path(os.getenv("SUBTITLE_FONTS_DIR", ""))
    if configured_family:
        return configured_family, configured_dir if configured_dir and configured_dir.is_dir() else None

    bundled_dir = PROJECT_ROOT / "static" / "fonts"
    if bundled_dir.is_dir() and any(bundled_dir.glob("*Noto*.*")):
        return "Noto Sans", bundled_dir

    if os.name == "nt":
        fonts_dir = Path(os.getenv("WINDIR", r"C:\Windows")) / "Fonts"
        candidates = (
            ("Noto Sans", ("NotoSans-Regular.ttf", "NotoSansCJK-Regular.ttc")),
            ("Segoe UI", ("segoeui.ttf",)),
            ("Arial", ("arial.ttf",)),
        )
        for family, filenames in candidates:
            if any((fonts_dir / filename).exists() for filename in filenames):
                return family, fonts_dir

    linux_candidates = (
        Path("/usr/share/fonts/truetype/noto"),
        Path("/usr/share/fonts/opentype/noto"),
        Path("/usr/share/fonts/truetype/dejavu"),
    )
    for fonts_dir in linux_candidates:
        if fonts_dir.is_dir():
            family = "Noto Sans" if "noto" in str(fonts_dir).lower() else "DejaVu Sans"
            return family, fonts_dir
    return "Arial", None


def _discover_tesseract() -> Optional[Path]:
    configured = _resolve_optional_path(os.getenv("TESSERACT_CMD", ""))
    candidates = [configured, PROJECT_ROOT / ".runtime" / "tesseract" / "tesseract.exe"]
    detected = shutil.which("tesseract")
    if detected:
        candidates.append(Path(detected))
    if os.name == "nt":
        candidates.extend([
            Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        ])
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    return None


app = FastAPI(title="AI视频转录器", version="1.1.0")
WORKER_THREADS = _env_int("WORKER_THREADS", 8, 1, 32)
SUBTITLE_FONT_FAMILY, SUBTITLE_FONTS_DIR = _discover_subtitle_font()
TESSERACT_CMD = _discover_tesseract()
TESSDATA_DIR = _resolve_optional_path(os.getenv("TESSDATA_DIR", ""))
if not TESSDATA_DIR:
    bundled_tessdata = PROJECT_ROOT / ".runtime" / "tessdata"
    TESSDATA_DIR = bundled_tessdata if bundled_tessdata.is_dir() else None

cors_origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "").split(",") if item.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# 可选的访问令牌：未设置时行为与以往完全一致（纯本机使用无需配置）
APP_AUTH_TOKEN = (os.getenv("APP_AUTH_TOKEN") or "").strip()
# /api/health 保持开放，供 Docker HEALTHCHECK 使用
_AUTH_EXEMPT_PATHS = frozenset({"/api/health"})
# 这些只读 GET 由浏览器自身加载（EventSource / <audio src> / 下载链接），
# 无法附加请求头，因此允许用 ?token= 传令牌。
_AUTH_QUERY_TOKEN_PREFIXES = (
    "/api/task-stream/",
    "/api/media/",
    "/api/download/",
    "/api/export-subtitles/",
)


@app.middleware("http")
async def require_auth_token(request, call_next):
    path = request.url.path
    if APP_AUTH_TOKEN and path.startswith("/api/") and path not in _AUTH_EXEMPT_PATHS:
        supplied = (request.headers.get("X-API-Token") or "").strip()
        if not supplied:
            authorization = request.headers.get("Authorization") or ""
            if authorization.lower().startswith("bearer "):
                supplied = authorization[7:].strip()
        # 浏览器直接发起的加载（EventSource、<audio src>、下载链接）无法自定义
        # 请求头，这些只读 GET 路由额外接受查询参数形式的令牌。
        if not supplied and path.startswith(_AUTH_QUERY_TOKEN_PREFIXES):
            supplied = (request.query_params.get("token") or "").strip()
        if not hmac.compare_digest(supplied, APP_AUTH_TOKEN):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


if APP_AUTH_TOKEN:
    logger.info("APP_AUTH_TOKEN is set; /api/* requires a token")

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")

# 创建临时目录
configured_temp = _resolve_optional_path(os.getenv("APP_TEMP_DIR", ""))
TEMP_DIR = configured_temp or (PROJECT_ROOT / "temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 初始化处理器
video_processor = VideoProcessor()
transcriber = Transcriber()
summarizer = Summarizer()
translator = Translator()
tts_engine = VieNeuTTS()
elevenlabs_tts_engine = ElevenLabsTTS()
fpt_tts_engine = FptTTS()

# 存储任务状态 - 使用文件持久化
import threading

TASKS_FILE = TEMP_DIR / "tasks.json"
TASKS_BACKUP_FILE = TEMP_DIR / "tasks.json.bak"
tasks_lock = threading.Lock()
_TASKS_NEEDS_RESAVE = False

# 任务库写入去抖窗口（秒）；0 表示每次调用都立即落盘
TASKS_SAVE_INTERVAL = max(0.0, float(os.getenv("TASKS_SAVE_INTERVAL", "2.0")))
_last_save_ts = 0.0
_tasks_dirty = False

SENSITIVE_SETTING_KEYS = {
    "ai": {"apiKey"},
    "sources": {"douyinCookie", "bilibiliCookie"},
    "tts": {"elevenLabsApiKey", "fptApiKey"},
}


def _scrub_sensitive_settings(settings: Any) -> tuple[dict, bool]:
    if not isinstance(settings, dict):
        return {}, False
    cleaned: dict[str, Any] = {}
    changed = False
    for section, value in settings.items():
        if not isinstance(value, dict):
            cleaned[section] = value
            continue
        blocked = SENSITIVE_SETTING_KEYS.get(section, set())
        section_value = {}
        for key, item in value.items():
            if key in blocked:
                if item not in (None, ""):
                    changed = True
                continue
            section_value[key] = item
        cleaned[section] = section_value
    return cleaned, changed


def _scrub_task_record(task: Any) -> bool:
    if not isinstance(task, dict):
        return False
    changed = False
    settings, settings_changed = _scrub_sensitive_settings(task.get("settings"))
    if settings_changed:
        task["settings"] = settings
        changed = True
    for key in (
        "api_key", "openai_api_key", "douyin_cookie", "bilibili_cookie",
        "elevenlabs_api_key", "fpt_api_key",
    ):
        if key in task:
            task.pop(key, None)
            changed = True
    return changed

def load_tasks():
    """加载任务状态"""
    global _TASKS_NEEDS_RESAVE
    for candidate in (TASKS_FILE, TASKS_BACKUP_FILE):
        if not candidate.exists():
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("Task store must contain a JSON object")
            changed = False
            for task in loaded.values():
                changed = _scrub_task_record(task) or changed
            _TASKS_NEEDS_RESAVE = changed or candidate == TASKS_BACKUP_FILE
            if candidate == TASKS_BACKUP_FILE:
                logger.warning("Recovered task state from backup: %s", TASKS_BACKUP_FILE)
            return loaded
        except Exception as exc:
            logger.error("Failed to load task state from %s: %s", candidate, exc)
    return {}

def _write_tasks_now(tasks_data) -> bool:
    """原子写入任务状态；返回是否写入成功。"""
    global _last_save_ts, _tasks_dirty
    temp_file = TASKS_FILE.with_suffix(".json.tmp")
    try:
        with tasks_lock:
            for task in tasks_data.values():
                _scrub_task_record(task)
            with open(temp_file, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(tasks_data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_file, TASKS_FILE)
            shutil.copy2(TASKS_FILE, TASKS_BACKUP_FILE)
        _last_save_ts = time.monotonic()
        _tasks_dirty = False
        return True
    except Exception as e:
        logger.error("保存任务状态失败 (%s): %s", TASKS_FILE, e)
        return False
    finally:
        temp_file.unlink(missing_ok=True)


def save_tasks(tasks_data, force: bool = False) -> bool:
    """保存任务状态，默认按 TASKS_SAVE_INTERVAL 合并高频写入。

    进度回调每一步都会调用本函数，而整个任务库（含全部转录/翻译正文）
    每次都要序列化 + fsync + 备份复制，因此默认做去抖；终态调用传
    force=True，另有后台定时器兜底刷盘，最坏丢失窗口即 TASKS_SAVE_INTERVAL。
    """
    global _tasks_dirty
    if not force and (time.monotonic() - _last_save_ts) < TASKS_SAVE_INTERVAL:
        _tasks_dirty = True
        return True
    return _write_tasks_now(tasks_data)


async def _flush_tasks_periodically() -> None:
    while True:
        await asyncio.sleep(TASKS_SAVE_INTERVAL)
        if _tasks_dirty:
            await asyncio.to_thread(_write_tasks_now, tasks)


def _now_ts() -> float:
    return time.time()


def _safe_task_settings(settings: Any, redact_secrets: bool = True) -> dict:
    if not isinstance(settings, dict):
        return {}
    allowed_sections = {"ai", "sources", "ocr", "tts", "subtitle", "meta"}
    cleaned: dict[str, Any] = {}
    for key, value in settings.items():
        if key not in allowed_sections:
            continue
        if isinstance(value, dict):
            blocked = SENSITIVE_SETTING_KEYS.get(key, set()) if redact_secrets else set()
            cleaned[key] = {item_key: item for item_key, item in value.items() if item_key not in blocked}
        elif key == "meta" and isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
    try:
        payload = json.dumps(cleaned, ensure_ascii=False)
    except Exception:
        return {}
    if len(payload.encode("utf-8")) > 128 * 1024:
        raise HTTPException(status_code=413, detail="Task settings are too large")
    return cleaned


def _parse_task_settings(raw: str) -> dict:
    if not raw:
        return {}
    try:
        return _safe_task_settings(json.loads(raw), redact_secrets=False)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task settings JSON")


def _task_saved_settings(task: dict) -> dict:
    settings = task.get("settings")
    return settings if isinstance(settings, dict) else {}


def _task_ai_setting(task: dict, key: str, default: str = "") -> str:
    value = _task_saved_settings(task).get("ai", {}).get(key, default)
    return str(value or "").strip()


def _store_task_settings(task_id: str, settings: dict) -> dict:
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    cleaned = _safe_task_settings(settings)
    task = tasks[task_id]
    task["settings"] = cleaned
    ai = cleaned.get("ai") if isinstance(cleaned.get("ai"), dict) else {}
    if ai.get("summaryLanguage"):
        task["summary_language"] = str(ai.get("summaryLanguage") or "").strip()
    ocr = cleaned.get("ocr") if isinstance(cleaned.get("ocr"), dict) else {}
    if ocr:
        task["ocr_settings"] = {
            "language": ocr.get("language") or "eng+vie+chi_sim",
            "fps": ocr.get("fps") or 2.0,
            "crop_top": ocr.get("cropTop") if ocr.get("cropTop") is not None else 0,
            "crop_bottom": ocr.get("cropBottom") if ocr.get("cropBottom") is not None else 0,
            "crop_left": ocr.get("cropLeft") if ocr.get("cropLeft") is not None else 0,
            "crop_right": ocr.get("cropRight") if ocr.get("cropRight") is not None else 0,
            "confidence": ocr.get("confidence") if ocr.get("confidence") is not None else 45,
        }
    task["updated_at"] = _now_ts()
    save_tasks(tasks, force=True)
    return cleaned


def _safe_temp_path(value: str) -> Optional[Path]:
    if not value:
        return None
    raw = str(value)
    path = Path(raw)
    if not path.is_absolute():
        path = TEMP_DIR / raw
    try:
        resolved = path.resolve()
        if resolved == TEMP_DIR.resolve() or TEMP_DIR.resolve() not in resolved.parents:
            return None
        return resolved
    except Exception:
        return None


def _delete_task_files(task_id: str, task: dict) -> int:
    removed = 0
    filenames = set()
    path_keys = ("script_path", "summary_path", "translation_path")
    filename_keys = (
        "raw_script_file",
        "media_filename",
        "translation_filename",
        "export_video_filename",
        "dubbed_video_filename",
    )

    for key in filename_keys:
        value = task.get(key)
        if value:
            filenames.add(str(value))
    for key in path_keys:
        path = _safe_temp_path(task.get(key) or "")
        if path and path.exists() and path.is_file():
            try:
                path.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"删除任务文件失败 {path}: {e}")

    # 按 safe_title/short_id 收集派生产物：导出的 .ass 字幕、.srt/.vtt 以及
    # transcript/translation/summary/raw 的 .md，这些都不在上面的字段里。
    safe_title = str(task.get("safe_title") or "").strip()
    short_id = str(task.get("short_id") or task_id.replace("-", "")[:6]).strip()
    if short_id:
        derived_patterns = [f"*_{short_id}.srt", f"*_{short_id}.vtt"]
        if safe_title:
            derived_patterns.extend([
                f"export_{safe_title}_{short_id}_*",
                f"transcript_{safe_title}_{short_id}.*",
                f"translation_{safe_title}_{short_id}.*",
                f"summary_{safe_title}_{short_id}.*",
                f"raw_{safe_title}_{short_id}.*",
            ])
        for pattern in derived_patterns:
            for path in TEMP_DIR.glob(pattern):
                if path.is_file():
                    filenames.add(path.name)

    for filename in filenames:
        path = _safe_temp_path(filename)
        if path and path.exists() and path.is_file():
            try:
                path.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"删除任务文件失败 {path}: {e}")

    task_prefix = task_id.replace("-", "")[:12]
    for pattern in (
        f"cookie_files_{task_prefix}*",
        f"dub_refs_{task_prefix}*",
        f"dub_{task_prefix}*",
        f"ocr_frames_{task_prefix}*",
    ):
        for path in TEMP_DIR.glob(pattern):
            safe_path = _safe_temp_path(str(path))
            if not safe_path or not safe_path.exists() or not safe_path.is_dir():
                continue
            try:
                shutil.rmtree(safe_path)
                removed += 1
            except Exception as e:
                logger.warning(f"删除任务目录失败 {safe_path}: {e}")

    return removed


def _append_task_log(task_data: dict) -> bool:
    logs = task_data.get("task_logs")
    if not isinstance(logs, list):
        logs = []

    entries = []
    message = task_data.get("message") or task_data.get("error") or ""
    if message:
        entries.append({
            "ts": _now_ts(),
            "status": task_data.get("status") or "",
            "progress": max(0, min(100, int(task_data.get("progress") or 0))),
            "message": str(message),
        })

    dub_message = task_data.get("dub_message") or ""
    dub_status = task_data.get("dub_status") or ""
    if dub_message and dub_status:
        entries.append({
            "ts": _now_ts(),
            "status": f"dub_{dub_status}",
            "progress": max(0, min(100, int(task_data.get("dub_progress") or 0))),
            "message": f"Dub: {dub_message}",
        })

    changed = False
    for entry in entries:
        previous = logs[-1] if logs else {}
        if (
            previous.get("message") == entry["message"]
            and previous.get("progress") == entry["progress"]
            and previous.get("status") == entry["status"]
        ):
            continue
        logs.append(entry)
        changed = True

    if changed:
        task_data["task_logs"] = logs[-80:]
    return changed


async def broadcast_task_update(task_id: str, task_data: dict):
    """向所有连接的SSE客户端广播任务状态更新"""
    if _append_task_log(task_data) and task_id in tasks:
        save_tasks(tasks)
    logger.info(f"广播任务更新: {task_id}, 状态: {task_data.get('status')}, 连接数: {len(sse_connections.get(task_id, []))}")
    if task_id in sse_connections:
        # 只序列化一次，所有连接共用同一份负载
        payload = json.dumps(task_data, ensure_ascii=False)
        connections_to_remove = []
        for queue in sse_connections[task_id]:
            try:
                # 队列有界：客户端不再消费时直接断开，避免整份任务 JSON 无限堆积
                queue.put_nowait(payload)
                logger.debug(f"消息已发送到队列: {task_id}")
            except asyncio.QueueFull:
                logger.warning("SSE 客户端消费过慢，断开该连接: %s", task_id)
                connections_to_remove.append(queue)
            except Exception as e:
                logger.warning(f"发送消息到队列失败: {e}")
                connections_to_remove.append(queue)

        # 移除断开的连接
        for queue in connections_to_remove:
            sse_connections[task_id].remove(queue)
        
        # 如果没有连接了，清理该任务的连接列表
        if not sse_connections[task_id]:
            del sse_connections[task_id]


async def _set_dub_progress(task_id: str, status: str, progress: int, message: str) -> None:
    task = tasks.get(task_id)
    if not task:
        return
    task.update({
        "dub_status": status,
        "dub_progress": max(0, min(100, int(progress))),
        "dub_message": message,
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, task)

# 启动时加载任务状态
tasks = load_tasks()
if _TASKS_NEEDS_RESAVE:
    save_tasks(tasks)
# 存储正在处理的URL，防止重复处理
processing_urls = set()
# 存储活跃的任务对象，用于控制和取消
active_tasks = {}
# 存储SSE连接，用于实时推送状态更新
sse_connections = {}
# 单个SSE连接的最大积压消息数；超出即视为客户端已失联
SSE_QUEUE_MAXSIZE = _env_int("SSE_QUEUE_MAXSIZE", 64, 1, 10000)
# 配音混音每批的片段数：每个片段占一个 -i，全部放进一条命令会超出
# Windows 的 32KB 命令行上限（约 300 个片段即触顶）
DUB_MIX_BATCH_SIZE = _env_int("DUB_MIX_BATCH_SIZE", 120, 1, 1000)


def _cleanup_done_active_tasks() -> None:
    for task_id, bg_task in list(active_tasks.items()):
        if hasattr(bg_task, "done") and bg_task.done():
            active_tasks.pop(task_id, None)


def _recover_interrupted_processing_tasks() -> None:
    changed = False
    now = _now_ts()
    for task_id, task in tasks.items():
        if task.get("status") != "processing":
            continue
        task.update({
            "status": "error",
            "error": "Task was interrupted before it finished. Click Resume task or run the step again.",
            "message": "Task was interrupted before it finished. Click Resume task or run the step again.",
            "updated_at": now,
        })
        task.pop("completed_at", None)
        changed = True
        logger.warning("Recovered interrupted processing task: %s", task_id)
    if changed:
        save_tasks(tasks, force=True)


_tasks_flusher: Optional[asyncio.Task] = None


@app.on_event("startup")
async def configure_executor():
    global _tasks_flusher
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=WORKER_THREADS))
    _recover_interrupted_processing_tasks()
    if TASKS_SAVE_INTERVAL > 0:
        _tasks_flusher = asyncio.create_task(_flush_tasks_periodically())
    logger.info("Background worker thread pool configured: %s", WORKER_THREADS)


@app.on_event("shutdown")
async def flush_tasks_on_shutdown():
    global _tasks_flusher
    if _tasks_flusher:
        _tasks_flusher.cancel()
        try:
            await _tasks_flusher
        except asyncio.CancelledError:
            pass
        _tasks_flusher = None
    if _tasks_dirty:
        _write_tasks_now(tasks)

# 本地上传：允许的类型与大小上限（MB），可用环境变量 UPLOAD_MAX_MB 调整
UPLOAD_ALLOWED_EXT = frozenset({".txt", ".mp3", ".mp4", ".m4a", ".wav", ".webm", ".mkv", ".ogg", ".flac"})
UPLOAD_MAX_MB = _env_int("UPLOAD_MAX_MB", 200, 1, 10240)

VIDEO_HOST_MARKERS = (
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "douyin.com",
    "bilibili.com",
    "facebook.com",
    "instagram.com",
    "soundcloud.com",
)


def _reject_non_public_address(hostname: str, addresses: list[str]) -> None:
    """Allow loopback (local LLM runtimes) but block other internal targets.

    Best effort only: a DNS rebind between this check and the actual request is
    not covered. It does close the cloud metadata endpoint (169.254.169.254)
    and LAN scanning, which is what this validation is for.
    """
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_loopback:
            continue
        if ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model API Base URL resolves to a non-public address ({hostname} -> {address}). "
                    "Only loopback (127.0.0.1/localhost) and public endpoints are allowed."
                ),
            )


async def _validate_model_base_url(base_url: Optional[str]) -> Optional[str]:
    if not base_url:
        return None

    parsed = urlparse(base_url)
    host = (parsed.netloc or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise HTTPException(status_code=400, detail="Model API Base URL must be an http(s) URL")

    if any(marker in host for marker in VIDEO_HOST_MARKERS):
        raise HTTPException(
            status_code=400,
            detail="Model API Base URL must be an OpenAI-compatible API endpoint, not a video URL",
        )

    hostname = parsed.hostname or ""
    if not hostname:
        raise HTTPException(status_code=400, detail="Model API Base URL has no host")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, port, 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Model API Base URL host could not be resolved: {hostname}",
        ) from exc

    _reject_non_public_address(hostname, [info[4][0] for info in infos])
    return base_url.rstrip("/")


def _sanitize_title_for_filename(title: str) -> str:
    """将视频标题清洗为安全的文件名片段。"""
    if not title:
        return "untitled"
    # 仅保留字母数字、下划线、连字符与空格
    safe = re.sub(r"[^\w\-\s]", "", title)
    # 压缩空白并转为下划线
    safe = re.sub(r"\s+", "_", safe).strip("._-")
    # 最长限制，避免过长文件名问题
    return safe[:80] or "untitled"


def _txt_to_raw_transcript_markdown(body: str) -> str:
    """将纯文本包装为与 Whisper 输出结构一致的 Markdown。"""
    text = body.strip() if body.strip() else "(empty)"
    return "\n".join([
        "# Video Transcription",
        "",
        "**Detected Language:**",
        "**Language Probability:** —",
        "",
        "## Transcription Content",
        "",
        text,
    ])


def _time_to_seconds(value: str) -> float:
    parts = value.strip().split(":")
    if not parts:
        raise ValueError(f"Invalid timestamp: {value}")
    try:
        tail = float(parts[-1].replace(",", "."))
        head = [int(p) for p in parts[:-1]]
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value}") from exc
    if len(parts) == 2:
        return float(head[0] * 60 + tail)
    if len(parts) == 3:
        return float(head[0] * 3600 + head[1] * 60 + tail)
    raise ValueError(f"Invalid timestamp: {value}")


def _seconds_to_srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis >= 1000:
        whole += 1
        millis -= 1000
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _extract_timed_cues(markdown: str) -> list[dict]:
    text = (markdown or "").replace("\r\n", "\n")
    lines = text.split("\n")
    # 分钟位放宽到 3 位：历史任务里存过 "100:00" 这类把小时折进分钟的时间戳
    timestamp = r"\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"
    time_re = re.compile(
        rf"^\s*(?:\*\*)?\[({timestamp})\s*-\s*({timestamp})\](?:\*\*)?\s*$"
    )
    cues = []

    for idx, line in enumerate(lines):
        match = time_re.match(line)
        if not match:
            continue

        body = []
        for next_line in lines[idx + 1:]:
            if time_re.match(next_line) or re.match(r"^\s*#{1,6}\s+", next_line):
                break
            body.append(next_line)

        cue_text = "\n".join(body)
        cue_text = cue_text.replace("**", "")
        cue_text = re.sub(r"\n{2,}", "\n", cue_text).strip()
        if not cue_text:
            continue

        try:
            start = _time_to_seconds(match.group(1))
            end = _time_to_seconds(match.group(2))
        except ValueError:
            continue

        if end <= start:
            end = start + 1.0

        cues.append({"start": start, "end": end, "text": cue_text})

    return cues


def _write_srt(cues: list[dict], output_path: Path) -> None:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        subtitle_text = re.sub(r"[ \t]+", " ", cue["text"]).strip()
        subtitle_text = re.sub(r"\n{3,}", "\n\n", subtitle_text)
        blocks.append(
            f"{index}\n"
            f"{_seconds_to_srt_time(cue['start'])} --> {_seconds_to_srt_time(cue['end'])}\n"
            f"{subtitle_text}\n"
        )
    output_path.write_text("\n".join(blocks), encoding="utf-8")


def _seconds_to_vtt_time(seconds: float) -> str:
    return _seconds_to_srt_time(seconds).replace(",", ".")


def _seconds_to_display_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    centis = int(round((seconds - whole) * 100))
    if centis >= 100:
        whole += 1
        centis -= 100
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{centis:02d}"
    return f"{minutes:02d}:{secs:02d}.{centis:02d}"


def _write_vtt(cues: list[dict], output_path: Path) -> None:
    blocks = ["WEBVTT", ""]
    for cue in cues:
        subtitle_text = re.sub(r"[ \t]+", " ", cue["text"]).strip()
        subtitle_text = re.sub(r"\n{3,}", "\n\n", subtitle_text)
        blocks.append(
            f"{_seconds_to_vtt_time(cue['start'])} --> {_seconds_to_vtt_time(cue['end'])}\n"
            f"{subtitle_text}\n"
        )
    output_path.write_text("\n".join(blocks), encoding="utf-8")


def _cues_to_timed_markdown(cues: list[dict]) -> str:
    blocks = []
    for cue in cues:
        text = (cue.get("text") or "").strip()
        if not text:
            continue
        blocks.append(
            f"**[{_seconds_to_display_time(float(cue.get('start') or 0))} - "
            f"{_seconds_to_display_time(float(cue.get('end') or 0))}]**\n\n{text}"
        )
    return "\n\n".join(blocks)


def _normalize_ocr_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned and cleaned not in lines:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _ocr_similarity(a: str, b: str) -> float:
    a_key = re.sub(r"[\W_]+", "", (a or "").lower())
    b_key = re.sub(r"[\W_]+", "", (b or "").lower())
    if not a_key or not b_key:
        return 0.0
    return difflib.SequenceMatcher(None, a_key, b_key).ratio()


def _ocr_language_label(tesseract_lang: str) -> str:
    lang = (tesseract_lang or "").lower()
    if "vie" in lang:
        return "vi"
    if "chi" in lang or "zh" in lang:
        return "zh"
    if "jpn" in lang:
        return "ja"
    if "kor" in lang:
        return "ko"
    if "eng" in lang:
        return "en"
    return ""


def _parse_tesseract_tsv(tsv_text: str, min_confidence: int) -> str:
    lines_by_key = {}
    for row in (tsv_text or "").splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        text = (cols[11] or "").strip()
        if not text:
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            conf = -1
        if conf < min_confidence:
            continue
        key = tuple(cols[0:5])
        lines_by_key.setdefault(key, []).append(text)
    return _normalize_ocr_text("\n".join(" ".join(parts) for parts in lines_by_key.values()))


_rapid_ocr_engine = None


def _rapid_ocr_text(image_path: Path, min_confidence: int) -> str:
    global _rapid_ocr_engine
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return ""
    if _rapid_ocr_engine is None:
        _rapid_ocr_engine = RapidOCR()

    result, _ = _rapid_ocr_engine(str(image_path))
    if not result:
        return ""

    lines = []
    min_score = max(0.0, min(1.0, float(min_confidence or 0) / 100.0))
    for item in result:
        if len(item) < 3:
            continue
        text = str(item[1] or "").strip()
        try:
            score = float(item[2])
        except (TypeError, ValueError):
            score = 0.0
        if text and score >= min_score:
            lines.append(text)
    return _normalize_ocr_text("\n".join(lines))


async def _extract_hard_subtitle_ocr(
    task_id: str,
    media_path: Path,
    language: str = "eng+vie+chi_sim",
    fps: float = 2.0,
    crop_top: int = 0,
    crop_bottom: int = 0,
    crop_left: int = 0,
    crop_right: int = 0,
    confidence: int = 45,
) -> str:
    has_tesseract = TESSERACT_CMD is not None
    has_rapidocr = True
    try:
        from rapidocr_onnxruntime import RapidOCR as _RapidOCRCheck  # noqa: F401
    except Exception:
        has_rapidocr = False
    if not has_tesseract and not has_rapidocr:
        raise Exception("No OCR engine is installed in the container")
    if not media_path.exists():
        raise Exception("Video file for OCR is missing")

    fps = max(0.5, min(8.0, float(fps or 2.0)))
    crop_top = max(0, min(90, int(crop_top)))
    crop_bottom = max(0, min(90, int(crop_bottom)))
    crop_left = max(0, min(90, int(crop_left)))
    crop_right = max(0, min(90, int(crop_right)))
    if crop_top + crop_bottom >= 95:
        raise Exception("OCR crop top/bottom is too large")
    if crop_left + crop_right >= 95:
        raise Exception("OCR crop left/right is too large")
    confidence = max(0, min(100, int(confidence)))

    work_dir = TEMP_DIR / f"ocr_frames_{task_id.replace('-', '')[:12]}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    try:

        width_expr = max(1, 100 - crop_left - crop_right) / 100
        height_expr = max(1, 100 - crop_top - crop_bottom) / 100
        x_expr = crop_left / 100
        y_expr = crop_top / 100
        vf = f"fps={fps},crop=iw*{width_expr:.4f}:ih*{height_expr:.4f}:iw*{x_expr:.4f}:ih*{y_expr:.4f}"
        frame_pattern = work_dir / "frame_%06d.png"
        extract_cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(media_path.resolve()),
            "-vf", vf,
            "-vsync", "0",
            str(frame_pattern),
        ]
        result = await asyncio.to_thread(
            subprocess.run,
            extract_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise Exception(f"FFmpeg OCR frame extraction failed: {err[:800]}")

        frame_paths = sorted(work_dir.glob("frame_*.png"))
        if not frame_paths:
            raise Exception("No OCR frames were extracted")

        cues = []
        active = None
        frame_duration = 1.0 / fps
        total = len(frame_paths)
        last_reported = -1

        for index, frame_path in enumerate(frame_paths):
            current_time = index * frame_duration
            text = ""
            if has_rapidocr:
                text = await asyncio.to_thread(_rapid_ocr_text, frame_path, confidence)
            if not text and has_tesseract:
                cmd = [str(TESSERACT_CMD), str(frame_path), "stdout"]
                if TESSDATA_DIR:
                    cmd.extend(["--tessdata-dir", str(TESSDATA_DIR)])
                cmd.extend([
                    "-l", language or "eng+vie+chi_sim",
                    "--psm", "6",
                    "tsv",
                ])
                result = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    text = _parse_tesseract_tsv(result.stdout or "", confidence)
            if not text:
                if active:
                    active["end"] = max(active["end"], current_time)
                    if active["end"] - active["start"] >= 0.3:
                        cues.append(active)
                    active = None
            elif active and _ocr_similarity(active["text"], text) >= 0.86:
                active["end"] = current_time + frame_duration
                if len(text) > len(active["text"]):
                    active["text"] = text
            else:
                if active and active["end"] - active["start"] >= 0.3:
                    cues.append(active)
                active = {
                    "start": current_time,
                    "end": current_time + frame_duration,
                    "text": text,
                }

            progress = 45 + int((index + 1) / max(total, 1) * 35)
            if progress >= last_reported + 5:
                last_reported = progress
                tasks[task_id].update({
                    "progress": progress,
                    "message": f"Running OCR on subtitle frames {index + 1}/{total}...",
                })
                save_tasks(tasks)
                await broadcast_task_update(task_id, tasks[task_id])

        if active and active["end"] - active["start"] >= 0.3:
            cues.append(active)


        merged = []
        for cue in cues:
            cue["text"] = _normalize_ocr_text(cue.get("text") or "")
            if not cue["text"]:
                continue
            if merged and _ocr_similarity(merged[-1]["text"], cue["text"]) >= 0.9 and cue["start"] - merged[-1]["end"] <= 0.8:
                merged[-1]["end"] = max(merged[-1]["end"], cue["end"])
                if len(cue["text"]) > len(merged[-1]["text"]):
                    merged[-1]["text"] = cue["text"]
            else:
                merged.append(cue)

        if not merged:
            raise Exception("OCR did not find readable hard subtitles. Adjust crop/language/confidence settings.")

        detected = _ocr_language_label(language)
        body = _cues_to_timed_markdown(merged)
        return "\n".join([
            "# Video Transcription",
            "",
            f"**Detected Language:** {detected}",
            "**Language Probability:** OCR",
            "",
            "## Transcription Content",
            "",
            body,
        ])


    finally:
        # OCR 中途抛错时同样要清掉抽帧目录
        shutil.rmtree(work_dir, ignore_errors=True)
def _batch_cues_for_translation(cues: list[dict], max_chars: int = 3600, max_cues: int = 50) -> list[list[dict]]:
    batches = []
    current = []
    current_chars = 0
    for cue in cues:
        cue_chars = len(cue.get("text") or "") + 32
        if current and (len(current) >= max_cues or current_chars + cue_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(cue)
        current_chars += cue_chars
    if current:
        batches.append(current)
    return batches


async def _translate_timed_transcript(
    request_translator: Translator,
    raw_script: str,
    target_language: str,
    detected_language: str,
    name_glossary: Optional[list[dict]] = None,
    speaker_profile: Optional[dict] = None,
    progress_callback: Optional[Callable[[int, str], Awaitable[None]]] = None,
) -> str:
    source_cues = _extract_timed_cues(raw_script)
    if not source_cues:
        return await request_translator.translate_text(raw_script, target_language, detected_language, name_glossary, speaker_profile)

    translated_cues = []
    batches = _batch_cues_for_translation(source_cues)
    total_units = max(1, len(source_cues))
    completed_units = 0
    for batch_index, batch in enumerate(batches, start=1):
        if progress_callback:
            await progress_callback(
                min(93, 78 + int((completed_units / total_units) * 15)),
                f"Translating timed subtitles batch {batch_index}/{len(batches)}...",
            )
        batch_text = _cues_to_timed_markdown(batch)
        translated_batch_text = await request_translator.translate_text(
            batch_text,
            target_language,
            detected_language,
            name_glossary,
            speaker_profile,
        )
        translated_batch = _extract_timed_cues(translated_batch_text)
        if len(translated_batch) == len(batch):
            translated_cues.extend(translated_batch)
            completed_units += len(batch)
            continue

        logger.warning(
            "分批时间戳翻译覆盖不完整: expected=%s actual=%s, falling back per cue",
            len(batch),
            len(translated_batch),
        )
        for cue_index, cue in enumerate(batch, start=1):
            if progress_callback:
                await progress_callback(
                    min(94, 78 + int((completed_units / total_units) * 16)),
                    f"Translating subtitle cue {completed_units + cue_index}/{total_units}...",
                )
            translated_text = await request_translator.translate_text(
                cue.get("text") or "",
                target_language,
                detected_language,
                name_glossary,
                speaker_profile,
            )
            translated_cues.append({
                "start": cue["start"],
                "end": cue["end"],
                "text": _clean_tts_text(translated_text) or cue.get("text") or "",
            })
        completed_units += len(batch)

    return _cues_to_timed_markdown(translated_cues)


def _plain_translation_lines(markdown: str) -> list[str]:
    lines = []
    in_content = False
    for raw_line in (markdown or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("source:"):
            continue
        if re.match(r"^#{1,6}\s+", line):
            continue
        if "transcription content" in lower:
            in_content = True
            continue
        if "detected language" in lower or "language probability" in lower:
            continue
        if re.fullmatch(r"[*_\s]*\d+(?:\.\d+)?[*_\s]*", line) and not in_content:
            continue
        line = re.sub(r"^\s*[-*]\s+", "", line)
        line = line.replace("**", "").strip()
        if line:
            lines.append(line)
    return lines


def _rebuild_translation_cues_from_plain_text(task: dict) -> list[dict]:
    transcript_cues = _extract_timed_cues(task.get("timed_transcript") or task.get("script") or "")
    translated_lines = _plain_translation_lines(task.get("translation") or "")
    if not transcript_cues or not translated_lines:
        return []
    usable_count = min(len(transcript_cues), len(translated_lines))
    if usable_count < max(3, int(len(transcript_cues) * 0.35)):
        return []
    return [
        {
            "start": transcript_cues[index]["start"],
            "end": transcript_cues[index]["end"],
            "text": translated_lines[index],
        }
        for index in range(usable_count)
    ]


def _prefer_rebuilt_translation_cues(task: dict, timed_cues: list[dict]) -> list[dict]:
    rebuilt = _rebuild_translation_cues_from_plain_text(task)
    if not rebuilt:
        return timed_cues
    transcript_cues = _extract_timed_cues(task.get("timed_transcript") or task.get("script") or "")
    if not timed_cues:
        return rebuilt
    timed_start = float(timed_cues[0].get("start") or 0)
    transcript_start = float(transcript_cues[0].get("start") or 0) if transcript_cues else 0
    timed_duration = max(float(cue.get("end") or 0) for cue in timed_cues) - timed_start
    rebuilt_duration = max(float(cue.get("end") or 0) for cue in rebuilt) - float(rebuilt[0].get("start") or 0)
    starts_late = timed_start > transcript_start + 5
    much_shorter = len(timed_cues) < len(rebuilt) * 0.7 or timed_duration < rebuilt_duration * 0.7
    return rebuilt if starts_late or much_shorter else timed_cues


def _subtitle_cues_signature(cues: list[dict]) -> str:
    sample = [
        f"{cue.get('start')}|{cue.get('end')}|{cue.get('text')}"
        for cue in cues[:5]
    ]
    if len(cues) > 5:
        sample.append(f"...{len(cues)}...")
        sample.extend(
            f"{cue.get('start')}|{cue.get('end')}|{cue.get('text')}"
            for cue in cues[-3:]
        )
    return hashlib.sha1("\n".join(sample).encode("utf-8")).hexdigest()[:10]


def _merge_short_subtitle_cues(cues: list[dict], min_duration: float = 3.0) -> list[dict]:
    try:
        threshold = max(0.0, min(30.0, float(min_duration or 0)))
    except (TypeError, ValueError):
        threshold = 3.0
    if threshold <= 0 or len(cues) < 2:
        return cues

    merged = []
    index = 0
    while index < len(cues):
        cue = cues[index]
        start = float(cue.get("start") or 0)
        end = float(cue.get("end") or start)
        text_parts = [(cue.get("text") or "").strip()]

        if end - start >= threshold:
            merged.append({"start": start, "end": end, "text": text_parts[0]})
            index += 1
            continue

        index += 1
        while index < len(cues) and end - start < threshold:
            next_cue = cues[index]
            end = max(end, float(next_cue.get("end") or end))
            next_text = (next_cue.get("text") or "").strip()
            if next_text:
                text_parts.append(next_text)
            index += 1

        merged.append({
            "start": start,
            "end": end,
            "text": "\n".join(part for part in text_parts if part),
        })

    return merged


def _offset_subtitle_cues(cues: list[dict], offset_seconds: float) -> list[dict]:
    try:
        offset = max(-10.0, min(10.0, float(offset_seconds or 0.0)))
    except (TypeError, ValueError):
        offset = 0.0
    if abs(offset) < 0.001:
        return cues

    shifted = []
    for cue in cues:
        next_cue = dict(cue)
        start = max(0.0, float(next_cue.get("start") or 0) + offset)
        end = max(start + 0.05, float(next_cue.get("end") or start) + offset)
        next_cue["start"] = start
        next_cue["end"] = end
        shifted.append(next_cue)
    return shifted


def _ass_escape_text(text: str) -> str:
    cleaned = re.sub(r"[ \t]+", " ", text or "").strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return (
        cleaned.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def _seconds_to_ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    centis = int(round((seconds - whole) * 100))
    if centis >= 100:
        whole += 1
        centis -= 100
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _positioned_subtitle_point(
    width: int,
    height: int,
    position: str,
    x_offset: int,
    y_offset: int,
    line_shift: int = 0,
) -> tuple[int, int, int]:
    pos = (position or "bottom").lower()
    alignment = {"top": 8, "middle": 5, "bottom": 2}.get(pos, 2)
    x = int(round(width / 2 + x_offset))
    if pos == "top":
        y = int(round(height * 0.12 + y_offset + line_shift))
    elif pos == "middle":
        y = int(round(height * 0.50 + y_offset + line_shift))
    else:
        y = int(round(height * 0.88 + y_offset + line_shift))
    return max(0, x), max(0, y), alignment


def _write_ass(
    cues: list[dict],
    output_path: Path,
    width: int,
    height: int,
    font_size: int,
    text_color: str,
    outline_color: str,
    box_color: str,
    position: str,
    x_offset: int,
    y_offset: int,
    line_shift: int = 0,
) -> None:
    size = max(14, min(72, int(font_size or 28)))
    x, y, alignment = _positioned_subtitle_point(width, height, position, x_offset, y_offset, line_shift)
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,"
        f"{SUBTITLE_FONT_FAMILY},{size},{_hex_to_ass_color(text_color, '00')},&H000000FF,"
        f"{_hex_to_ass_color(outline_color, '00')},{_hex_to_ass_color(box_color, '80')},"
        f"0,0,0,0,100,100,0,0,3,2,0,{alignment},20,20,20,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = []
    for cue in cues:
        text = _ass_escape_text(cue.get("text") or "")
        if not text:
            continue
        events.append(
            "Dialogue: 0,"
            f"{_seconds_to_ass_time(cue['start'])},{_seconds_to_ass_time(cue['end'])},"
            f"Default,,0,0,0,,{{\\an{alignment}\\pos({x},{y})}}{text}"
        )
    output_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")


def _select_subtitle_cues(task: dict, source: str, merge_min_duration: float = 3.0) -> list[dict]:
    selected = (source or "translation").lower()
    if selected == "translation":
        subtitle_source = task.get("timed_translation") or task.get("translation") or ""
        cues = _prefer_rebuilt_translation_cues(task, _extract_timed_cues(subtitle_source))
        return _merge_short_subtitle_cues(cues, merge_min_duration)

    if selected in {"transcript", "original"}:
        subtitle_source = task.get("timed_transcript") or task.get("script") or ""
        return _merge_short_subtitle_cues(_extract_timed_cues(subtitle_source), merge_min_duration)

    raise HTTPException(status_code=400, detail="Invalid subtitle source")


def _dub_cues_from_original_timing(task: dict, merge_min_duration: float = 0.0) -> tuple[list[dict], int, int]:
    """按时间轴而非下标对齐译文与原文。

    下标配对在模型合并/拆分任一条字幕后就会整体错位，且因为每个槽位都有文本，
    missing_count 检查也发现不了。改用中点落入原文区间来匹配。
    """
    original_cues = _select_subtitle_cues(task, "transcript", merge_min_duration)
    translation_cues = _select_subtitle_cues(task, "translation", merge_min_duration)
    if not original_cues or not translation_cues:
        return [], 0, len(original_cues)

    bounds = [
        (float(cue.get("start") or 0), float(cue.get("end") or cue.get("start") or 0))
        for cue in original_cues
    ]

    # 每条译文只能归属一条原文，否则边界上的译文会被重复配音。
    buckets: dict[int, list[str]] = {}
    for cue in translation_cues:
        midpoint = _cue_midpoint(cue)
        target = next(
            (index for index, (start, end) in enumerate(bounds) if start <= midpoint <= end),
            None,
        )
        if target is None:
            # 落在所有原文区间之外：归给中点距离最近的一条
            target = min(
                range(len(bounds)),
                key=lambda index: abs(midpoint - (bounds[index][0] + bounds[index][1]) / 2),
            )
        text = _clean_tts_text(cue.get("text") or "")
        if text:
            buckets.setdefault(target, []).append(text)

    aligned = []
    translated_count = 0
    missing_count = 0
    for index, (start, end) in enumerate(bounds):
        text = " ".join(buckets.get(index, [])).strip()
        if text:
            translated_count += 1
        else:
            missing_count += 1
            text = _clean_tts_text(original_cues[index].get("text") or "")
        aligned.append({"start": start, "end": end, "text": text})

    return aligned, translated_count, missing_count


def _replace_cue_text(markdown: str, start: float, end: float, text: str) -> tuple[str, bool]:
    cues = _extract_timed_cues(markdown or "")
    if not cues:
        return markdown or "", False
    target_mid = start + max(0.0, end - start) / 2
    changed = False
    for cue in cues:
        cue_start = float(cue.get("start") or 0)
        cue_end = float(cue.get("end") or cue_start)
        cue_mid = cue_start + max(0.0, cue_end - cue_start) / 2
        if abs(cue_start - start) < 0.05 and abs(cue_end - end) < 0.05 or cue_start <= target_mid <= cue_end:
            cue["text"] = text.strip()
            changed = True
            break
    if not changed:
        return markdown or "", False
    return _cues_to_timed_markdown(cues), True


def _prepare_subtitle_filter(
    task: dict,
    media_path: Path,
    style_sig: str,
    subtitle_mode: str,
    subtitle_source: str,
    secondary_subtitle_source: str,
    font_size: int,
    text_color: str,
    outline_color: str,
    box_color: str,
    secondary_font_size: int,
    secondary_text_color: str,
    secondary_outline_color: str,
    secondary_box_color: str,
    position: str,
    subtitle_x_offset: int,
    subtitle_y_offset: int,
    subtitle_merge_seconds: float,
    subtitle_time_offset: float = 0.0,
) -> str:
    primary_cues = _offset_subtitle_cues(_select_subtitle_cues(task, subtitle_source, subtitle_merge_seconds), subtitle_time_offset)
    dual_enabled = (subtitle_mode or "single").lower() == "dual"
    secondary_cues = (
        _offset_subtitle_cues(_select_subtitle_cues(task, secondary_subtitle_source, subtitle_merge_seconds), subtitle_time_offset)
        if dual_enabled
        else []
    )
    if not primary_cues:
        raise HTTPException(status_code=400, detail="没有可用于导出视频的时间戳字幕")
    if dual_enabled and not secondary_cues:
        raise HTTPException(status_code=400, detail="没有可用于第二字幕的时间戳字幕")

    safe_title = task.get("safe_title") or "video"
    short_id = task.get("short_id") or "task"
    sub_path = TEMP_DIR / f"export_{safe_title}_{short_id}_{style_sig}_primary.ass"
    sub2_path = TEMP_DIR / f"export_{safe_title}_{short_id}_{style_sig}_secondary.ass"

    width, height = _probe_video_size(media_path) if _has_media_stream(media_path, "v:0", "video") else (1280, 720)
    dual_gap = max(int(font_size or 28), int(secondary_font_size or 22)) + 14
    pos = (position or "bottom").lower()
    primary_shift = 0
    secondary_shift = 0
    if dual_enabled:
        if pos == "top":
            primary_shift, secondary_shift = 0, dual_gap
        elif pos == "middle":
            primary_shift, secondary_shift = -(dual_gap // 2), dual_gap // 2
        else:
            primary_shift, secondary_shift = -dual_gap, 0

    _write_ass(
        primary_cues,
        sub_path,
        width=width,
        height=height,
        font_size=font_size,
        text_color=text_color,
        outline_color=outline_color,
        box_color=box_color,
        position=position,
        x_offset=subtitle_x_offset,
        y_offset=subtitle_y_offset,
        line_shift=primary_shift,
    )
    subtitle_filter = _subtitle_filter_path(sub_path)
    if dual_enabled:
        _write_ass(
            secondary_cues,
            sub2_path,
            width=width,
            height=height,
            font_size=secondary_font_size,
            text_color=secondary_text_color,
            outline_color=secondary_outline_color,
            box_color=secondary_box_color,
            position=position,
            x_offset=subtitle_x_offset,
            y_offset=subtitle_y_offset,
            line_shift=secondary_shift,
        )
        subtitle_filter += f",{_subtitle_filter_path(sub2_path)}"

    return subtitle_filter


def _cue_midpoint(cue: dict) -> float:
    start = float(cue.get("start") or 0)
    end = float(cue.get("end") or start)
    return start + max(0.0, end - start) / 2


def _midpoint_is_covered(cue: dict, ranges: list[tuple[float, float]]) -> bool:
    midpoint = _cue_midpoint(cue)
    return any(start <= midpoint <= end for start, end in ranges)


def _dub_translation_cues_and_gap_count(task: dict, merge_min_duration: float) -> tuple[list[dict], int, int]:
    translated_cues = _select_subtitle_cues(task, "translation", merge_min_duration)
    translated_count = len(translated_cues)
    if not translated_cues:
        return [], 0, 0

    combined = list(translated_cues)
    ranges = [
        (float(cue.get("start") or 0), float(cue.get("end") or 0))
        for cue in combined
    ]

    timed_translation = _merge_short_subtitle_cues(
        _extract_timed_cues(task.get("timed_translation") or ""),
        merge_min_duration,
    )
    for cue in timed_translation:
        if not _midpoint_is_covered(cue, ranges):
            combined.append(cue)
            ranges.append((float(cue.get("start") or 0), float(cue.get("end") or 0)))

    missing_count = 0
    original_cues = _select_subtitle_cues(task, "transcript", merge_min_duration)
    for cue in original_cues:
        if not _midpoint_is_covered(cue, ranges):
            missing_count += 1

    combined.sort(key=lambda cue: (float(cue.get("start") or 0), float(cue.get("end") or 0)))
    return combined, translated_count, missing_count


def _hex_to_ass_color(hex_color: str, alpha: str = "00") -> str:
    value = (hex_color or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value = "000000"
    rr, gg, bb = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha}{bb}{gg}{rr}"


def _safe_filter_path(path: Path) -> str:
    resolved = path.resolve().as_posix().replace("'", r"\'")
    escaped = resolved.replace(":", r"\:")
    return f"filename='{escaped}'"


def _safe_filter_directory(path: Path) -> str:
    resolved = path.resolve().as_posix().replace("'", r"\'")
    escaped = resolved.replace(":", r"\:")
    return f"'{escaped}'"


def _subtitle_filter_path(path: Path) -> str:
    value = f"subtitles={_safe_filter_path(path)}"
    if SUBTITLE_FONTS_DIR and SUBTITLE_FONTS_DIR.is_dir():
        value += f":fontsdir={_safe_filter_directory(SUBTITLE_FONTS_DIR)}"
    return value


def _probe_duration(media_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return 0.0
    try:
        return max(0.0, float((result.stdout or "").strip()))
    except ValueError:
        return 0.0


def _probe_video_size(media_path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        str(media_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode == 0:
        match = re.search(r"(\d+)x(\d+)", result.stdout or "")
        if match:
            return max(1, int(match.group(1))), max(1, int(match.group(2)))
    return 1280, 720


def _has_media_stream(media_path: Path, selector: str, stream_type: str) -> bool:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", selector,
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(media_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode == 0 and stream_type in (result.stdout or "").lower()


def _is_playable_video(media_path: Path) -> bool:
    if not media_path.exists() or media_path.stat().st_size <= 0:
        return False
    return _has_media_stream(media_path, "v:0", "video") and _probe_duration(media_path) > 0


def _has_audible_audio(media_path: Path) -> bool:
    if not _has_media_stream(media_path, "a:0", "audio"):
        return False
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin",
        "-i", str(media_path),
        "-map", "0:a:0",
        "-af", "volumedetect",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = f"{result.stderr}\n{result.stdout}"
    if result.returncode != 0:
        return False
    mean_match = re.search(r"mean_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", output, flags=re.IGNORECASE)
    max_match = re.search(r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", output, flags=re.IGNORECASE)
    if not mean_match or not max_match:
        return False
    if mean_match.group(1).lower() == "-inf" or max_match.group(1).lower() == "-inf":
        return False
    mean_volume = float(mean_match.group(1))
    max_volume = float(max_match.group(1))
    return mean_volume > -80.0 and max_volume > -32.0


def _clean_tts_text(text: str) -> str:
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")
    cleaned = re.sub(r"[*_`>#-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _infer_cue_gender(cue: dict) -> Optional[str]:
    for key in ("gender", "speaker_gender", "voice_gender"):
        value = str(cue.get(key) or "").strip().lower()
        if value in {"male", "man", "m", "nam", "男"}:
            return "male"
        if value in {"female", "woman", "f", "nu", "nữ", "女"}:
            return "female"

    text = _clean_tts_text(cue.get("text") or "")
    prefix_match = re.match(
        r"^\s*(?:speaker\s*)?(male|female|man|woman|nam|nữ|nu|男|女)\s*[:：\-]\s+",
        text,
        flags=re.IGNORECASE,
    )
    if prefix_match:
        value = prefix_match.group(1).lower()
        return "male" if value in {"male", "man", "nam", "男"} else "female"
    return None


def _voice_profile_for_cue(index: int, cue: dict, speaker_mode: str, voice_profiles: Optional[dict]) -> tuple[str, dict]:
    profiles = voice_profiles or {}
    mode = (speaker_mode or "default").lower()
    if mode == "all_male":
        return "male", profiles.get("male") or profiles.get("default") or {}
    if mode == "all_female":
        return "female", profiles.get("female") or profiles.get("default") or {}
    if mode == "auto_gender":
        gender = _infer_cue_gender(cue)
        if gender in {"male", "female"}:
            return gender, profiles.get(gender) or profiles.get("default") or {}
        return "default", profiles.get("default") or {}
    if mode == "alternate":
        key = "male" if index % 2 == 1 else "female"
        return key, profiles.get(key) or profiles.get("default") or {}
    return "default", profiles.get("default") or {}


def _apply_voice_overrides(cues: list[dict], overrides_json: str) -> list[dict]:
    raw = (overrides_json or "").strip()
    if not raw:
        return cues
    try:
        overrides = json.loads(raw)
    except Exception:
        return cues
    if not isinstance(overrides, list):
        return cues

    normalized = []
    for item in overrides:
        if not isinstance(item, dict):
            continue
        voice = str(item.get("voice") or "").strip().lower()
        if voice not in {"male", "female", "default"}:
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        normalized.append((start, end, voice))

    if not normalized:
        return cues

    patched = []
    for cue in cues:
        next_cue = dict(cue)
        midpoint = _cue_midpoint(next_cue)
        for start, end, voice in normalized:
            if start <= midpoint <= end:
                next_cue["voice_gender"] = voice
                break
        patched.append(next_cue)
    return patched


def _scale_dub_cue_timing(cues: list[dict], timing_scale: float) -> list[dict]:
    scale = _normalize_dub_timing_scale(timing_scale)
    if abs(scale - 1.0) < 0.0005:
        return cues

    scaled = []
    for cue in cues:
        next_cue = dict(cue)
        start = max(0.0, float(next_cue.get("start") or 0))
        original_end = max(start + 0.1, float(next_cue.get("end") or start))
        end = start + max(0.1, (original_end - start) * scale)
        next_cue["start"] = start
        next_cue["end"] = end
        scaled.append(next_cue)
    return scaled


def _normalize_dub_timing_scale(timing_scale: float) -> float:
    try:
        scale = float(timing_scale or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return scale if 0.8 <= scale <= 1.2 else 1.0


def _offset_dub_cue_timing(cues: list[dict], audio_offset: float) -> list[dict]:
    try:
        offset = max(-10.0, min(10.0, float(audio_offset or 0.0)))
    except (TypeError, ValueError):
        offset = 0.0
    if abs(offset) < 0.001:
        return cues

    shifted = []
    for cue in cues:
        next_cue = dict(cue)
        start = max(0.0, float(next_cue.get("start") or 0) + offset)
        end = max(start + 0.1, float(next_cue.get("end") or start) + offset)
        next_cue["start"] = start
        next_cue["end"] = end
        shifted.append(next_cue)
    return shifted


def _merge_short_dub_cues_by_voice(
    cues: list[dict],
    min_duration: float,
    speaker_mode: str,
    voice_profiles: Optional[dict],
) -> list[dict]:
    try:
        threshold = max(0.0, min(10.0, float(min_duration or 0)))
    except (TypeError, ValueError):
        threshold = 0.0
    if threshold <= 0 or len(cues) < 2:
        return cues
    max_merge_gap = 0.25

    resolved = [
        (_voice_profile_for_cue(index, cue, speaker_mode, voice_profiles)[0], dict(cue))
        for index, cue in enumerate(cues, start=1)
    ]
    merged = []
    index = 0
    while index < len(resolved):
        voice_key, cue = resolved[index]
        start = float(cue.get("start") or 0)
        end = float(cue.get("end") or start)
        text_parts = [(cue.get("text") or "").strip()]

        if end - start >= threshold:
            cue["start"] = start
            cue["end"] = end
            cue["text"] = text_parts[0]
            cue["voice_gender"] = voice_key
            merged.append(cue)
            index += 1
            continue

        index += 1
        while index < len(resolved) and end - start < threshold:
            next_voice_key, next_cue = resolved[index]
            if next_voice_key != voice_key:
                break
            next_start = float(next_cue.get("start") or end)
            if next_start - end > max_merge_gap:
                break
            end = max(end, float(next_cue.get("end") or end))
            next_text = (next_cue.get("text") or "").strip()
            if next_text:
                text_parts.append(next_text)
            index += 1

        cue["start"] = start
        cue["end"] = end
        cue["text"] = "\n".join(part for part in text_parts if part)
        cue["voice_gender"] = voice_key
        merged.append(cue)

    return merged


def _dub_style_signature(export_mode: str, speaker_mode: str, voice_profiles: Optional[dict]) -> str:
    parts = [export_mode, speaker_mode or "default"]
    for key in ("default", "male", "female"):
        profile = (voice_profiles or {}).get(key) or {}
        voice = (profile.get("voice") or "").strip()
        style = (profile.get("style") or "").strip()
        clone = "clone" if profile.get("ref_audio") else ""
        parts.append("_".join([key, voice, style, clone]))
    return _sanitize_title_for_filename("_".join(parts))[:64]


def _normalize_tts_provider(provider: str) -> str:
    value = (provider or "vieneu").strip().lower()
    if value in {"elevenlabs", "eleven_labs", "eventlabs", "11labs"}:
        return "elevenlabs"
    if value in {"fpt", "fptai", "fpt.ai"}:
        return "fpt"
    return "vieneu"


def _tts_engine_for_provider(provider: str):
    normalized = _normalize_tts_provider(provider)
    if normalized == "elevenlabs":
        return elevenlabs_tts_engine
    if normalized == "fpt":
        return fpt_tts_engine
    return tts_engine


async def _save_clone_upload(upload: Optional[UploadFile], work_dir: Path, label: str) -> Optional[str]:
    if not upload or not (upload.filename or "").strip():
        return None
    ext = Path(upload.filename).suffix.lower()
    if ext not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4"}:
        raise HTTPException(status_code=400, detail=f"Unsupported {label} clone audio file")
    out_path = work_dir / f"{label}_clone{ext}"
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{label} clone audio file is empty")
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return str(out_path)


async def _save_cookie_file_upload(upload: Optional[UploadFile], work_dir: Path, label: str) -> Optional[str]:
    if not upload or not (upload.filename or "").strip():
        return None
    ext = Path(upload.filename).suffix.lower()
    if ext not in {".txt", ".cookies", ".json"}:
        raise HTTPException(status_code=400, detail=f"Unsupported {label} cookie file")
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{label} cookie file is empty")
    if len(data) > 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"{label} cookie file is too large")
    text = data.decode("utf-8", errors="replace")
    cookie_text = _cookie_file_text_to_netscape(text, label)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / f"{label}_cookies.txt"
    out_path.write_text(cookie_text, encoding="utf-8")
    try:
        out_path.chmod(0o600)
    except Exception:
        pass
    return str(out_path)


def _cookie_domain_for_label(label: str) -> str:
    if label == "douyin":
        return ".douyin.com"
    if label == "bilibili":
        return ".bilibili.com"
    return ""


def _cookie_file_text_to_netscape(text: str, label: str) -> str:
    raw = (text or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail=f"{label} cookie file is empty")

    if "# Netscape HTTP Cookie File" in raw or "\t" in raw:
        lines = [line for line in raw.splitlines() if line.strip()]
        cookie_lines = [line for line in lines if not line.lstrip().startswith("#") and len(line.split("\t")) >= 7]
        if cookie_lines:
            return "\n".join(lines) + "\n"

    try:
        parsed = json.loads(raw)
        cookies = parsed if isinstance(parsed, list) else parsed.get("cookies") if isinstance(parsed, dict) else []
        if isinstance(cookies, list) and cookies:
            lines = [
                "# Netscape HTTP Cookie File",
                "# Generated from JSON cookie export.",
            ]
            fallback_domain = _cookie_domain_for_label(label)
            default_expires = str(int(time.time()) + 30 * 24 * 60 * 60)
            for item in cookies:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                value = str(item.get("value") or "").replace("\r", "").replace("\n", "")
                domain = str(item.get("domain") or fallback_domain).strip()
                if not domain:
                    continue
                include_subdomains = "TRUE" if domain.startswith(".") or not item.get("hostOnly", False) else "FALSE"
                path = str(item.get("path") or "/").strip() or "/"
                secure = "TRUE" if bool(item.get("secure")) else "FALSE"
                expires_raw = item.get("expirationDate", item.get("expires", item.get("expiration", "")))
                try:
                    expires = str(max(0, int(float(expires_raw))))
                except Exception:
                    expires = "0" if item.get("session") else default_expires
                lines.append("\t".join([domain, include_subdomains, path, secure, expires, name, value]))
            if len(lines) > 2:
                return "\n".join(lines) + "\n"
    except json.JSONDecodeError:
        pass

    cookie_header = raw
    for line in raw.splitlines():
        if re.match(r"^\s*cookie\s*:", line, flags=re.I):
            cookie_header = re.sub(r"^\s*cookie\s*:\s*", "", line, flags=re.I).strip()
            break
    cookie_header = re.sub(r"^\s*cookie\s*:\s*", "", cookie_header, flags=re.I).replace("\r", "").replace("\n", "; ")
    domain = _cookie_domain_for_label(label)
    if not domain:
        raise HTTPException(status_code=400, detail=f"{label} cookie file has no recognizable cookie domain")
    expires = str(int(time.time()) + 30 * 24 * 60 * 60)
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated from Cookie header.",
    ]
    for part in cookie_header.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            continue
        lines.append("\t".join([domain, "TRUE", "/", "FALSE", expires, name, value.strip()]))
    if len(lines) <= 2:
        raise HTTPException(status_code=400, detail=f"{label} cookie file has no recognizable cookies")
    return "\n".join(lines) + "\n"


def _normalize_for_equivalence(text: str) -> str:
    normalized = re.sub(r"source:\s*\S+", "", text or "", flags=re.IGNORECASE)
    normalized = re.sub(r"[*_`>#\-\[\]:]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized


def _texts_equivalent(left: str, right: str) -> bool:
    left_norm = _normalize_for_equivalence(left)
    right_norm = _normalize_for_equivalence(right)
    return bool(left_norm and right_norm and left_norm == right_norm)


def _timed_cue_texts_equivalent(left: str, right: str) -> bool:
    left_cues = _extract_timed_cues(left or "")
    right_cues = _extract_timed_cues(right or "")
    if not left_cues or not right_cues:
        return _texts_equivalent(left, right)
    left_text = "\n".join(str(cue.get("text") or "") for cue in left_cues)
    right_text = "\n".join(str(cue.get("text") or "") for cue in right_cues)
    return _texts_equivalent(left_text, right_text)


def _atempo_filter(speed: float) -> str:
    speed = max(1.0, min(float(speed), 100.0))
    parts = []
    while speed > 2.0:
        parts.append("atempo=2.0")
        speed /= 2.0
    parts.append(f"atempo={speed:.6f}")
    return ",".join(parts)


async def _fit_tts_clip_to_cue(input_path: Path, output_path: Path, cue_duration: float, max_speed: float = 1.3) -> Path:
    clip_duration = _probe_duration(input_path)
    if cue_duration <= 0 or clip_duration <= 0 or clip_duration <= cue_duration * 1.05:
        return input_path

    try:
        speed_limit = max(1.0, min(2.0, float(max_speed or 1.3)))
    except (TypeError, ValueError):
        speed_limit = 1.3
    speed = min(clip_duration / cue_duration, speed_limit)
    if speed <= 1.01:
        return input_path
    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-i", str(input_path),
        "-filter:a", _atempo_filter(speed),
        "-ar", "48000",
        "-ac", "1",
        str(output_path),
    ]
    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not output_path.exists():
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Fit TTS clip failed: {err[-800:]}")
    return output_path


async def _trim_tts_clip_to_window(input_path: Path, output_path: Path, max_duration: float) -> Path:
    if max_duration <= 0.05:
        return input_path
    clip_duration = _probe_duration(input_path)
    if clip_duration <= 0 or clip_duration <= max_duration + 0.02:
        return input_path

    fade_start = max(0.0, max_duration - 0.03)
    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-i", str(input_path),
        "-t", f"{max_duration:.3f}",
        "-af", f"afade=t=out:st={fade_start:.3f}:d=0.03",
        "-ar", "48000",
        "-ac", "1",
        str(output_path),
    ]
    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not output_path.exists():
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Trim TTS clip failed: {err[-800:]}")
    return output_path


async def _mix_dub_clips(
    clip_paths: list[tuple[dict, Path, float]],
    work_dir: Path,
    duration: float,
    task_id: Optional[str] = None,
    min_gap_ms: int = 80,
) -> Path:
    if not clip_paths:
        raise RuntimeError("No usable text was found for TTS")

    if task_id:
        await _set_dub_progress(task_id, "processing", 78, "Aligning translated voice track...")

    dub_path = work_dir / "dub_track.wav"
    source_clips = []
    gap_seconds = max(0.0, min(1.0, float(min_gap_ms or 0) / 1000.0))
    for index, (cue, clip_path, clip_duration) in enumerate(clip_paths, start=1):
        cue_start = max(0.0, float(cue.get("start") or 0))
        cue_end = max(cue_start + 0.05, float(cue.get("end") or cue_start))
        duration_seconds = max(0.0, float(clip_duration or _probe_duration(clip_path) or 0))
        source_clips.append({
            "index": index,
            "start": cue_start,
            "end": cue_end,
            "path": clip_path,
            "duration": duration_seconds,
        })
    source_clips.sort(key=lambda item: (item["start"], item["end"]))

    scheduled_clips = []
    latest_end = 0.0
    for index, item in enumerate(source_clips):
        next_start = source_clips[index + 1]["start"] if index + 1 < len(source_clips) else None
        window_end = item["end"]
        if next_start is not None:
            window_end = min(window_end, max(item["start"] + 0.05, next_start - gap_seconds))
        max_window = max(0.05, window_end - item["start"])
        clip_path = item["path"]
        duration_seconds = item["duration"]
        if duration_seconds > max_window + 0.02:
            trim_path = work_dir / f"tts_{item['index']:04d}_trim.wav"
            clip_path = await _trim_tts_clip_to_window(clip_path, trim_path, max_window)
            duration_seconds = _probe_duration(clip_path)
        latest_end = max(latest_end, item["start"] + duration_seconds)
        scheduled_clips.append((item["start"], clip_path))

    mix_duration = max(duration, latest_end, 1.0)

    async def _mix_batch(batch: list[tuple[float, Path]], output: Path, base_silence: bool) -> None:
        """Mix one batch of positioned clips onto a silent bed of mix_duration."""
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-f", "lavfi", "-t", f"{mix_duration:.3f}", "-i", "anullsrc=r=48000:cl=mono",
        ]
        for _, clip_path in batch:
            cmd.extend(["-i", str(clip_path)])
        labels, filters = [], []
        for index, (start, _) in enumerate(batch, start=1):
            label = f"a{index}"
            delay_ms = max(0, int(round(start * 1000)))
            filters.append(f"[{index}:a]adelay={delay_ms}:all=1,apad[{label}]")
            labels.append(f"[{label}]")
        inputs = "[0:a]" + "".join(labels)
        tail = ",alimiter=limit=0.95" if base_silence else ""
        filters.append(
            f"{inputs}amix=inputs={len(labels) + 1}:duration=first:"
            f"dropout_transition=0:normalize=0{tail}[aout]"
        )
        cmd.extend([
            "-filter_complex", ";".join(filters),
            "-map", "[aout]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output),
        ])
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0 or not output.exists():
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Build dub audio failed: {err[-1200:]}")

    # One "-i" per clip blows past the Windows 32k command-line limit at roughly
    # 300 clips (a 35-minute video has ~900), so mix in batches and combine.
    batch_size = max(1, DUB_MIX_BATCH_SIZE)
    if len(scheduled_clips) <= batch_size:
        await _mix_batch(scheduled_clips, dub_path, base_silence=True)
    else:
        partials = []
        for offset in range(0, len(scheduled_clips), batch_size):
            batch = scheduled_clips[offset:offset + batch_size]
            part = work_dir / f"dub_part_{offset // batch_size:03d}.wav"
            await _mix_batch(batch, part, base_silence=False)
            partials.append(part)
            if task_id:
                await _set_dub_progress(
                    task_id, "processing",
                    78 + int((offset + len(batch)) / len(scheduled_clips) * 4),
                    f"Aligning voice track {offset + len(batch)}/{len(scheduled_clips)}...",
                )
        logger.info("配音混音分 %s 批完成，开始合并", len(partials))
        combine = ["ffmpeg", "-y", "-nostdin"]
        for part in partials:
            combine.extend(["-i", str(part)])
        combine.extend([
            "-filter_complex",
            "".join(f"[{i}:a]" for i in range(len(partials)))
            + f"amix=inputs={len(partials)}:duration=longest:"
              "dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]",
            "-map", "[aout]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(dub_path),
        ])
        result = await asyncio.to_thread(
            subprocess.run, combine, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0 or not dub_path.exists():
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Build dub audio failed (merge): {err[-1200:]}")
        for part in partials:
            part.unlink(missing_ok=True)
    if not _has_audible_audio(dub_path):
        raise RuntimeError("Built dub audio track is silent. Check TTS generation and subtitle text.")

    if task_id:
        await _set_dub_progress(task_id, "processing", 84, "Voice track ready")

    return dub_path


async def _build_dub_audio(
    cues: list[dict],
    work_dir: Path,
    duration: float,
    task_id: Optional[str] = None,
    voice_profiles: Optional[dict] = None,
    speaker_mode: str = "default",
    max_fit_speed: float = 1.3,
    min_gap_ms: int = 80,
    tts_provider: str = "vieneu",
    elevenlabs_api_key: str = "",
    elevenlabs_model_id: str = "eleven_multilingual_v2",
    fpt_api_key: str = "",
    fpt_speed: str = "0",
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    clip_paths = []
    usable_total = sum(1 for cue in cues if _clean_tts_text(cue.get("text") or ""))
    generated_count = 0
    for index, cue in enumerate(cues, start=1):
        text = _clean_tts_text(cue.get("text") or "")
        if not text:
            continue

        generated_count += 1
        if task_id:
            await _set_dub_progress(
                task_id,
                "processing",
                10 + int((generated_count - 1) / max(usable_total, 1) * 65),
                f"Generating voice {generated_count}/{usable_total}...",
            )

        raw_clip = work_dir / f"tts_{index:04d}.wav"
        fitted_clip = work_dir / f"tts_{index:04d}_fit.wav"
        profile_key, profile = _voice_profile_for_cue(index, cue, speaker_mode, voice_profiles)
        selected_engine = _tts_engine_for_provider(tts_provider)
        extra_kwargs = {}
        if _normalize_tts_provider(tts_provider) == "elevenlabs":
            extra_kwargs = {
                "api_key": elevenlabs_api_key,
                "model_id": elevenlabs_model_id,
            }
        elif _normalize_tts_provider(tts_provider) == "fpt":
            extra_kwargs = {
                "api_key": fpt_api_key,
                "speed": fpt_speed,
            }
        await selected_engine.synthesize_to_file(
            text,
            raw_clip,
            voice=profile.get("voice") or None,
            style=profile.get("style") or None,
            ref_audio=profile.get("ref_audio") or None,
            denoise=bool(profile.get("denoise", True)),
            **extra_kwargs,
        )
        if not raw_clip.exists() or raw_clip.stat().st_size == 0 or not _has_audible_audio(raw_clip):
            raise RuntimeError(
                "TTS generated an empty or silent audio clip. Check VieNeu-TTS voice/clone settings."
            )
        clip_path = await _fit_tts_clip_to_cue(
            raw_clip,
            fitted_clip,
            max(0.1, float(cue["end"]) - float(cue["start"])),
            max_fit_speed,
        )
        clip_duration = _probe_duration(clip_path)
        clip_paths.append((cue, clip_path, clip_duration))
        if task_id:
            voice_name = profile.get("voice") or profile_key
            await _set_dub_progress(
                task_id,
                "processing",
                10 + int(generated_count / max(usable_total, 1) * 65),
                f"Generated voice {generated_count}/{usable_total} ({voice_name})",
            )

    return await _mix_dub_clips(clip_paths, work_dir, duration, task_id=task_id, min_gap_ms=min_gap_ms)


async def _run_post_extract_pipeline(
    task_id: str,
    raw_script: str,
    video_title: str,
    source_ref: str,
    summary_language: str,
    request_summarizer: Summarizer,
    dedup_url: Optional[str] = None,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    media_path: Optional[str] = None,
) -> None:
    """取得 raw_script 后的共用管线：归档、优化、翻译、摘要、广播。"""
    short_id = task_id.replace("-", "")[:6]
    safe_title = _sanitize_title_for_filename(video_title)

    try:
        raw_md_filename = f"raw_{safe_title}_{short_id}.md"
        raw_md_path = TEMP_DIR / raw_md_filename
        with open(raw_md_path, "w", encoding="utf-8") as f:
            f.write((raw_script or "") + f"\n\nsource: {source_ref}\n")
        tasks[task_id].update({"raw_script_file": raw_md_filename})
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])
    except Exception as e:
        logger.error(f"保存原始转录Markdown失败: {e}")

    tasks[task_id].update({
        "progress": 55,
        "message": "Optimizing transcript...",
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, tasks[task_id])

    async def _optimize_progress(index: int, total: int) -> None:
        tasks[task_id].update({
            "progress": 55 + int(index / max(total, 1) * 12),
            "message": f"Optimizing transcript chunk {index}/{total}...",
            "updated_at": _now_ts(),
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

    script = await request_summarizer.optimize_transcript(raw_script, _optimize_progress)

    script_with_title = f"# {video_title}\n\n{script}\n\nsource: {source_ref}\n"

    detected_language = transcriber.get_detected_language(raw_script)
    detected_language = (detected_language or "").strip()
    if not detected_language:
        detected_language = translator.infer_language_code(raw_script)
    detected_language = translator.normalize_lang_code(detected_language) or detected_language

    logger.info(f"检测到的语言: {detected_language}, 摘要语言: {summary_language}")

    translation_content = None
    timed_translation_content = None
    translation_error = None
    translation_filename = None
    translation_path = None

    eff_key = (api_key or "").strip()
    eff_base = (model_base_url or "").strip().rstrip("/")
    if eff_key:
        request_translator = Translator(
            api_key=eff_key,
            base_url=eff_base or None,
            model=model_id or None,
        )
    else:
        request_translator = translator

    need_translation = translator.languages_differ_for_translation(
        detected_language, summary_language
    )

    if need_translation:
        logger.info(f"需要翻译: {detected_language} -> {summary_language}")
        tasks[task_id].update({
            "progress": 68,
            "message": "Identifying names and preparing translation...",
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

        name_glossary = await request_translator.build_name_glossary(
            raw_script,
            summary_language,
            detected_language,
        )
        if name_glossary:
            tasks[task_id]["translation_glossary"] = name_glossary
            save_tasks(tasks)

        speaker_profile = await request_translator.build_speaker_profile(
            raw_script,
            summary_language,
            detected_language,
            name_glossary,
        )
        if speaker_profile:
            tasks[task_id]["speaker_profile"] = speaker_profile
            save_tasks(tasks)

        async def _full_translation_progress(index: int, total: int) -> None:
            tasks[task_id].update({
                "progress": 70 + int(index / max(total, 1) * 18),
                "message": f"Translating transcript chunk {index}/{total}...",
                "updated_at": _now_ts(),
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

        translation_content = await request_translator.translate_text(
            script,
            summary_language,
            detected_language,
            name_glossary,
            speaker_profile,
            _full_translation_progress,
        )

        async def _timed_translation_progress(progress: int, message: str) -> None:
            scaled_progress = 88 + int((max(78, min(95, progress)) - 78) / 17 * 9)
            tasks[task_id].update({
                "progress": min(97, scaled_progress),
                "message": message,
                "updated_at": _now_ts(),
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

        timed_translation_content = await _translate_timed_transcript(
            request_translator,
            raw_script,
            summary_language,
            detected_language,
            name_glossary,
            speaker_profile,
            _timed_translation_progress,
        )
        full_translation_unchanged = _texts_equivalent(translation_content, script)
        timed_translation_unchanged = _timed_cue_texts_equivalent(timed_translation_content, raw_script)
        if full_translation_unchanged or timed_translation_unchanged:
            translation_error = "Translation failed or returned unchanged text. Check the model API key/base URL."
            logger.warning(f"翻译未生成有效译文: {translation_error}")
            translation_content = None
            timed_translation_content = None
        else:
            coverage_task = {
                "translation": translation_content,
                "timed_translation": timed_translation_content,
                "timed_transcript": raw_script,
                "script": script,
            }
            _, translated_cue_count, missing_cue_count = _dub_translation_cues_and_gap_count(coverage_task, 2.0)
            if translated_cue_count and missing_cue_count:
                translation_error = (
                    f"Translation is incomplete: {missing_cue_count} transcript segment(s) have no translated text."
                )
                logger.warning(f"翻译覆盖不完整: {translation_error}")
            translation_with_title = f"# {video_title}\n\n{translation_content}\n\nsource: {source_ref}\n"
            translation_filename = f"translation_{safe_title}_{short_id}.md"
            translation_path = TEMP_DIR / translation_filename
            async with aiofiles.open(translation_path, "w", encoding="utf-8") as f:
                await f.write(translation_with_title)
    else:
        logger.info(
            f"不需要翻译: detected_language={detected_language}, summary_language={summary_language}, "
            f"need_translation={need_translation}"
        )

    tasks[task_id].update({
        "progress": 80,
        "message": "Generating summary...",
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, tasks[task_id])

    summary = await request_summarizer.summarize(script, summary_language, video_title)
    summary_with_source = summary + f"\n\nsource: {source_ref}\n"

    script_filename = f"transcript_{task_id}.md"
    script_path = TEMP_DIR / script_filename
    async with aiofiles.open(script_path, "w", encoding="utf-8") as f:
        await f.write(script_with_title)

    new_script_filename = f"transcript_{safe_title}_{short_id}.md"
    new_script_path = TEMP_DIR / new_script_filename
    try:
        if script_path.exists():
            script_path.rename(new_script_path)
            script_path = new_script_path
    except Exception:
        pass

    summary_filename = f"summary_{safe_title}_{short_id}.md"
    summary_path = TEMP_DIR / summary_filename
    async with aiofiles.open(summary_path, "w", encoding="utf-8") as f:
        await f.write(summary_with_source)

    task_result = {
        "status": "completed",
        "progress": 100,
        "message": "处理完成！",
        "completed_at": _now_ts(),
        "video_title": video_title,
        "script": script_with_title,
        "summary": summary_with_source,
        "timed_transcript": raw_script,
        "script_path": str(script_path),
        "summary_path": str(summary_path),
        "short_id": short_id,
        "safe_title": safe_title,
        "detected_language": detected_language,
        "summary_language": summary_language,
    }
    if need_translation and tasks[task_id].get("translation_glossary"):
        task_result["translation_glossary"] = tasks[task_id].get("translation_glossary")
    if need_translation and tasks[task_id].get("speaker_profile"):
        task_result["speaker_profile"] = tasks[task_id].get("speaker_profile")

    if media_path:
        media_file = Path(media_path)
        if media_file.exists() and media_file.parent.resolve() == TEMP_DIR.resolve():
            task_result.update({
                "media_filename": media_file.name,
                "media_url": f"/api/media/{media_file.name}",
            })

    if translation_content and translation_path:
        task_result.update({
            "translation": translation_with_title,
            "timed_translation": timed_translation_content,
            "translation_path": str(translation_path),
            "translation_filename": translation_filename,
        })
    if translation_error:
        task_result.update({"translation_error": translation_error})

    tasks[task_id].update(task_result)
    save_tasks(tasks)
    logger.info(f"任务完成，准备广播最终状态: {task_id}")
    await broadcast_task_update(task_id, tasks[task_id])
    logger.info(f"最终状态已广播: {task_id}")

    if dedup_url:
        processing_urls.discard(dedup_url)
    if task_id in active_tasks:
        del active_tasks[task_id]


def _tool_version(command: str) -> dict:
    executable = shutil.which(command)
    if not executable:
        return {"available": False, "path": "", "version": ""}
    result = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    first_line = (result.stdout or result.stderr or "").splitlines()
    return {
        "available": result.returncode == 0,
        "path": executable,
        "version": first_line[0] if first_line else "",
    }


def _tesseract_languages() -> list[str]:
    if not TESSERACT_CMD:
        return []
    cmd = [str(TESSERACT_CMD)]
    if TESSDATA_DIR:
        cmd.extend(["--tessdata-dir", str(TESSDATA_DIR)])
    cmd.append("--list-langs")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []
    return sorted(
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip() and not line.lower().startswith("list of available")
    )


def _temp_is_writable() -> bool:
    probe = TEMP_DIR / f".health_{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="ascii")
        return probe.read_text(encoding="ascii") == "ok"
    except Exception:
        return False
    finally:
        probe.unlink(missing_ok=True)


@app.get("/api/health")
async def health_check():
    ffmpeg = _tool_version("ffmpeg")
    ffprobe = _tool_version("ffprobe")
    temp_writable = _temp_is_writable()
    tesseract_languages = _tesseract_languages()
    core_ready = ffmpeg["available"] and ffprobe["available"] and temp_writable
    return {
        "status": "ok" if core_ready else "degraded",
        "app_version": app.version,
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "temp": {"path": str(TEMP_DIR), "writable": temp_writable},
        "ocr": {
            "rapidocr": importlib.util.find_spec("rapidocr_onnxruntime") is not None,
            "tesseract": bool(TESSERACT_CMD),
            "tesseract_path": str(TESSERACT_CMD or ""),
            "languages": tesseract_languages,
        },
        "subtitle_font": {
            "family": SUBTITLE_FONT_FAMILY,
            "directory": str(SUBTITLE_FONTS_DIR or ""),
        },
        "whisper": transcriber.runtime_info(),
        "tts": {
            "vieneu": {"available": importlib.util.find_spec("vieneu") is not None},
            "elevenlabs": {"available": True, "configured": bool(os.getenv("ELEVENLABS_API_KEY"))},
            "fpt": {"available": True, "configured": bool(os.getenv("FPT_API_KEY"))},
            "puter": {"available": False},
        },
    }


@app.get("/")
async def read_root():
    """返回前端页面"""
    return FileResponse(str(PROJECT_ROOT / "static" / "index.html"))

@app.post("/api/models")
async def list_models(
    base_url: str = Form(default=""),
    api_key:  str = Form(default=""),
):
    """Proxy: fetch model list from any OpenAI-compatible API."""
    effective_key = api_key or os.getenv("OPENAI_API_KEY", "")
    validated_url = await _validate_model_base_url(base_url.strip())
    effective_url = validated_url or os.getenv("OPENAI_BASE_URL") or None

    if not effective_key:
        raise HTTPException(status_code=400, detail="API key is required")

    try:
        client = openai.OpenAI(api_key=effective_key, base_url=effective_url)
        resp   = await asyncio.to_thread(client.models.list)
        models = [{"id": m.id, "name": getattr(m, "name", m.id)} for m in resp.data]
        # Sort by id for readability
        models.sort(key=lambda x: x["id"])
        return {"data": models}
    except Exception as e:
        # Log the upstream detail; do not echo the response body back to the caller.
        logger.warning("列出模型失败 base_url=%s: %s", effective_url or "(default)", e)
        raise HTTPException(
            status_code=400,
            detail="Failed to list models from the configured endpoint. Check the API key and Base URL.",
        )


async def _enqueue_upload_job(
    file: UploadFile,
    summary_language: str,
    api_key: str,
    model_base_url: str,
    model_id: str,
    hard_subtitle_ocr: bool = False,
    ocr_language: str = "eng+vie+chi_sim",
    ocr_fps: float = 2.0,
    ocr_crop_top: int = 0,
    ocr_crop_bottom: int = 0,
    ocr_crop_left: int = 0,
    ocr_crop_right: int = 0,
    ocr_confidence: int = 45,
    settings: Optional[dict] = None,
) -> dict:
    """保存上传文件并入队 process_upload_task，返回 {task_id, message}。"""
    raw_name = file.filename or "upload.bin"
    if ".." in raw_name or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    safe_name = os.path.basename(raw_name)
    ext = Path(safe_name).suffix.lower()
    if ext not in UPLOAD_ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext or '(none)'}",
        )

    max_bytes = UPLOAD_MAX_MB * 1024 * 1024
    task_id = str(uuid.uuid4())
    unique_stem = task_id.replace("-", "")[:12]
    dest = TEMP_DIR / f"upload_{unique_stem}{ext}"

    total = 0
    with open(dest, "wb") as out_f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds limit of {UPLOAD_MAX_MB} MB",
                )
            out_f.write(chunk)

    if total == 0:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Empty file")

    video_title = _sanitize_title_for_filename(Path(safe_name).stem) or "upload"
    source_label = f"upload:{safe_name}"

    tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "message": "开始处理上传文件...",
        "created_at": _now_ts(),
        "script": None,
        "summary": None,
        "error": None,
        "url": source_label,
        "hard_subtitle_ocr": bool(hard_subtitle_ocr),
        "settings": _safe_task_settings(settings or {}),
    }
    save_tasks(tasks)

    bg = asyncio.create_task(
        process_upload_task(
            task_id,
            dest,
            safe_name,
            video_title,
            ext,
            summary_language,
            api_key,
            model_base_url,
            model_id,
            hard_subtitle_ocr,
            {
                "language": ocr_language,
                "fps": ocr_fps,
                "crop_top": ocr_crop_top,
                "crop_bottom": ocr_crop_bottom,
                "crop_left": ocr_crop_left,
                "crop_right": ocr_crop_right,
                "confidence": ocr_confidence,
            },
        )
    )
    active_tasks[task_id] = bg

    return {"task_id": task_id, "message": "任务已创建，正在处理中..."}


@app.post("/api/process-video")
async def process_video(
    url: str = Form(default=""),
    summary_language: str = Form(default="zh"),
    download_only: bool = Form(default=False),
    hard_subtitle_ocr: bool = Form(default=False),
    ocr_language: str = Form(default="eng+vie+chi_sim"),
    ocr_fps: float = Form(default=2.0),
    ocr_crop_top: int = Form(default=0),
    ocr_crop_bottom: int = Form(default=0),
    ocr_crop_left: int = Form(default=0),
    ocr_crop_right: int = Form(default=0),
    ocr_confidence: int = Form(default=45),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
    douyin_cookie: str = Form(default=""),
    bilibili_cookie: str = Form(default=""),
    douyin_cookie_file: Optional[UploadFile] = File(None),
    bilibili_cookie_file: Optional[UploadFile] = File(None),
    file: Optional[UploadFile] = File(None),
    settings_json: str = Form(default=""),
):
    """
    处理视频链接或本地上传（multipart 中带 file 且无有效 URL 时走上传流程）。
    上传与 URL 共用此路径，便于反向代理只放行 /api/process-video 的环境。
    """
    try:
        task_settings = _parse_task_settings(settings_json)
        ai_settings = task_settings.get("ai") if isinstance(task_settings.get("ai"), dict) else {}
        source_settings = task_settings.get("sources") if isinstance(task_settings.get("sources"), dict) else {}
        api_key = api_key or str(ai_settings.get("apiKey") or "").strip()
        model_base_url = model_base_url or str(ai_settings.get("baseUrl") or "").strip()
        model_id = model_id or str(ai_settings.get("model") or "").strip()
        douyin_cookie = douyin_cookie or str(source_settings.get("douyinCookie") or "").strip()
        bilibili_cookie = bilibili_cookie or str(source_settings.get("bilibiliCookie") or "").strip()

        if file is not None and (file.filename or "").strip():
            return await _enqueue_upload_job(
                file,
                summary_language,
                api_key,
                model_base_url,
                model_id,
                hard_subtitle_ocr,
                ocr_language,
                ocr_fps,
                ocr_crop_top,
                ocr_crop_bottom,
                ocr_crop_left,
                ocr_crop_right,
                ocr_confidence,
                task_settings,
            )

        stripped = (url or "").strip()
        if not stripped:
            raise HTTPException(
                status_code=400,
                detail="Provide a video URL or upload a file",
            )

        url = stripped
        if download_only:
            dedup_url = f"download_only:{url}"
        elif hard_subtitle_ocr:
            dedup_url = f"hard_subtitle_ocr:{url}"
        else:
            dedup_url = url

        # 检查是否已经在处理相同的URL
        if dedup_url in processing_urls:
            # 查找现有任务
            for tid, task in tasks.items():
                if (task.get("dedup_url") or task.get("url")) == dedup_url:
                    return {"task_id": tid, "message": "该视频正在处理中，请等待..."}
            
        # 生成唯一任务ID
        task_id = str(uuid.uuid4())
        
        # 标记URL为正在处理
        processing_urls.add(dedup_url)
        
        # 初始化任务状态
        tasks[task_id] = {
            "status": "processing",
            "progress": 0,
            "message": "开始下载视频..." if download_only else "开始处理视频...",
            "created_at": _now_ts(),
            "script": None,
            "summary": None,
            "error": None,
            "url": url,
            "dedup_url": dedup_url,
            "download_only": bool(download_only),
            "hard_subtitle_ocr": bool(hard_subtitle_ocr),
            "settings": task_settings,
        }
        save_tasks(tasks)
        
        # 创建并跟踪异步任务
        cookie_file_dir = TEMP_DIR / f"cookie_files_{task_id.replace('-', '')[:12]}"
        douyin_cookie_file_path = await _save_cookie_file_upload(douyin_cookie_file, cookie_file_dir, "douyin")
        bilibili_cookie_file_path = await _save_cookie_file_upload(bilibili_cookie_file, cookie_file_dir, "bilibili")
        platform_cookies = {
            "douyin": douyin_cookie.strip(),
            "bilibili": bilibili_cookie.strip(),
            "douyin_cookie_file": douyin_cookie_file_path or "",
            "bilibili_cookie_file": bilibili_cookie_file_path or "",
        }
        platform_cookie_status = {
            "douyin": video_processor.cookie_diagnostics("douyin", platform_cookies),
            "bilibili": video_processor.cookie_diagnostics("bilibili", platform_cookies),
        }
        tasks[task_id]["platform_cookie_status"] = platform_cookie_status
        logger.info(
            "平台 Cookie 状态: douyin=%s, bilibili=%s",
            platform_cookie_status["douyin"],
            platform_cookie_status["bilibili"],
        )
        save_tasks(tasks)
        if download_only:
            worker = download_only_task
            worker_args = (
                task_id,
                url,
                summary_language,
                api_key,
                model_base_url,
                model_id,
                platform_cookies,
                dedup_url,
            )
        elif hard_subtitle_ocr:
            worker = hard_subtitle_ocr_task
            worker_args = (
                task_id,
                url,
                summary_language,
                api_key,
                model_base_url,
                model_id,
                platform_cookies,
                dedup_url,
                {
                    "language": ocr_language,
                    "fps": ocr_fps,
                    "crop_top": ocr_crop_top,
                    "crop_bottom": ocr_crop_bottom,
                    "crop_left": ocr_crop_left,
                    "crop_right": ocr_crop_right,
                    "confidence": ocr_confidence,
                },
            )
        else:
            worker = process_video_task
            worker_args = (
                task_id,
                url,
                summary_language,
                api_key,
                model_base_url,
                model_id,
                platform_cookies,
                dedup_url,
            )
        task = asyncio.create_task(
            worker(*worker_args)
        )
        active_tasks[task_id] = task
        
        return {"task_id": task_id, "message": "任务已创建，正在处理中..."}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理视频时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")

async def download_only_task(
    task_id: str,
    url: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    platform_cookies: Optional[dict] = None,
    dedup_url: Optional[str] = None,
):
    """Download source video only; do not fetch subtitles, transcribe, translate, or summarize."""
    try:
        tasks[task_id].update({
            "status": "processing",
            "progress": 10,
            "message": "正在下载视频...",
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

        video_title, source_media_path = await video_processor.download_source_video_only(
            url,
            TEMP_DIR,
            platform_cookies=platform_cookies,
        )

        media_file = Path(source_media_path)
        if not media_file.exists() or media_file.parent.resolve() != TEMP_DIR.resolve():
            raise Exception("Downloaded video file is not available")

        tasks[task_id].update({
            "status": "completed",
            "progress": 100,
            "message": "Video downloaded",
            "completed_at": _now_ts(),
            "video_title": video_title,
            "script": "",
            "summary": "",
            "translation": "",
            "timed_transcript": "",
            "timed_translation": "",
            "detected_language": "",
            "summary_language": summary_language,
            "media_filename": media_file.name,
            "media_url": f"/api/media/{media_file.name}",
            "download_only": True,
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

    except Exception as e:
        logger.error(f"下载任务 {task_id} 失败: {str(e)}")
        tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "message": f"下载失败: {str(e)}",
        })
        save_tasks(tasks, force=True)
        await broadcast_task_update(task_id, tasks[task_id])
    finally:
        processing_urls.discard(dedup_url or url)
        if task_id in active_tasks:
            del active_tasks[task_id]

async def hard_subtitle_ocr_task(
    task_id: str,
    url: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    platform_cookies: Optional[dict] = None,
    dedup_url: Optional[str] = None,
    ocr_settings: Optional[dict] = None,
):
    """Download source video, OCR burned-in subtitles, then run the normal text pipeline."""
    try:
        settings = ocr_settings or {}
        tasks[task_id].update({
            "status": "processing",
            "progress": 10,
            "message": "Downloading video for hard-subtitle OCR...",
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

        video_title, source_media_path = await video_processor.download_source_video_only(
            url,
            TEMP_DIR,
            platform_cookies=platform_cookies,
        )
        media_file = Path(source_media_path)
        if not media_file.exists() or media_file.parent.resolve() != TEMP_DIR.resolve():
            raise Exception("Downloaded video file is not available")

        tasks[task_id].update({
            "progress": 35,
            "message": "Extracting subtitle frames for OCR...",
            "media_filename": media_file.name,
            "media_url": f"/api/media/{media_file.name}",
            "ocr_settings": settings,
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

        raw_script = await _extract_hard_subtitle_ocr(
            task_id,
            media_file,
            language=settings.get("language") or "eng+vie+chi_sim",
            fps=settings.get("fps") or 2.0,
            crop_top=settings.get("crop_top") if settings.get("crop_top") is not None else 0,
            crop_bottom=settings.get("crop_bottom") if settings.get("crop_bottom") is not None else 0,
            crop_left=settings.get("crop_left") if settings.get("crop_left") is not None else 0,
            crop_right=settings.get("crop_right") if settings.get("crop_right") is not None else 0,
            confidence=settings.get("confidence") if settings.get("confidence") is not None else 45,
        )

        if api_key:
            effective_url = model_base_url.rstrip("/") or None
            request_summarizer = Summarizer(
                api_key=api_key,
                base_url=effective_url,
                model=model_id or None,
            )
        else:
            request_summarizer = summarizer

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=video_title,
            source_ref=url,
            summary_language=summary_language,
            request_summarizer=request_summarizer,
            dedup_url=dedup_url or url,
            api_key=api_key,
            model_base_url=model_base_url,
            model_id=model_id,
            media_path=str(media_file),
        )

    except Exception as e:
        logger.error(f"OCR task {task_id} failed: {str(e)}")
        processing_urls.discard(dedup_url or url)
        if task_id in active_tasks:
            del active_tasks[task_id]
        tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "message": f"OCR failed: {str(e)}",
        })
        save_tasks(tasks, force=True)
        await broadcast_task_update(task_id, tasks[task_id])

async def existing_media_hard_subtitle_ocr_task(
    task_id: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    ocr_settings: Optional[dict] = None,
):
    """Run hard-subtitle OCR on a task's already downloaded/uploaded media."""
    try:
        task = tasks.get(task_id)
        if not task:
            raise Exception("Task not found")
        media_filename = task.get("media_filename")
        media_file = _safe_temp_path(media_filename or "")
        if not media_file or not media_file.exists() or not media_file.is_file():
            raise Exception("Downloaded video file is not available for OCR")
        if media_file.suffix.lower() not in {".mp4", ".webm", ".mkv", ".mov"}:
            raise Exception("Hard-subtitle OCR requires a video file")

        settings = ocr_settings or {}
        task.update({
            "status": "processing",
            "progress": 25,
            "message": "Extracting subtitle frames for OCR...",
            "error": None,
            "hard_subtitle_ocr": True,
            "download_only": False,
            "ocr_settings": settings,
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, task)

        raw_script = await _extract_hard_subtitle_ocr(
            task_id,
            media_file,
            language=settings.get("language") or "eng+vie+chi_sim",
            fps=settings.get("fps") or 2.0,
            crop_top=settings.get("crop_top") if settings.get("crop_top") is not None else 0,
            crop_bottom=settings.get("crop_bottom") if settings.get("crop_bottom") is not None else 0,
            crop_left=settings.get("crop_left") if settings.get("crop_left") is not None else 0,
            crop_right=settings.get("crop_right") if settings.get("crop_right") is not None else 0,
            confidence=settings.get("confidence") if settings.get("confidence") is not None else 45,
        )

        if api_key:
            effective_url = model_base_url.rstrip("/") or None
            request_summarizer = Summarizer(
                api_key=api_key,
                base_url=effective_url,
                model=model_id or None,
            )
        else:
            request_summarizer = summarizer

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=task.get("video_title") or media_file.stem,
            source_ref=task.get("url") or f"media:{media_file.name}",
            summary_language=summary_language,
            request_summarizer=request_summarizer,
            dedup_url=None,
            api_key=api_key,
            model_base_url=model_base_url,
            model_id=model_id,
            media_path=str(media_file),
        )

    except Exception as e:
        logger.error(f"Existing media OCR task {task_id} failed: {str(e)}")
        if task_id in active_tasks:
            del active_tasks[task_id]
        if task_id in tasks:
            tasks[task_id].update({
                "status": "error",
                "error": str(e),
                "message": f"OCR failed: {str(e)}",
            })
            save_tasks(tasks, force=True)
            await broadcast_task_update(task_id, tasks[task_id])

async def process_video_task(
    task_id: str,
    url: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    platform_cookies: Optional[dict] = None,
    dedup_url: Optional[str] = None,
):
    """
    异步处理视频任务
    """
    try:
        audio_path = None
        source_media_path = None
        # ── 阶段一：优先尝试获取平台字幕（快速路径） ──────────────────────
        tasks[task_id].update({
            "status": "processing",
            "progress": 10,
            "message": "正在检测视频字幕..."
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])
        await asyncio.sleep(0.1)

        # 如果前端传入了 API 凭据，创建专用 Summarizer（线程安全，覆盖全局实例）
        if api_key:
            effective_url = model_base_url.rstrip("/") or None
            request_summarizer = Summarizer(
                api_key=api_key,
                base_url=effective_url,
                model=model_id or None,
            )
            logger.info(f"使用前端提供的 API Key，base_url={effective_url}, model={model_id or 'default'}")
        else:
            request_summarizer = summarizer  # 全局实例（使用环境变量）

        subtitle_text, sub_title, sub_lang = await video_processor.fetch_subtitles(
            url,
            TEMP_DIR,
            platform_cookies=platform_cookies,
        )

        if subtitle_text:
            # ── 快速路径：有字幕，跳过音频下载和 Whisper ──────────────────
            video_title = sub_title
            raw_script = subtitle_text
            # 把语言写入 transcriber，保持下游逻辑一致
            transcriber.last_detected_language = sub_lang

            tasks[task_id].update({
                "progress": 40,
                "message": f"字幕获取成功（{sub_lang}），正在处理文本..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            try:
                tasks[task_id].update({
                    "progress": 42,
                    "message": "正在下载原视频用于导出...",
                })
                save_tasks(tasks)
                await broadcast_task_update(task_id, tasks[task_id])
                audio_path, _, source_media_path = await video_processor.download_and_convert(
                    url,
                    TEMP_DIR,
                    prefetched_title=sub_title or None,
                    platform_cookies=platform_cookies,
                )
            except Exception as e:
                logger.warning(f"下载原视频失败，继续生成文本结果: {e}")
        else:
            # ── 慢速路径：无字幕，下载音频 → Whisper 转录 ─────────────────
            tasks[task_id].update({
                "progress": 15,
                "message": "未找到字幕，正在下载视频音频..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            audio_path, video_title, source_media_path = await video_processor.download_and_convert(
                url,
                TEMP_DIR,
                prefetched_title=sub_title or None,
                platform_cookies=platform_cookies,
            )

            tasks[task_id].update({
                "progress": 35,
                "message": "音频下载完成，准备转录..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            tasks[task_id].update({
                "progress": 40,
                "message": "正在转录音频（Whisper）..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            raw_script = await transcriber.transcribe(audio_path)

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=video_title,
            source_ref=url,
            summary_language=summary_language,
            request_summarizer=request_summarizer,
            dedup_url=dedup_url or url,
            api_key=api_key,
            model_base_url=model_base_url,
            model_id=model_id,
            media_path=source_media_path or audio_path,
        )

        # 不要立即删除临时文件！保留给用户下载
        # 文件会在一定时间后自动清理或用户手动清理

    except Exception as e:
        logger.error(f"任务 {task_id} 处理失败: {str(e)}")
        # 从处理列表中移除URL
        processing_urls.discard(dedup_url or url)
        
        # 从活跃任务列表中移除
        if task_id in active_tasks:
            del active_tasks[task_id]
            
        tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "message": f"处理失败: {str(e)}"
        })
        save_tasks(tasks, force=True)
        await broadcast_task_update(task_id, tasks[task_id])

async def resume_post_extract_task(
    task_id: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
):
    """Resume a restarted task from its saved raw timed transcript."""
    try:
        task = tasks.get(task_id)
        if not task:
            raise Exception("Task not found")
        raw_path = _safe_temp_path(task.get("raw_script_file") or "")
        if not raw_path or not raw_path.exists() or not raw_path.is_file():
            raise Exception("No saved transcript is available to resume")

        raw_script = raw_path.read_text(encoding="utf-8", errors="replace")
        if not raw_script.strip():
            raise Exception("Saved transcript is empty")

        if api_key:
            request_summarizer = Summarizer(
                api_key=api_key,
                base_url=model_base_url.rstrip("/") or None,
                model=model_id or None,
            )
        else:
            request_summarizer = summarizer

        media_path = None
        media_file = _safe_temp_path(task.get("media_filename") or "")
        if media_file and media_file.exists() and media_file.is_file():
            media_path = str(media_file)

        task.update({
            "status": "processing",
            "progress": max(55, int(task.get("progress") or 0)),
            "message": "Resuming from saved transcript...",
            "error": None,
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, task)

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=task.get("video_title") or task.get("custom_title") or "Untitled",
            source_ref=task.get("url") or "",
            summary_language=summary_language or task.get("summary_language") or "zh",
            request_summarizer=request_summarizer,
            dedup_url=None,
            api_key=api_key,
            model_base_url=model_base_url,
            model_id=model_id,
            media_path=media_path,
        )
    except Exception as e:
        logger.error(f"Resume task {task_id} failed: {str(e)}")
        if task_id in active_tasks:
            del active_tasks[task_id]
        if task_id in tasks:
            tasks[task_id].update({
                "status": "error",
                "error": str(e),
                "message": f"Resume failed: {str(e)}",
            })
            save_tasks(tasks, force=True)
            await broadcast_task_update(task_id, tasks[task_id])

@app.post("/api/process-upload")
async def process_upload(
    file: UploadFile = File(...),
    summary_language: str = Form(default="zh"),
    hard_subtitle_ocr: bool = Form(default=False),
    ocr_language: str = Form(default="eng+vie+chi_sim"),
    ocr_fps: float = Form(default=2.0),
    ocr_crop_top: int = Form(default=0),
    ocr_crop_bottom: int = Form(default=0),
    ocr_crop_left: int = Form(default=0),
    ocr_crop_right: int = Form(default=0),
    ocr_confidence: int = Form(default=45),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
    settings_json: str = Form(default=""),
):
    """独立上传入口；逻辑与 multipart 带 file 的 /api/process-video 相同。"""
    task_settings = _parse_task_settings(settings_json)
    ai_settings = task_settings.get("ai") if isinstance(task_settings.get("ai"), dict) else {}
    api_key = api_key or str(ai_settings.get("apiKey") or "").strip()
    model_base_url = model_base_url or str(ai_settings.get("baseUrl") or "").strip()
    model_id = model_id or str(ai_settings.get("model") or "").strip()
    return await _enqueue_upload_job(
        file,
        summary_language,
        api_key,
        model_base_url,
        model_id,
        hard_subtitle_ocr,
        ocr_language,
        ocr_fps,
        ocr_crop_top,
        ocr_crop_bottom,
        ocr_crop_left,
        ocr_crop_right,
        ocr_confidence,
        task_settings,
    )


async def process_upload_task(
    task_id: str,
    saved_path: Path,
    original_name: str,
    video_title: str,
    ext_lower: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    hard_subtitle_ocr: bool = False,
    ocr_settings: Optional[dict] = None,
):
    source_ref = f"upload:{original_name}"
    try:
        if api_key:
            effective_url = model_base_url.rstrip("/") or None
            request_summarizer = Summarizer(
                api_key=api_key,
                base_url=effective_url,
                model=model_id or None,
            )
            logger.info(
                f"上传任务使用前端 API Key，base_url={effective_url}, model={model_id or 'default'}"
            )
        else:
            request_summarizer = summarizer

        if hard_subtitle_ocr and ext_lower not in {".mp4", ".webm", ".mkv"}:
            raise Exception("Hard-subtitle OCR requires a video upload")

        if hard_subtitle_ocr:
            settings = ocr_settings or {}
            tasks[task_id].update({
                "progress": 35,
                "message": "Extracting subtitle frames for OCR...",
                "media_filename": saved_path.name,
                "media_url": f"/api/media/{saved_path.name}",
                "ocr_settings": settings,
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            raw_script = await _extract_hard_subtitle_ocr(
                task_id,
                saved_path,
                language=settings.get("language") or "eng+vie+chi_sim",
                fps=settings.get("fps") or 2.0,
                crop_top=settings.get("crop_top") if settings.get("crop_top") is not None else 0,
                crop_bottom=settings.get("crop_bottom") if settings.get("crop_bottom") is not None else 0,
                crop_left=settings.get("crop_left") if settings.get("crop_left") is not None else 0,
                crop_right=settings.get("crop_right") if settings.get("crop_right") is not None else 0,
                confidence=settings.get("confidence") if settings.get("confidence") is not None else 45,
            )
            audio_path = None
        elif ext_lower == ".txt":
            tasks[task_id].update({
                "progress": 20,
                "message": "正在读取文本文件...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            body = saved_path.read_text(encoding="utf-8", errors="replace")
            if not body.strip():
                raise Exception("文本文件为空")
            transcriber.last_detected_language = None
            raw_script = _txt_to_raw_transcript_markdown(body)
        else:
            tasks[task_id].update({
                "progress": 15,
                "message": "正在转换音频格式...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            audio_path = await video_processor.normalize_local_media_to_m4a(saved_path, TEMP_DIR)

            tasks[task_id].update({
                "progress": 35,
                "message": "音频准备完成，准备转录...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            tasks[task_id].update({
                "progress": 40,
                "message": "正在转录音频（Whisper）...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            raw_script = await transcriber.transcribe(audio_path)

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=video_title,
            source_ref=source_ref,
            summary_language=summary_language,
            request_summarizer=request_summarizer,
            dedup_url=None,
            api_key=api_key,
            model_base_url=model_base_url,
            model_id=model_id,
            media_path=str(saved_path) if ext_lower in {".mp4", ".webm", ".mkv"} else (audio_path if ext_lower != ".txt" else None),
        )

    except Exception as e:
        logger.error(f"任务 {task_id} 处理失败: {str(e)}")
        if task_id in active_tasks:
            del active_tasks[task_id]
        tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "message": f"处理失败: {str(e)}",
        })
        save_tasks(tasks, force=True)
        await broadcast_task_update(task_id, tasks[task_id])


@app.get("/api/task-status/{task_id}")
async def get_task_status(task_id: str):
    """
    获取任务状态
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return tasks[task_id]


@app.get("/api/task-settings/{task_id}")
async def get_task_settings(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task_id": task_id, "settings": _task_saved_settings(tasks[task_id])}


@app.put("/api/task-settings/{task_id}")
async def save_task_settings(task_id: str, settings: dict = Body(default={})):
    saved = _store_task_settings(task_id, settings)
    return {"task_id": task_id, "settings": saved}


@app.post("/api/resume-task/{task_id}")
async def resume_task(
    task_id: str,
    summary_language: str = Form(default=""),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
    douyin_cookie: str = Form(default=""),
    bilibili_cookie: str = Form(default=""),
    settings_json: str = Form(default=""),
):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task_id in active_tasks:
        raise HTTPException(status_code=409, detail="Task is already processing")

    task = tasks[task_id]
    if task.get("status") == "completed":
        return task

    if settings_json:
        # _store_task_settings redacts secrets before persisting; only the
        # non-secret parts of settings_json survive.
        _store_task_settings(task_id, _parse_task_settings(settings_json))
    # Secrets are stripped from stored settings on every save, so they can only
    # come from this request. Non-secret settings still fall back to the record.
    model_base_url = model_base_url or _task_ai_setting(task, "baseUrl")
    model_id = model_id or _task_ai_setting(task, "model")
    task_summary_language = summary_language or _task_ai_setting(task, "summaryLanguage") or task.get("summary_language") or "zh"
    raw_path = _safe_temp_path(task.get("raw_script_file") or "")
    media_file = _safe_temp_path(task.get("media_filename") or "")

    if raw_path and raw_path.exists() and raw_path.is_file():
        worker = resume_post_extract_task
        worker_args = (task_id, task_summary_language, api_key, model_base_url, model_id)
        message = "Resume queued from saved transcript"
    elif task.get("hard_subtitle_ocr") and media_file and media_file.exists() and media_file.is_file():
        worker = existing_media_hard_subtitle_ocr_task
        worker_args = (
            task_id,
            task_summary_language,
            api_key,
            model_base_url,
            model_id,
            task.get("ocr_settings") or {},
        )
        message = "Resume queued from downloaded media"
    elif task.get("url"):
        url = task.get("url")
        platform_cookies = {
            "douyin": douyin_cookie.strip(),
            "bilibili": bilibili_cookie.strip(),
        }
        if task.get("download_only"):
            dedup_url = f"download_only:{url}"
            worker = download_only_task
            worker_args = (
                task_id,
                url,
                task_summary_language,
                api_key,
                model_base_url,
                model_id,
                platform_cookies,
                dedup_url,
            )
        elif task.get("hard_subtitle_ocr"):
            dedup_url = f"hard_subtitle_ocr:{url}"
            worker = hard_subtitle_ocr_task
            worker_args = (
                task_id,
                url,
                task_summary_language,
                api_key,
                model_base_url,
                model_id,
                platform_cookies,
                dedup_url,
                task.get("ocr_settings") or {},
            )
        else:
            dedup_url = url
            worker = process_video_task
            worker_args = (
                task_id,
                url,
                task_summary_language,
                api_key,
                model_base_url,
                model_id,
                platform_cookies,
                dedup_url,
            )
        processing_urls.add(dedup_url)
        message = "Resume queued from source URL"
    else:
        raise HTTPException(status_code=400, detail="Task has no saved transcript, media, or source URL to resume")

    task.update({
        "status": "processing",
        "progress": max(1, int(task.get("progress") or 1)),
        "message": message,
        "error": None,
        "summary_language": task_summary_language,
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, task)

    bg = asyncio.create_task(worker(*worker_args))
    active_tasks[task_id] = bg
    return task

@app.post("/api/hard-subtitle-ocr/{task_id}")
async def run_hard_subtitle_ocr(
    task_id: str,
    summary_language: str = Form(default="zh"),
    ocr_language: str = Form(default="eng+vie+chi_sim"),
    ocr_fps: float = Form(default=2.0),
    ocr_crop_top: int = Form(default=0),
    ocr_crop_bottom: int = Form(default=0),
    ocr_crop_left: int = Form(default=0),
    ocr_crop_right: int = Form(default=0),
    ocr_confidence: int = Form(default=45),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task_id in active_tasks:
        raise HTTPException(status_code=409, detail="Task is already processing")

    task = tasks[task_id]
    media_filename = task.get("media_filename")
    media_file = _safe_temp_path(media_filename or "")
    if not media_file or not media_file.exists():
        raise HTTPException(status_code=400, detail="No downloaded video is available for OCR")

    ocr_settings = {
        "language": ocr_language,
        "fps": ocr_fps,
        "crop_top": ocr_crop_top,
        "crop_bottom": ocr_crop_bottom,
        "crop_left": ocr_crop_left,
        "crop_right": ocr_crop_right,
        "confidence": ocr_confidence,
    }
    task.update({
        "status": "processing",
        "progress": 5,
        "message": "Preparing hard-subtitle OCR...",
        "error": None,
        "ocr_settings": ocr_settings,
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, task)

    bg = asyncio.create_task(
        existing_media_hard_subtitle_ocr_task(
            task_id,
            summary_language,
            api_key,
            model_base_url,
            model_id,
            ocr_settings,
        )
    )
    active_tasks[task_id] = bg
    return task


@app.post("/api/update-cue-text/{task_id}")
async def update_cue_text(
    task_id: str,
    source: str = Form(default="translation"),
    start: float = Form(...),
    end: float = Form(...),
    text: str = Form(default=""),
):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    selected = (source or "translation").lower()
    if selected == "translation":
        key = "timed_translation"
        fallback_key = "translation"
    elif selected in {"transcript", "original"}:
        key = "timed_transcript"
        fallback_key = "script"
    else:
        raise HTTPException(status_code=400, detail="Invalid subtitle source")

    markdown = task.get(key) or task.get(fallback_key) or ""
    updated_markdown, changed = _replace_cue_text(markdown, float(start), float(end), text)
    if not changed:
        raise HTTPException(status_code=404, detail="Cue not found")

    task[key] = updated_markdown
    for cache_key in ("export_video_filename", "export_video_url", "dubbed_video_filename", "dubbed_video_url"):
        task.pop(cache_key, None)
    task["updated_at"] = _now_ts()
    save_tasks(tasks)
    await broadcast_task_update(task_id, task)
    return {
        "task_id": task_id,
        "source": selected,
        "start": start,
        "end": end,
        "text": text.strip(),
        "timed_transcript": task.get("timed_transcript"),
        "timed_translation": task.get("timed_translation"),
    }


async def _run_background_task(task_id: str, job) -> None:
    try:
        await job
    except asyncio.CancelledError:
        logger.warning("Background task %s was cancelled", task_id)
        if task_id in tasks:
            tasks[task_id].update({
                "status": "error",
                "error": "Task was cancelled before it finished. Click Resume task or run the step again.",
                "message": "Task was cancelled before it finished. Click Resume task or run the step again.",
                "updated_at": _now_ts(),
            })
            tasks[task_id].pop("completed_at", None)
            save_tasks(tasks, force=True)
            await broadcast_task_update(task_id, tasks[task_id])
        raise
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, str) else str(e.detail)
        logger.error(f"Background task {task_id} failed: {detail}")
        if task_id in tasks:
            tasks[task_id].update({
                "status": "error",
                "error": detail,
                "message": detail,
                "updated_at": _now_ts(),
            })
            tasks[task_id].pop("completed_at", None)
            save_tasks(tasks, force=True)
            await broadcast_task_update(task_id, tasks[task_id])
    except Exception as e:
        logger.error(f"Background task {task_id} failed: {str(e)}")
        if task_id in tasks:
            tasks[task_id].update({
                "status": "error",
                "error": str(e),
                "message": str(e),
                "updated_at": _now_ts(),
            })
            tasks[task_id].pop("completed_at", None)
            save_tasks(tasks, force=True)
            await broadcast_task_update(task_id, tasks[task_id])
    finally:
        current = asyncio.current_task()
        if active_tasks.get(task_id) is current:
            active_tasks.pop(task_id, None)


@app.post("/api/regenerate-translation/{task_id}")
async def regenerate_translation(
    task_id: str,
    summary_language: str = Form(default=""),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task_id in active_tasks:
        raise HTTPException(status_code=409, detail="Task is already processing")
    task = tasks[task_id]
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    # api_key must come from this request: stored settings are redacted on save.
    model_base_url = model_base_url or _task_ai_setting(task, "baseUrl")
    model_id = model_id or _task_ai_setting(task, "model")
    summary_language = summary_language or _task_ai_setting(task, "summaryLanguage") or task.get("summary_language") or ""
    task.update({
        "status": "processing",
        "progress": 70,
        "message": "Regenerating translation...",
        "error": None,
        "updated_at": _now_ts(),
    })
    task.pop("completed_at", None)
    save_tasks(tasks, force=True)
    await broadcast_task_update(task_id, task)
    bg = asyncio.create_task(_run_background_task(
        task_id,
        _regenerate_translation_job(task_id, summary_language, api_key, model_base_url, model_id),
    ))
    active_tasks[task_id] = bg
    return task


async def _regenerate_translation_job(
    task_id: str,
    summary_language: str = Form(default=""),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task.get("status") not in {"completed", "processing"}:
        raise HTTPException(status_code=400, detail="任务尚未完成")

    raw_script = task.get("timed_transcript") or task.get("script") or ""
    if not raw_script.strip():
        raise HTTPException(status_code=400, detail="任务没有可用于重新翻译的转录文本")

    target_language = (summary_language or task.get("summary_language") or "").strip()
    detected_language = (task.get("detected_language") or "").strip()
    if not detected_language:
        detected_language = translator.infer_language_code(raw_script)
    detected_language = translator.normalize_lang_code(detected_language) or detected_language
    if not target_language:
        raise HTTPException(status_code=400, detail="Missing target translation language")

    if not translator.languages_differ_for_translation(detected_language, target_language):
        raise HTTPException(status_code=400, detail="Source and target language are the same; no translation is needed")

    await _set_dub_progress(task_id, "processing", 1, "Regenerating translation...")
    tasks[task_id].update({
        "progress": 70,
        "message": "Regenerating translation...",
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, tasks[task_id])

    eff_key = (api_key or "").strip()
    eff_base = (model_base_url or "").strip().rstrip("/")
    request_translator = Translator(
        api_key=eff_key,
        base_url=eff_base or None,
        model=model_id or None,
    ) if eff_key else translator

    script_source = task.get("script") or raw_script
    name_glossary = task.get("translation_glossary")
    if not isinstance(name_glossary, list) or not name_glossary:
        tasks[task_id].update({
            "progress": 71,
            "message": "Identifying names for translation...",
            "updated_at": _now_ts(),
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])
        name_glossary = await request_translator.build_name_glossary(
            raw_script,
            target_language,
            detected_language,
        )
    speaker_profile = task.get("speaker_profile")
    if not isinstance(speaker_profile, dict) or not speaker_profile:
        tasks[task_id].update({
            "progress": 73,
            "message": "Building speaker/pronoun profile...",
            "updated_at": _now_ts(),
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])
        speaker_profile = await request_translator.build_speaker_profile(
            raw_script,
            target_language,
            detected_language,
            name_glossary,
        )
    tasks[task_id].update({
        "progress": 70,
        "message": "Translating full transcript...",
        "updated_at": _now_ts(),
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, tasks[task_id])

    async def _full_translation_progress(index: int, total: int) -> None:
        current = tasks.get(task_id)
        if not current or current.get("status") != "processing":
            return
        current.update({
            "progress": 70 + int(index / max(total, 1) * 18),
            "message": f"Translating transcript chunk {index}/{total}...",
            "updated_at": _now_ts(),
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, current)

    translation_content = await request_translator.translate_text(
        script_source,
        target_language,
        detected_language,
        name_glossary,
        speaker_profile,
        _full_translation_progress,
    )

    async def _translation_progress(progress: int, message: str) -> None:
        current = tasks.get(task_id)
        if not current or current.get("status") != "processing":
            return
        current.update({
            "progress": progress,
            "message": message,
            "updated_at": _now_ts(),
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, current)

    async def _timed_translation_progress(progress: int, message: str) -> None:
        scaled_progress = 88 + int((max(78, min(95, progress)) - 78) / 17 * 9)
        await _translation_progress(min(97, scaled_progress), message)

    await _translation_progress(88, "Translating timed subtitles...")
    timed_translation_content = await _translate_timed_transcript(
        request_translator,
        raw_script,
        target_language,
        detected_language,
        name_glossary,
        speaker_profile,
        _timed_translation_progress,
    )
    await _translation_progress(98, "Validating translation coverage...")

    full_translation_unchanged = _texts_equivalent(translation_content, script_source)
    timed_translation_unchanged = _timed_cue_texts_equivalent(timed_translation_content, raw_script)
    if full_translation_unchanged or timed_translation_unchanged:
        detail = "Translation failed or returned unchanged text. Check the model API key/base URL."
        await _set_dub_progress(task_id, "error", 0, detail)
        raise HTTPException(status_code=400, detail=detail)

    coverage_task = {
        "translation": translation_content,
        "timed_translation": timed_translation_content,
        "timed_transcript": raw_script,
        "script": script_source,
    }
    _, translated_cue_count, missing_cue_count = _dub_translation_cues_and_gap_count(coverage_task, 2.0)
    if missing_cue_count:
        detail = f"Translation is incomplete: {missing_cue_count} transcript segment(s) have no translated text."
        tasks[task_id].update({"translation_error": detail})
        save_tasks(tasks)
        await _set_dub_progress(task_id, "error", 0, detail)
        raise HTTPException(status_code=400, detail=detail)

    safe_title = task.get("safe_title") or _sanitize_title_for_filename(task.get("video_title") or "video")
    short_id = task.get("short_id") or task_id.replace("-", "")[:6]
    source_ref = task.get("url") or task.get("video_title") or "source"
    translation_with_title = f"# {task.get('video_title') or safe_title}\n\n{translation_content}\n\nsource: {source_ref}\n"
    translation_filename = f"translation_{safe_title}_{short_id}.md"
    translation_path = TEMP_DIR / translation_filename
    async with aiofiles.open(translation_path, "w", encoding="utf-8") as f:
        await f.write(translation_with_title)

    task.update({
        "status": "completed",
        "progress": 100,
        "translation": translation_with_title,
        "timed_translation": timed_translation_content,
        "translation_path": str(translation_path),
        "translation_filename": translation_filename,
        "translation_error": None,
        "translation_glossary": name_glossary,
        "speaker_profile": speaker_profile,
        "summary_language": target_language,
        "detected_language": detected_language,
        "message": "翻译已重新生成",
        "updated_at": _now_ts(),
        "completed_at": _now_ts(),
    })
    for key in ("dubbed_video_filename", "dubbed_video_url"):
        task.pop(key, None)
    await _set_dub_progress(task_id, "completed", 100, "Translation regenerated")
    save_tasks(tasks, force=True)
    await broadcast_task_update(task_id, task)
    return task


@app.post("/api/regenerate-transcript/{task_id}")
async def regenerate_transcript(
    task_id: str,
    summary_language: str = Form(default=""),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task_id in active_tasks:
        raise HTTPException(status_code=409, detail="Task is already processing")
    task = tasks[task_id]
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    # api_key must come from this request: stored settings are redacted on save.
    model_base_url = model_base_url or _task_ai_setting(task, "baseUrl")
    model_id = model_id or _task_ai_setting(task, "model")
    summary_language = summary_language or _task_ai_setting(task, "summaryLanguage") or task.get("summary_language") or ""
    task.update({
        "status": "processing",
        "progress": 55,
        "message": "Regenerating transcript and translation...",
        "error": None,
        "updated_at": _now_ts(),
    })
    task.pop("completed_at", None)
    save_tasks(tasks, force=True)
    await broadcast_task_update(task_id, task)
    bg = asyncio.create_task(_run_background_task(
        task_id,
        _regenerate_transcript_job(task_id, summary_language, api_key, model_base_url, model_id),
    ))
    active_tasks[task_id] = bg
    return task


async def _regenerate_transcript_job(
    task_id: str,
    summary_language: str = Form(default=""),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task.get("status") not in {"completed", "processing"}:
        raise HTTPException(status_code=400, detail="任务尚未完成")

    raw_script = task.get("timed_transcript") or ""
    if not raw_script.strip():
        raw_file = task.get("raw_script_file") or ""
        raw_path = TEMP_DIR / raw_file if raw_file and ".." not in raw_file and "/" not in raw_file and "\\" not in raw_file else None
        if raw_path and raw_path.exists() and raw_path.parent.resolve() == TEMP_DIR.resolve():
            raw_script = raw_path.read_text(encoding="utf-8")
            raw_script = re.sub(r"\n\nsource:\s*.+\s*$", "", raw_script, flags=re.IGNORECASE | re.DOTALL).strip()
    if not raw_script.strip():
        raise HTTPException(status_code=400, detail="No raw timed transcript is available to regenerate")

    target_language = (summary_language or task.get("summary_language") or "").strip()
    if not target_language:
        raise HTTPException(status_code=400, detail="Missing target language")

    eff_key = (api_key or "").strip()
    eff_base = (model_base_url or "").strip().rstrip("/")
    request_summarizer = Summarizer(
        api_key=eff_key,
        base_url=eff_base or None,
        model=model_id or None,
    ) if eff_key else summarizer

    await _set_dub_progress(task_id, "processing", 1, "Regenerating transcript...")
    task.update({
        "progress": 55,
        "message": "Regenerating transcript and translation...",
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, task)

    await _run_post_extract_pipeline(
        task_id=task_id,
        raw_script=raw_script,
        video_title=task.get("video_title") or task.get("safe_title") or "video",
        source_ref=task.get("url") or task.get("video_title") or "source",
        summary_language=target_language,
        request_summarizer=request_summarizer,
        api_key=api_key,
        model_base_url=model_base_url,
        model_id=model_id,
        media_path=str(TEMP_DIR / task["media_filename"]) if task.get("media_filename") else None,
    )
    updated = tasks[task_id]
    for key in ("export_video_filename", "export_video_url", "dubbed_video_filename", "dubbed_video_url"):
        updated.pop(key, None)
    await _set_dub_progress(task_id, "completed", 100, "Transcript regenerated")
    save_tasks(tasks, force=True)
    await broadcast_task_update(task_id, updated)
    return updated


@app.get("/api/tasks/recent")
async def recent_tasks(limit: int = Query(default=20, ge=1, le=100)):
    items = []
    for task_id, task in tasks.items():
        items.append({
            "task_id": task_id,
            "status": task.get("status") or "unknown",
            "progress": task.get("progress") or 0,
            "message": task.get("message") or task.get("error") or "",
            "video_title": task.get("custom_title") or task.get("video_title") or task.get("safe_title") or "Untitled",
            "original_video_title": task.get("video_title") or task.get("safe_title") or "Untitled",
            "custom_title": task.get("custom_title") or "",
            "url": task.get("url"),
            "detected_language": task.get("detected_language"),
            "summary_language": task.get("summary_language"),
            "has_translation": bool(task.get("translation")),
            "has_media": bool(task.get("media_url") or task.get("media_filename")),
            "has_dubbed_video": bool(task.get("dubbed_video_url") or task.get("dubbed_video_filename")),
            "dub_status": task.get("dub_status") or "",
            "dub_progress": task.get("dub_progress") or 0,
            "dub_message": task.get("dub_message") or "",
            "dubbed_video_filename": task.get("dubbed_video_filename") or "",
            "dubbed_video_url": task.get("dubbed_video_url") or "",
            "completed_at": task.get("completed_at") or task.get("created_at") or 0,
            "created_at": task.get("created_at") or task.get("completed_at") or 0,
        })

    items.sort(key=lambda item: item.get("completed_at") or item.get("created_at") or 0, reverse=True)
    return {"items": items[:limit]}


@app.patch("/api/task/{task_id}/title")
async def rename_task(task_id: str, title: str = Form(default="")):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    cleaned = re.sub(r"\s+", " ", (title or "").strip())
    if len(cleaned) > 160:
        cleaned = cleaned[:160].rstrip()
    if cleaned:
        tasks[task_id]["custom_title"] = cleaned
    else:
        tasks[task_id].pop("custom_title", None)
    save_tasks(tasks, force=True)
    await broadcast_task_update(task_id, tasks[task_id])
    return {
        "task_id": task_id,
        "custom_title": tasks[task_id].get("custom_title") or "",
        "video_title": tasks[task_id].get("custom_title") or tasks[task_id].get("video_title") or tasks[task_id].get("safe_title") or "Untitled",
    }

@app.get("/api/task-stream/{task_id}")
async def task_stream(task_id: str):
    """
    SSE实时任务状态流
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    async def event_generator():
        # 创建任务专用的队列
        queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
        
        # 将队列添加到连接列表
        if task_id not in sse_connections:
            sse_connections[task_id] = []
        sse_connections[task_id].append(queue)
        
        try:
            # 立即发送当前状态
            current_task = tasks.get(task_id, {})
            yield f"data: {json.dumps(current_task, ensure_ascii=False)}\n\n"
            
            # 持续监听状态更新
            while True:
                try:
                    # 等待状态更新，超时时间30秒发送心跳
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {data}\n\n"
                    
                    # 如果任务完成或失败，结束流
                    task_data = json.loads(data)
                    if task_data.get("status") in ["completed", "error"]:
                        break
                        
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                    
        except asyncio.CancelledError:
            logger.info(f"SSE连接被取消: {task_id}")
        except Exception as e:
            logger.error(f"SSE流异常: {e}")
        finally:
            # 清理连接
            if task_id in sse_connections and queue in sse_connections[task_id]:
                sse_connections[task_id].remove(queue)
                if not sse_connections[task_id]:
                    del sse_connections[task_id]
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )

@app.get("/api/media/{filename}")
async def media_file(filename: str):
    """
    Stream generated audio/video files from temp for transcript review.
    """
    allowed_ext = {".m4a", ".mp3", ".mp4", ".wav", ".webm", ".mkv", ".ogg", ".flac"}
    try:
        if ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=400, detail="文件名格式无效")

        ext = Path(filename).suffix.lower()
        if ext not in allowed_ext:
            raise HTTPException(status_code=400, detail="不支持的媒体文件类型")

        file_path = TEMP_DIR / filename
        if not file_path.exists() or file_path.parent.resolve() != TEMP_DIR.resolve():
            raise HTTPException(status_code=404, detail="文件不存在")

        media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        return FileResponse(file_path, media_type=media_type)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取媒体文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取媒体失败: {str(e)}")


@app.post("/api/export-video/{task_id}")
async def export_video(
    task_id: str,
    subtitle_mode: str = Form(default="single"),
    subtitle_source: str = Form(default="translation"),
    secondary_subtitle_source: str = Form(default="transcript"),
    font_size: int = Form(default=28),
    text_color: str = Form(default="#ffffff"),
    outline_color: str = Form(default="#000000"),
    box_color: str = Form(default="#000000"),
    secondary_font_size: int = Form(default=22),
    secondary_text_color: str = Form(default="#ffffff"),
    secondary_outline_color: str = Form(default="#000000"),
    secondary_box_color: str = Form(default="#000000"),
    position: str = Form(default="bottom"),
    subtitle_x_offset: int = Form(default=0),
    subtitle_y_offset: int = Form(default=0),
    subtitle_merge_seconds: float = Form(default=0.0),
    subtitle_time_offset: float = Form(default=0.0),
):
    """
    Render an MP4 with the task audio and burned subtitles.
    Prefer timestamped translated subtitles; fall back to timestamped transcript.
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    media_filename = task.get("media_filename")
    if not media_filename:
        raise HTTPException(status_code=400, detail="任务没有可用于导出视频的媒体文件")

    if ".." in media_filename or "/" in media_filename or "\\" in media_filename:
        raise HTTPException(status_code=400, detail="媒体文件名无效")

    media_path = TEMP_DIR / media_filename
    if not media_path.exists() or media_path.parent.resolve() != TEMP_DIR.resolve():
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    primary_cues = _offset_subtitle_cues(_select_subtitle_cues(task, subtitle_source, subtitle_merge_seconds), subtitle_time_offset)
    dual_enabled = (subtitle_mode or "single").lower() == "dual"
    secondary_cues = (
        _offset_subtitle_cues(_select_subtitle_cues(task, secondary_subtitle_source, subtitle_merge_seconds), subtitle_time_offset)
        if dual_enabled
        else []
    )
    if not primary_cues:
        raise HTTPException(status_code=400, detail="没有可用于导出视频的时间戳字幕")
    if dual_enabled and not secondary_cues:
        raise HTTPException(status_code=400, detail="没有可用于第二字幕的时间戳字幕")

    safe_title = task.get("safe_title") or "video"
    short_id = task.get("short_id") or task_id.replace("-", "")[:6]
    style_raw = (
        f"{SUBTITLE_FONT_FAMILY}_{subtitle_mode}_{subtitle_source}_{secondary_subtitle_source}_"
        f"{font_size}_{secondary_font_size}_{text_color}_{outline_color}_{box_color}_"
        f"{secondary_text_color}_{secondary_outline_color}_{secondary_box_color}_"
        f"{position}_{subtitle_x_offset}_{subtitle_y_offset}_"
        f"{subtitle_merge_seconds}_{subtitle_time_offset}_"
        f"{_subtitle_cues_signature(primary_cues)}_"
        f"{_subtitle_cues_signature(secondary_cues) if dual_enabled else 'single'}"
    )
    style_sig = (
        f"{_sanitize_title_for_filename(style_raw)[:36]}_"
        f"{hashlib.sha1(style_raw.encode('utf-8')).hexdigest()[:10]}"
    )
    sub_filename = f"export_{safe_title}_{short_id}_{style_sig}_primary.ass"
    sub2_filename = f"export_{safe_title}_{short_id}_{style_sig}_secondary.ass"
    video_filename = f"export_{safe_title}_{short_id}_{style_sig}.mp4"
    sub_path = TEMP_DIR / sub_filename
    sub2_path = TEMP_DIR / sub2_filename
    video_path = TEMP_DIR / video_filename

    if video_path.exists():
        if _is_playable_video(video_path):
            task.update({
                "export_video_filename": video_filename,
                "export_video_url": f"/api/media/{video_filename}",
            })
            save_tasks(tasks)
            return {
                "filename": video_filename,
                "url": f"/api/media/{video_filename}",
            }
        video_path.unlink(missing_ok=True)
        for cache_key in ("export_video_filename", "export_video_url"):
            task.pop(cache_key, None)
        save_tasks(tasks)

    width, height = _probe_video_size(media_path) if _has_media_stream(media_path, "v:0", "video") else (1280, 720)
    dual_gap = max(int(font_size or 28), int(secondary_font_size or 22)) + 14
    pos = (position or "bottom").lower()
    primary_shift = 0
    secondary_shift = 0
    if dual_enabled:
        if pos == "top":
            primary_shift, secondary_shift = 0, dual_gap
        elif pos == "middle":
            primary_shift, secondary_shift = -(dual_gap // 2), dual_gap // 2
        else:
            primary_shift, secondary_shift = -dual_gap, 0

    _write_ass(
        primary_cues,
        sub_path,
        width=width,
        height=height,
        font_size=font_size,
        text_color=text_color,
        outline_color=outline_color,
        box_color=box_color,
        position=position,
        x_offset=subtitle_x_offset,
        y_offset=subtitle_y_offset,
        line_shift=primary_shift,
    )
    subtitle_filter = _subtitle_filter_path(sub_path)
    if dual_enabled:
        _write_ass(
            secondary_cues,
            sub2_path,
            width=width,
            height=height,
            font_size=secondary_font_size,
            text_color=secondary_text_color,
            outline_color=secondary_outline_color,
            box_color=secondary_box_color,
            position=position,
            x_offset=subtitle_x_offset,
            y_offset=subtitle_y_offset,
            line_shift=secondary_shift,
        )
        subtitle_filter += f",{_subtitle_filter_path(sub2_path)}"
    if _has_media_stream(media_path, "v:0", "video"):
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(media_path),
            "-vf", subtitle_filter,
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-f", "lavfi", "-i", "color=c=0x111111:s=1280x720:r=30",
            "-i", str(media_path),
            "-vf", subtitle_filter,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "stillimage",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]

    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not video_path.exists():
        err = (result.stderr or result.stdout or "").strip()
        raise HTTPException(status_code=500, detail=f"导出视频失败: {err[-1200:]}")
    if not _is_playable_video(video_path):
        video_path.unlink(missing_ok=True)
        err = (result.stderr or result.stdout or "").strip()
        detail = "Exported video is not playable"
        if err:
            detail += f": {err[-800:]}"
        raise HTTPException(status_code=500, detail=detail)

    task.update({
        "export_video_filename": video_filename,
        "export_video_url": f"/api/media/{video_filename}",
    })
    save_tasks(tasks)

    return {
        "filename": video_filename,
        "url": f"/api/media/{video_filename}",
    }


@app.get("/api/export-subtitles/{task_id}")
async def export_subtitles(
    task_id: str,
    source: str = Query(default="translation"),
    format: str = Query(default="srt"),
    merge_seconds: float = Query(default=0.0, ge=0.0, le=30.0),
    time_offset: float = Query(default=0.0, ge=-10.0, le=10.0),
):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    fmt = (format or "srt").lower()
    if fmt not in {"srt", "vtt"}:
        raise HTTPException(status_code=400, detail="Invalid subtitle format")

    source_key = "translation" if (source or "").lower() == "translation" else "transcript"
    cues = _offset_subtitle_cues(_select_subtitle_cues(task, source_key, merge_seconds), time_offset)
    if not cues:
        raise HTTPException(status_code=400, detail="没有可导出的时间戳字幕")

    safe_title = task.get("safe_title") or "video"
    short_id = task.get("short_id") or task_id.replace("-", "")[:6]
    filename = f"{source_key}_{safe_title}_{short_id}.{fmt}"
    output_path = TEMP_DIR / filename
    if fmt == "srt":
        _write_srt(cues, output_path)
        media_type = "application/x-subrip"
    else:
        _write_vtt(cues, output_path)
        media_type = "text/vtt"

    return FileResponse(output_path, media_type=media_type, filename=filename)


TTS_PREVIEW_TTL_HOURS = _env_int("TTS_PREVIEW_TTL_HOURS", 24, 1, 24 * 30)


def _prune_tts_previews(preview_dir: Path) -> None:
    """预览目录不挂在任何任务下，删除任务时清不掉，这里按时间自行回收。"""
    cutoff = time.time() - TTS_PREVIEW_TTL_HOURS * 3600
    for path in preview_dir.glob("preview_*.wav"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except Exception as e:
            logger.warning("清理 TTS 预览失败 %s: %s", path, e)
    clone_dir = preview_dir / "clone_refs"
    if clone_dir.is_dir():
        for path in clone_dir.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except Exception as e:
                logger.warning("清理 TTS 预览失败 %s: %s", path, e)


@app.post("/api/tts-preview")
async def tts_preview(
    provider: str = Form(default="vieneu"),
    voice: str = Form(default="Phạm Tuyên"),
    style: str = Form(default="tu_nhien"),
    elevenlabs_api_key: str = Form(default=""),
    elevenlabs_model_id: str = Form(default="eleven_multilingual_v2"),
    fpt_api_key: str = Form(default=""),
    fpt_speed: str = Form(default="0"),
    text: str = Form(default="Xin chào, đây là câu đọc thử bằng tiếng Việt để kiểm tra giọng lồng tiếng cho video."),
    clone_audio: Optional[UploadFile] = File(None),
):
    voice_name = (voice or "Phạm Tuyên").strip()
    style_key = style if style in {"tu_nhien", "tin_tuc", "doc_truyen"} else "tu_nhien"
    provider_key = _normalize_tts_provider(provider)
    sample_text = _clean_tts_text(text or "") or "Xin chào, đây là câu đọc thử bằng tiếng Việt để kiểm tra giọng lồng tiếng cho video."
    sample_text = sample_text[:220]
    preview_dir = TEMP_DIR / "tts_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    _prune_tts_previews(preview_dir)
    clone_ref = None
    clone_sig = "noclone"
    if provider_key == "vieneu" and clone_audio and clone_audio.filename:
        clone_ref = await _save_clone_upload(
            clone_audio,
            preview_dir / "clone_refs",
            f"preview_{uuid.uuid4().hex[:12]}",
        )
        if clone_ref:
            clone_path = Path(clone_ref)
            clone_sig = hashlib.sha1(clone_path.read_bytes()).hexdigest()[:12]
    sig = hashlib.sha1(
        f"{provider_key}|{voice_name}|{style_key}|{elevenlabs_model_id}|{fpt_speed}|{clone_sig}|{sample_text}".encode("utf-8")
    ).hexdigest()[:12]
    output_path = preview_dir / f"preview_{sig}.wav"
    if not output_path.exists() or not _has_audible_audio(output_path):
        selected_engine = _tts_engine_for_provider(provider_key)
        extra_kwargs = {}
        if provider_key == "elevenlabs":
            extra_kwargs = {
                "api_key": elevenlabs_api_key,
                "model_id": elevenlabs_model_id,
            }
        elif provider_key == "fpt":
            extra_kwargs = {
                "api_key": fpt_api_key,
                "speed": fpt_speed,
            }
        try:
            await selected_engine.synthesize_to_file(
                sample_text,
                output_path,
                voice=voice_name,
                style=style_key,
                ref_audio=clone_ref,
                denoise=True,
                **extra_kwargs,
            )
        except RuntimeError as exc:
            detail = str(exc)
            status_code = 402 if "HTTP 402" in detail or "payment_required" in detail or "paid_plan_required" in detail else 400
            raise HTTPException(status_code=status_code, detail=detail) from exc
    if not output_path.exists() or output_path.stat().st_size == 0 or not _has_audible_audio(output_path):
        raise HTTPException(status_code=500, detail="TTS preview generated no audible audio")
    return FileResponse(output_path, media_type="audio/wav", filename=output_path.name)


@app.post("/api/elevenlabs/voices")
async def elevenlabs_voices(api_key: str = Form(default="")):
    effective_key = (api_key or "").strip() or os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not effective_key:
        raise HTTPException(status_code=400, detail="ElevenLabs API key is required")

    req = urlrequest.Request(
        "https://api.elevenlabs.io/v2/voices",
        method="GET",
        headers={"xi-api-key": effective_key, "Accept": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=exc.code, detail=detail[:500]) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch ElevenLabs voices: {exc}") from exc

    raw_voices = payload.get("voices") if isinstance(payload, dict) else None
    voices = []
    if isinstance(raw_voices, list):
        for item in raw_voices:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("voice_id") or item.get("id") or "").strip()
            name = str(item.get("name") or voice_id).strip()
            if not voice_id:
                continue
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            language = str(labels.get("language") or labels.get("accent") or "").strip()
            gender = str(labels.get("gender") or "").strip()
            accent = str(labels.get("accent") or "").strip()
            category = str(item.get("category") or "").strip()
            voices.append({
                "id": voice_id,
                "name": name,
                "language": language,
                "country": accent,
                "category": " · ".join(part for part in (gender, category) if part),
            })
    voices.sort(key=lambda voice: voice["name"].lower())
    return {"data": voices}


@app.post("/api/export-dubbed-video/{task_id}")
async def export_dubbed_video(
    task_id: str,
    mode: str = Form(default="replace"),
    original_volume: float = Form(default=0.25),
    include_subtitles: bool = Form(default=False),
    subtitle_mode: str = Form(default="single"),
    subtitle_source: str = Form(default="translation"),
    secondary_subtitle_source: str = Form(default="transcript"),
    font_size: int = Form(default=28),
    text_color: str = Form(default="#ffffff"),
    outline_color: str = Form(default="#000000"),
    box_color: str = Form(default="#000000"),
    secondary_font_size: int = Form(default=22),
    secondary_text_color: str = Form(default="#ffffff"),
    secondary_outline_color: str = Form(default="#000000"),
    secondary_box_color: str = Form(default="#000000"),
    position: str = Form(default="bottom"),
    subtitle_x_offset: int = Form(default=0),
    subtitle_y_offset: int = Form(default=0),
    subtitle_merge_seconds: float = Form(default=0.0),
    subtitle_time_offset: float = Form(default=0.0),
    speaker_mode: str = Form(default="auto_gender"),
    tts_provider: str = Form(default="vieneu"),
    default_voice: str = Form(default="Phạm Tuyên"),
    male_voice: str = Form(default="Phạm Tuyên"),
    female_voice: str = Form(default="Trúc Ly"),
    elevenlabs_api_key: str = Form(default=""),
    elevenlabs_model_id: str = Form(default="eleven_multilingual_v2"),
    elevenlabs_default_voice_id: str = Form(default=""),
    elevenlabs_male_voice_id: str = Form(default=""),
    elevenlabs_female_voice_id: str = Form(default=""),
    fpt_api_key: str = Form(default=""),
    fpt_speed: str = Form(default="0"),
    fpt_default_voice: str = Form(default="banmai"),
    fpt_male_voice: str = Form(default="leminh"),
    fpt_female_voice: str = Form(default="banmai"),
    tts_style: str = Form(default="tu_nhien"),
    tts_merge_seconds: float = Form(default=0.0),
    tts_max_fit_speed: float = Form(default=1.3),
    dub_timing_scale: float = Form(default=1.0),
    dub_audio_offset: float = Form(default=0.0),
    voice_overrides: str = Form(default=""),
    default_clone: Optional[UploadFile] = File(None),
    male_clone: Optional[UploadFile] = File(None),
    female_clone: Optional[UploadFile] = File(None),
):
    """
    Render an MP4 with a translated VieNeu-TTS voice track.

    mode=replace replaces the original audio. mode=voiceover lowers the original
    audio and mixes the translated voice on top.
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    await _set_dub_progress(task_id, "processing", 1, "Preparing dubbed export...")

    media_filename = task.get("media_filename")
    if not media_filename:
        raise HTTPException(status_code=400, detail="任务没有可用于配音的视频文件")
    if ".." in media_filename or "/" in media_filename or "\\" in media_filename:
        raise HTTPException(status_code=400, detail="媒体文件名无效")

    media_path = TEMP_DIR / media_filename
    if not media_path.exists() or media_path.parent.resolve() != TEMP_DIR.resolve():
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    needs_translated_dub = translator.languages_differ_for_translation(
        task.get("detected_language"),
        task.get("summary_language"),
    )
    try:
        dub_merge_seconds = max(0.0, min(10.0, float(tts_merge_seconds or 0)))
    except (TypeError, ValueError):
        dub_merge_seconds = 0.0
    try:
        max_fit_speed = max(1.0, min(2.0, float(tts_max_fit_speed or 1.3)))
    except (TypeError, ValueError):
        max_fit_speed = 1.3
    try:
        timing_scale = _normalize_dub_timing_scale(dub_timing_scale)
    except Exception:
        timing_scale = 1.0
    try:
        audio_offset = max(-10.0, min(10.0, float(dub_audio_offset or 0.0)))
    except (TypeError, ValueError):
        audio_offset = 0.0
    try:
        original_volume_level = max(0.0, min(1.0, float(original_volume)))
    except (TypeError, ValueError):
        original_volume_level = 0.25
    fallback_cue_count = 0
    if needs_translated_dub:
        cues, translated_cue_count, fallback_cue_count = _dub_cues_from_original_timing(task, 0.0)
        if not cues:
            detail = task.get("translation_error") or (
                "No translated timestamp text is available. Check the model API key/base URL, "
                "then process the video again."
            )
            await _set_dub_progress(task_id, "error", 0, detail)
            raise HTTPException(status_code=400, detail=detail)
        if translated_cue_count == 0:
            detail = task.get("translation_error") or "No translated text is available for dubbed export"
            await _set_dub_progress(task_id, "error", 0, detail)
            raise HTTPException(status_code=400, detail=detail)
        if fallback_cue_count:
            detail = (
                f"Translation is incomplete: {fallback_cue_count} transcript segment(s) have no translated text. "
                "Reprocess the video or regenerate translation before exporting dubbed video."
            )
            await _set_dub_progress(task_id, "error", 0, detail)
            raise HTTPException(status_code=400, detail=detail)
    else:
        cues = _select_subtitle_cues(task, "translation", 0.0) or _select_subtitle_cues(task, "transcript", 0.0)
    if not cues:
        await _set_dub_progress(task_id, "error", 0, "No timestamped text is available for dubbing")
        raise HTTPException(status_code=400, detail="没有可用于配音的时间戳文本")
    cues = _apply_voice_overrides(cues, voice_overrides)

    export_mode = "voiceover" if mode == "voiceover" else "replace"
    speaker_mode_key = speaker_mode if speaker_mode in {"default", "all_male", "all_female", "alternate", "auto_gender"} else "default"
    safe_title = task.get("safe_title") or "video"
    short_id = task.get("short_id") or task_id.replace("-", "")[:6]
    clone_work_dir = TEMP_DIR / f"dub_refs_{task_id.replace('-', '')[:12]}"
    default_ref = await _save_clone_upload(default_clone, clone_work_dir, "default")
    male_ref = await _save_clone_upload(male_clone, clone_work_dir, "male")
    female_ref = await _save_clone_upload(female_clone, clone_work_dir, "female")
    provider_key = _normalize_tts_provider(tts_provider)
    style_key = tts_style if tts_style in {"tu_nhien", "tin_tuc", "doc_truyen"} else "tu_nhien"
    if provider_key == "elevenlabs":
        default_voice_value = (elevenlabs_default_voice_id or default_voice).strip()
        male_voice_value = (elevenlabs_male_voice_id or default_voice_value).strip()
        female_voice_value = (elevenlabs_female_voice_id or default_voice_value).strip()
        if not default_voice_value:
            await _set_dub_progress(task_id, "error", 0, "ElevenLabs default voice ID is missing")
            raise HTTPException(status_code=400, detail="ElevenLabs default voice ID is missing")
    elif provider_key == "fpt":
        default_voice_value = fpt_default_voice.strip() or "banmai"
        male_voice_value = fpt_male_voice.strip() or default_voice_value
        female_voice_value = fpt_female_voice.strip() or default_voice_value
        if not (fpt_api_key or os.getenv("FPT_API_KEY", "")).strip():
            await _set_dub_progress(task_id, "error", 0, "FPT API key is missing")
            raise HTTPException(status_code=400, detail="FPT API key is missing")
    else:
        default_voice_value = default_voice.strip() or "Phạm Tuyên"
        male_voice_value = male_voice.strip() or default_voice_value
        female_voice_value = female_voice.strip() or "Trúc Ly"
    voice_profiles = {
        "default": {
            "voice": default_voice_value,
            "style": style_key,
            "ref_audio": default_ref,
            "denoise": True,
        },
        "male": {
            "voice": male_voice_value,
            "style": style_key,
            "ref_audio": male_ref or default_ref,
            "denoise": True,
        },
        "female": {
            "voice": female_voice_value,
            "style": style_key,
            "ref_audio": female_ref or default_ref,
            "denoise": True,
        },
    }
    cues = _merge_short_dub_cues_by_voice(cues, dub_merge_seconds, speaker_mode_key, voice_profiles)
    cues = _scale_dub_cue_timing(cues, timing_scale)
    cues = _offset_dub_cue_timing(cues, audio_offset)
    cue_sig = _subtitle_cues_signature(cues)
    subtitle_style_raw = "nosub"
    if include_subtitles:
        primary_subtitle_cues = _offset_subtitle_cues(
            _select_subtitle_cues(task, subtitle_source, subtitle_merge_seconds),
            subtitle_time_offset,
        )
        secondary_subtitle_cues = (
            _offset_subtitle_cues(
                _select_subtitle_cues(task, secondary_subtitle_source, subtitle_merge_seconds),
                subtitle_time_offset,
            )
            if (subtitle_mode or "single").lower() == "dual"
            else []
        )
        subtitle_style_raw = (
            f"sub{subtitle_mode}_{subtitle_source}_{secondary_subtitle_source}_"
            f"{font_size}_{secondary_font_size}_{text_color}_{outline_color}_{box_color}_"
            f"{secondary_text_color}_{secondary_outline_color}_{secondary_box_color}_"
            f"{position}_{subtitle_x_offset}_{subtitle_y_offset}_{subtitle_merge_seconds}_{subtitle_time_offset}_"
            f"{_subtitle_cues_signature(primary_subtitle_cues)}_"
            f"{_subtitle_cues_signature(secondary_subtitle_cues) if secondary_subtitle_cues else 'single'}"
        )
    style_raw = (
        f"{_dub_style_signature(export_mode, speaker_mode_key, voice_profiles)}_"
        f"provider{provider_key}_model{elevenlabs_model_id if provider_key == 'elevenlabs' else ('fpt' + str(fpt_speed) if provider_key == 'fpt' else 'vieneu')}_"
        f"origvol{original_volume_level:g}_merge{dub_merge_seconds:g}_gapmerge1_durationscale1_{timing_scale:g}_audiooffset{audio_offset:g}_cliptrim1_"
        f"speed{max_fit_speed:g}_origtime1_fallback{fallback_cue_count}_"
        f"voices{hashlib.sha1((voice_overrides or '').encode('utf-8')).hexdigest()[:8]}_"
        f"{subtitle_style_raw}_{cue_sig}"
    )
    style_sig = (
        f"{_sanitize_title_for_filename(style_raw)[:52]}_"
        f"{hashlib.sha1(style_raw.encode('utf-8')).hexdigest()[:10]}"
    )
    video_filename = f"dub_{safe_title}_{short_id}_{style_sig}.mp4"
    video_path = TEMP_DIR / video_filename

    if video_path.exists():
        if _has_audible_audio(video_path):
            task.update({
                "dubbed_video_filename": video_filename,
                "dubbed_video_url": f"/api/media/{video_filename}",
            })
            await _set_dub_progress(task_id, "completed", 100, "Dubbed video is ready")
            save_tasks(tasks)
            return {"filename": video_filename, "url": f"/api/media/{video_filename}"}
        video_path.unlink(missing_ok=True)

    if fallback_cue_count:
        await _set_dub_progress(
            task_id,
            "processing",
            5,
            f"Preparing {len(cues)} voice segment(s); {fallback_cue_count} untranslated segment(s) will use original text...",
        )
    else:
        await _set_dub_progress(task_id, "processing", 5, f"Preparing {len(cues)} voice segment(s)...")

    media_duration = _probe_duration(media_path)
    cue_duration = max(float(cue["end"]) for cue in cues)
    duration = max(media_duration, cue_duration, 1.0)
    work_dir = TEMP_DIR / f"dub_{task_id.replace('-', '')[:12]}_{style_sig}"

    try:
        dub_audio = await _build_dub_audio(
            cues,
            work_dir,
            duration,
            task_id=task_id,
            voice_profiles=voice_profiles,
            speaker_mode=speaker_mode_key,
            max_fit_speed=max_fit_speed,
            tts_provider=provider_key,
            elevenlabs_api_key=elevenlabs_api_key,
            elevenlabs_model_id=elevenlabs_model_id,
            fpt_api_key=fpt_api_key,
            fpt_speed=fpt_speed,
        )
        duration = max(duration, _probe_duration(dub_audio), 1.0)
    except RuntimeError as e:
        await _set_dub_progress(task_id, "error", 0, str(e))
        raise HTTPException(status_code=500, detail=str(e))

    has_video = _has_media_stream(media_path, "v:0", "video")
    has_audio = _has_media_stream(media_path, "a:0", "audio")
    subtitle_filter = None
    if include_subtitles:
        subtitle_filter = _prepare_subtitle_filter(
            task,
            media_path,
            style_sig,
            subtitle_mode,
            subtitle_source,
            secondary_subtitle_source,
            font_size,
            text_color,
            outline_color,
            box_color,
            secondary_font_size,
            secondary_text_color,
            secondary_outline_color,
            secondary_box_color,
            position,
            subtitle_x_offset,
            subtitle_y_offset,
            subtitle_merge_seconds,
            subtitle_time_offset,
        )

    if has_video and export_mode == "voiceover" and has_audio:
        audio_filter = (
            f"[0:a]volume={original_volume_level:.4f}[orig];[1:a]volume=1.0[dub];"
            "[orig][dub]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95[aout]"
        )
        if subtitle_filter:
            filter_complex = f"[0:v]{subtitle_filter}[vout];{audio_filter}"
            video_map = "[vout]"
        else:
            filter_complex = audio_filter
            video_map = "0:v:0"
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(media_path),
            "-i", str(dub_audio),
            "-filter_complex", filter_complex,
            "-map", video_map,
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
    elif has_video:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(media_path),
            "-i", str(dub_audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        if subtitle_filter:
            cmd[7:7] = ["-vf", subtitle_filter]
    else:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-f", "lavfi", "-i", "color=c=0x111111:s=1280x720:r=30",
            "-i", str(dub_audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "stillimage",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", f"{duration:.3f}",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        if subtitle_filter:
            cmd[9:9] = ["-vf", subtitle_filter]

    await _set_dub_progress(task_id, "processing", 88, "Muxing dubbed audio into video...")

    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not video_path.exists():
        err = (result.stderr or result.stdout or "").strip()
        await _set_dub_progress(task_id, "error", 0, f"Export dubbed video failed: {err[-500:]}")
        raise HTTPException(status_code=500, detail=f"导出配音视频失败: {err[-1200:]}")
    if not _has_audible_audio(video_path):
        video_path.unlink(missing_ok=True)
        detail = "Dubbed export finished but the output video has no audible audio stream"
        await _set_dub_progress(task_id, "error", 0, detail)
        raise HTTPException(status_code=500, detail=detail)

    task.update({
        "dubbed_video_filename": video_filename,
        "dubbed_video_url": f"/api/media/{video_filename}",
    })
    await _set_dub_progress(task_id, "completed", 100, "Dubbed video is ready")
    save_tasks(tasks)

    return {
        "filename": video_filename,
        "url": f"/api/media/{video_filename}",
    }


@app.post("/api/export-dubbed-video-clips/{task_id}")
async def export_dubbed_video_clips(
    task_id: str,
    mode: str = Form(default="replace"),
    original_volume: float = Form(default=0.25),
    include_subtitles: bool = Form(default=False),
    subtitle_mode: str = Form(default="single"),
    subtitle_source: str = Form(default="translation"),
    secondary_subtitle_source: str = Form(default="transcript"),
    font_size: int = Form(default=28),
    text_color: str = Form(default="#ffffff"),
    outline_color: str = Form(default="#000000"),
    box_color: str = Form(default="#000000"),
    secondary_font_size: int = Form(default=22),
    secondary_text_color: str = Form(default="#ffffff"),
    secondary_outline_color: str = Form(default="#000000"),
    secondary_box_color: str = Form(default="#000000"),
    position: str = Form(default="bottom"),
    subtitle_x_offset: int = Form(default=0),
    subtitle_y_offset: int = Form(default=0),
    subtitle_merge_seconds: float = Form(default=0.0),
    subtitle_time_offset: float = Form(default=0.0),
    tts_merge_seconds: float = Form(default=0.0),
    tts_max_fit_speed: float = Form(default=1.3),
    dub_timing_scale: float = Form(default=1.0),
    dub_audio_offset: float = Form(default=0.0),
    puter_provider: str = Form(default="openai"),
    puter_model: str = Form(default="gpt-4o-mini-tts"),
    puter_language: str = Form(default="vi-VN"),
    puter_default_voice: str = Form(default="alloy"),
    puter_male_voice: str = Form(default="onyx"),
    puter_female_voice: str = Form(default="nova"),
    cues_json: str = Form(default="[]"),
    clip_files: list[UploadFile] = File(default=[]),
):
    raise HTTPException(status_code=410, detail="Puter TTS is disabled")

    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    await _set_dub_progress(task_id, "processing", 72, "Uploading Puter voice clips...")

    media_filename = task.get("media_filename")
    if not media_filename:
        raise HTTPException(status_code=400, detail="任务没有可用于配音的视频文件")
    if ".." in media_filename or "/" in media_filename or "\\" in media_filename:
        raise HTTPException(status_code=400, detail="媒体文件名无效")

    media_path = TEMP_DIR / media_filename
    if not media_path.exists() or media_path.parent.resolve() != TEMP_DIR.resolve():
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    try:
        cue_items = json.loads(cues_json or "[]")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Puter cue metadata")
    if not isinstance(cue_items, list) or not cue_items:
        raise HTTPException(status_code=400, detail="No Puter TTS cue metadata was uploaded")
    if len(cue_items) != len(clip_files):
        raise HTTPException(status_code=400, detail="Puter cue/audio count mismatch")

    try:
        max_fit_speed = max(1.0, min(2.0, float(tts_max_fit_speed or 1.3)))
    except (TypeError, ValueError):
        max_fit_speed = 1.3
    try:
        timing_scale = _normalize_dub_timing_scale(dub_timing_scale)
    except Exception:
        timing_scale = 1.0
    try:
        audio_offset = max(-10.0, min(10.0, float(dub_audio_offset or 0.0)))
    except (TypeError, ValueError):
        audio_offset = 0.0
    try:
        original_volume_level = max(0.0, min(1.0, float(original_volume)))
    except (TypeError, ValueError):
        original_volume_level = 0.25

    cues = []
    for item in cue_items:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        text = _clean_tts_text(item.get("text") or "")
        if end > start and text:
            cues.append({"start": start, "end": end, "text": text, "voice_gender": item.get("role") or "default"})
    if len(cues) != len(clip_files):
        raise HTTPException(status_code=400, detail="Invalid Puter cue metadata")
    cues = _scale_dub_cue_timing(cues, timing_scale)
    cues = _offset_dub_cue_timing(cues, audio_offset)

    export_mode = "voiceover" if mode == "voiceover" else "replace"
    safe_title = task.get("safe_title") or "video"
    short_id = task.get("short_id") or task_id.replace("-", "")[:6]
    settings_raw = json.dumps({
        "provider": puter_provider,
        "model": puter_model,
        "language": puter_language,
        "default": puter_default_voice,
        "male": puter_male_voice,
        "female": puter_female_voice,
        "mode": export_mode,
        "original_volume": original_volume_level,
        "include_subtitles": include_subtitles,
        "subtitle_mode": subtitle_mode,
        "subtitle_source": subtitle_source,
        "secondary_subtitle_source": secondary_subtitle_source,
        "font_size": font_size,
        "position": position,
        "subtitle_x_offset": subtitle_x_offset,
        "subtitle_y_offset": subtitle_y_offset,
        "subtitle_merge_seconds": subtitle_merge_seconds,
        "subtitle_time_offset": subtitle_time_offset,
        "tts_merge_seconds": tts_merge_seconds,
        "tts_max_fit_speed": max_fit_speed,
        "dub_timing_scale": timing_scale,
        "dub_audio_offset": audio_offset,
        "alignment": "original_timing_gapmerge_cliptrim_duration_scale_v1",
        "cues": cue_items,
    }, ensure_ascii=False, sort_keys=True)
    style_sig = f"puter_{hashlib.sha1(settings_raw.encode('utf-8')).hexdigest()[:14]}"
    video_filename = f"dub_{safe_title}_{short_id}_{style_sig}.mp4"
    video_path = TEMP_DIR / video_filename
    if video_path.exists() and _has_audible_audio(video_path):
        task.update({
            "dubbed_video_filename": video_filename,
            "dubbed_video_url": f"/api/media/{video_filename}",
        })
        await _set_dub_progress(task_id, "completed", 100, "Dubbed video is ready")
        save_tasks(tasks)
        return {"filename": video_filename, "url": f"/api/media/{video_filename}"}
    video_path.unlink(missing_ok=True)

    media_duration = _probe_duration(media_path)
    cue_duration = max(float(cue["end"]) for cue in cues)
    duration = max(media_duration, cue_duration, 1.0)
    work_dir = TEMP_DIR / f"dub_{task_id.replace('-', '')[:12]}_{style_sig}"
    work_dir.mkdir(parents=True, exist_ok=True)

    clip_paths = []
    try:
        for index, (cue, upload) in enumerate(zip(cues, clip_files), start=1):
            await _set_dub_progress(task_id, "processing", 72 + int(index / max(len(cues), 1) * 8), f"Preparing Puter clip {index}/{len(cues)}...")
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in {".mp3", ".wav", ".ogg", ".webm", ".m4a", ".aac", ".flac"}:
                suffix = ".mp3"
            raw_clip = work_dir / f"puter_{index:04d}{suffix}"
            fitted_clip = work_dir / f"puter_{index:04d}_fit.wav"
            raw_clip.write_bytes(await upload.read())
            if not raw_clip.exists() or raw_clip.stat().st_size == 0 or not _has_audible_audio(raw_clip):
                raise RuntimeError(f"Puter clip {index} is empty or silent")
            clip_path = await _fit_tts_clip_to_cue(
                raw_clip,
                fitted_clip,
                max(0.1, float(cue["end"]) - float(cue["start"])),
                max_fit_speed,
            )
            clip_paths.append((cue, clip_path, _probe_duration(clip_path)))
        dub_audio = await _mix_dub_clips(clip_paths, work_dir, duration, task_id=task_id)
        duration = max(duration, _probe_duration(dub_audio), 1.0)
    except RuntimeError as e:
        await _set_dub_progress(task_id, "error", 0, str(e))
        raise HTTPException(status_code=500, detail=str(e))

    has_video = _has_media_stream(media_path, "v:0", "video")
    has_audio = _has_media_stream(media_path, "a:0", "audio")
    subtitle_filter = None
    if include_subtitles:
        subtitle_filter = _prepare_subtitle_filter(
            task,
            media_path,
            style_sig,
            subtitle_mode,
            subtitle_source,
            secondary_subtitle_source,
            font_size,
            text_color,
            outline_color,
            box_color,
            secondary_font_size,
            secondary_text_color,
            secondary_outline_color,
            secondary_box_color,
            position,
            subtitle_x_offset,
            subtitle_y_offset,
            subtitle_merge_seconds,
            subtitle_time_offset,
        )

    if has_video and export_mode == "voiceover" and has_audio:
        audio_filter = (
            f"[0:a]volume={original_volume_level:.4f}[orig];[1:a]volume=1.0[dub];"
            "[orig][dub]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95[aout]"
        )
        if subtitle_filter:
            filter_complex = f"[0:v]{subtitle_filter}[vout];{audio_filter}"
            video_map = "[vout]"
        else:
            filter_complex = audio_filter
            video_map = "0:v:0"
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(media_path),
            "-i", str(dub_audio),
            "-filter_complex", filter_complex,
            "-map", video_map,
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
    elif has_video:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(media_path),
            "-i", str(dub_audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        if subtitle_filter:
            cmd[7:7] = ["-vf", subtitle_filter]
    else:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-f", "lavfi", "-i", "color=c=0x111111:s=1280x720:r=30",
            "-i", str(dub_audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "stillimage",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", f"{duration:.3f}",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        if subtitle_filter:
            cmd[9:9] = ["-vf", subtitle_filter]

    await _set_dub_progress(task_id, "processing", 88, "Muxing dubbed audio into video...")
    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not video_path.exists():
        err = (result.stderr or result.stdout or "").strip()
        await _set_dub_progress(task_id, "error", 0, f"Export dubbed video failed: {err[-500:]}")
        raise HTTPException(status_code=500, detail=f"导出配音视频失败: {err[-1200:]}")
    if not _has_audible_audio(video_path):
        video_path.unlink(missing_ok=True)
        detail = "Dubbed export finished but the output video has no audible audio stream"
        await _set_dub_progress(task_id, "error", 0, detail)
        raise HTTPException(status_code=500, detail=detail)

    task.update({
        "dubbed_video_filename": video_filename,
        "dubbed_video_url": f"/api/media/{video_filename}",
    })
    await _set_dub_progress(task_id, "completed", 100, "Dubbed video is ready")
    save_tasks(tasks, force=True)
    return {"filename": video_filename, "url": f"/api/media/{video_filename}"}


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """
    直接从temp目录下载文件（简化方案）
    """
    try:
        # 检查文件扩展名安全性
        if not filename.endswith('.md'):
            raise HTTPException(status_code=400, detail="仅支持下载.md文件")
        
        # 检查文件名格式（防止路径遍历攻击）
        if '..' in filename or '/' in filename or '\\' in filename:
            raise HTTPException(status_code=400, detail="文件名格式无效")
            
        file_path = TEMP_DIR / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="文件不存在")
            
        return FileResponse(
            file_path,
            filename=filename,
            media_type="text/markdown"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str):
    """
    取消并删除任务
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task_record = tasks[task_id]
    
    # 如果任务还在运行，先取消它
    if task_id in active_tasks:
        task = active_tasks[task_id]
        if not task.done():
            task.cancel()
            logger.info(f"任务 {task_id} 已被取消")
        del active_tasks[task_id]
    
    # 从处理URL列表中移除
    task_url = task_record.get("url")
    if task_url:
        processing_urls.discard(task_url)
    task_dedup_url = task_record.get("dedup_url")
    if task_dedup_url:
        processing_urls.discard(task_dedup_url)

    removed_files = _delete_task_files(task_id, task_record)
    sse_connections.pop(task_id, None)
    
    # 删除任务记录
    del tasks[task_id]
    save_tasks(tasks, force=True)
    return {"message": "任务已取消并删除", "removed_files": removed_files}

@app.get("/api/tasks/active")
async def get_active_tasks():
    """
    获取当前活跃任务列表（用于调试）
    """
    _cleanup_done_active_tasks()
    active_count = len(active_tasks)
    processing_count = len(processing_urls)
    return {
        "active_tasks": active_count,
        "processing_urls": processing_count,
        "task_ids": list(active_tasks.keys())
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_env_int("PORT", 8000, 1, 65535),
    )
