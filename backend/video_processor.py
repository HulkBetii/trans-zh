import os
import re
import shutil
import uuid
import asyncio
import subprocess
import json
import tempfile
import time
import yt_dlp
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from yt_dlp.networking.impersonate import ImpersonateTarget

logger = logging.getLogger(__name__)
YT_DLP_IMPERSONATE_TARGET = ImpersonateTarget.from_str("chrome")
VIDEO_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

DEFAULT_PLATFORM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

class VideoProcessor:
    """视频处理器，使用yt-dlp下载和转换视频"""
    
    # 每个下载入口都会自行构造 ydl_opts（见 download_and_convert /
    # download_source_video_only），这里不保存共享配置，避免与实际使用的参数不一致。

    async def normalize_local_media_to_m4a(self, input_path: Path, output_dir: Path) -> str:
        """
        将本地上传的音视频转为单声道 16kHz AAC m4a，供 Faster-Whisper 使用（与 yt-dlp 后处理参数对齐）。
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        unique_id = str(uuid.uuid4())[:8]
        out_path = output_dir / f"upload_norm_{unique_id}.m4a"

        cmd = [
            "ffmpeg", "-y", "-nostdin", "-i", str(input_path.resolve()),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(out_path.resolve()),
        ]

        def _run():
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip()
                raise Exception(f"FFmpeg 转换失败: {err[:800]}")
            if not out_path.exists():
                raise Exception("FFmpeg 未生成输出文件")

        await asyncio.to_thread(_run)
        return str(out_path)

    def _has_audio_stream(self, media_path: Path) -> bool:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(media_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return result.returncode == 0 and "audio" in (result.stdout or "").lower()

    def _has_video_stream(self, media_path: Path) -> bool:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(media_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return result.returncode == 0 and "video" in (result.stdout or "").lower()

    def _probe_duration(self, media_path: Path) -> float:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return 0.0
        try:
            return max(0.0, float((result.stdout or "").strip()))
        except ValueError:
            return 0.0

    def _extract_video_url(self, text: str) -> str:
        match = VIDEO_URL_RE.search(text or "")
        if not match:
            return (text or "").strip()
        return match.group(0).rstrip("，。,.")

    def _site_key(self, url: str) -> str:
        host = (urlparse(url).netloc or "").lower()
        if "douyin.com" in host or "iesdouyin.com" in host or "amemv.com" in host:
            return "douyin"
        if "bilibili.com" in host or "b23.tv" in host:
            return "bilibili"
        if "tiktok.com" in host:
            return "tiktok"
        return ""

    def _cookie_from_overrides(self, site: str, platform_cookies: Optional[dict] = None) -> str:
        if not platform_cookies:
            return ""
        value = platform_cookies.get(site) or ""
        return self._normalize_cookie(value) if isinstance(value, str) else ""

    def cookie_diagnostics(self, site: str, platform_cookies: Optional[dict] = None) -> dict:
        file_path = self._cookie_file_from_overrides(site, platform_cookies)
        cookie = self._cookie_from_overrides(site, platform_cookies)
        source = "ui_file" if file_path else ("ui" if cookie else "")
        if not cookie:
            env_name = self._cookie_env_name(site)
            cookie = self._normalize_cookie(os.getenv(env_name or "", ""))
            source = source or ("env" if cookie else "")
        names = []
        if file_path:
            try:
                for line in Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7 and parts[5].strip():
                        names.append(parts[5].strip())
            except Exception:
                pass
        for part in cookie.split(";"):
            item = part.strip()
            if "=" not in item:
                continue
            name = item.split("=", 1)[0].strip()
            if name:
                names.append(name)
        return {
            "source": source,
            "count": len(names),
            "has_cookie_file": bool(file_path),
            "has_cookie": bool(names),
            "has_ttwid": "ttwid" in names,
            "has_ms_token": "msToken" in names or "ms_token" in names,
        }

    def _cookie_file_from_overrides(self, site: str, platform_cookies: Optional[dict] = None) -> str:
        if not platform_cookies:
            return ""
        value = platform_cookies.get(f"{site}_cookie_file") or ""
        return value.strip() if isinstance(value, str) else ""

    def _normalize_cookie(self, raw_cookie: str) -> str:
        text = (raw_cookie or "").strip()
        if not text:
            return ""

        cookie_match = re.search(r"(?im)^\s*cookie\s*:\s*(.+)$", text)
        if cookie_match:
            text = cookie_match.group(1).strip()

        text = re.sub(r"(?im)^\s*(user-agent|accept|referer|origin|host|sec-[^:]+|x-[^:]+)\s*:\s*.*$", "", text)
        text = text.replace("\r", "\n")
        text = re.sub(r"\n+", "; ", text)
        text = re.sub(r"\s*;\s*", "; ", text)
        return text.strip(" ;")

    def _platform_headers(self, site: str, platform_cookies: Optional[dict] = None) -> dict:
        headers = DEFAULT_PLATFORM_HEADERS.copy()
        if site == "douyin":
            headers["Referer"] = "https://www.douyin.com/"
            cookie = self._cookie_from_overrides(site, platform_cookies) or self._normalize_cookie(os.getenv("DOUYIN_COOKIE", ""))
            if cookie:
                headers["Cookie"] = cookie
        elif site == "bilibili":
            headers["Referer"] = "https://www.bilibili.com/"
            cookie = self._cookie_from_overrides(site, platform_cookies) or self._normalize_cookie(os.getenv("BILIBILI_COOKIE", ""))
            if cookie:
                headers["Cookie"] = cookie
        elif site == "tiktok":
            headers["Referer"] = "https://www.tiktok.com/"
        return headers

    def _cookie_env_name(self, site: str) -> Optional[str]:
        if site == "douyin":
            return "DOUYIN_COOKIE"
        if site == "bilibili":
            return "BILIBILI_COOKIE"
        return None

    def _cookie_file_env_name(self, site: str) -> Optional[str]:
        if site == "douyin":
            return "DOUYIN_COOKIE_FILE"
        if site == "bilibili":
            return "BILIBILI_COOKIE_FILE"
        return None

    def _cookie_domain(self, site: str) -> Optional[str]:
        if site == "douyin":
            return ".douyin.com"
        if site == "bilibili":
            return ".bilibili.com"
        return None

    def _cookiefile_from_env(self, site: str, platform_cookies: Optional[dict] = None) -> Optional[str]:
        override_file = self._cookie_file_from_overrides(site, platform_cookies)
        if override_file:
            return override_file

        file_env = self._cookie_file_env_name(site)
        if file_env:
            cookie_file = os.getenv(file_env, "").strip()
            if cookie_file:
                return cookie_file

        cookie_env = self._cookie_env_name(site)
        domain = self._cookie_domain(site)
        raw_cookie = self._cookie_from_overrides(site, platform_cookies) or self._normalize_cookie(os.getenv(cookie_env or "", ""))
        if not raw_cookie or not domain:
            return None

        cookie_lines = [
            "# Netscape HTTP Cookie File",
            "# Generated from environment for yt-dlp.",
        ]
        expires = str(int(time.time()) + 30 * 24 * 60 * 60)
        for part in raw_cookie.split(";"):
            item = part.strip()
            if not item or "=" not in item:
                continue
            name, value = item.split("=", 1)
            name = name.strip()
            if not name:
                continue
            cookie_lines.append(
                "\t".join([domain, "TRUE", "/", "FALSE", expires, name, value.strip()])
            )

        if len(cookie_lines) <= 2:
            return None

        cookie_path = Path(tempfile.gettempdir()) / f"ai_video_transcriber_{site}_cookies.txt"
        cookie_path.write_text("\n".join(cookie_lines) + "\n", encoding="utf-8")
        try:
            cookie_path.chmod(0o600)
        except Exception:
            pass
        return str(cookie_path)

    def _platform_ydl_opts(self, site: str, platform_cookies: Optional[dict] = None) -> dict:
        cookiefile = self._cookiefile_from_env(site, platform_cookies)
        headers = self._platform_headers(site, platform_cookies)
        if cookiefile:
            headers.pop("Cookie", None)
        opts = {
            "impersonate": YT_DLP_IMPERSONATE_TARGET,
            "http_headers": headers,
        }
        if cookiefile:
            opts["cookiefile"] = cookiefile
        return opts

    def _platform_cookie_hint(self, site: str) -> str:
        if site == "douyin":
            return (
                "Douyin requires fresh cookies for this video. Open douyin.com in a browser, "
                "copy the request Cookie header or document.cookie, then paste it into Settings > Platform Cookies."
            )
        if site == "bilibili":
            return "Bilibili requires cookies for this video. Paste the cookie into Settings > Platform Cookies."
        return ""

    async def _resolve_share_url(self, raw_url: str, site: str, platform_cookies: Optional[dict] = None) -> str:
        url = self._extract_video_url(raw_url)
        if site not in {"douyin", "bilibili"}:
            return url

        host = (urlparse(url).netloc or "").lower()
        if not (host == "v.douyin.com" or host == "b23.tv"):
            return url

        headers = self._platform_headers(site, platform_cookies)

        def _resolve() -> str:
            req = Request(url, headers=headers, method="GET")
            with urlopen(req, timeout=15) as resp:
                return resp.geturl() or url

        try:
            resolved = await asyncio.to_thread(_resolve)
            logger.info(f"短链接已解析: {url} -> {resolved}")
            return resolved
        except Exception as e:
            logger.warning(f"短链接解析失败，继续使用原始链接: {e}")
            return url

    def _api_base_url(self) -> str:
        return (
            os.getenv("DOUYIN_TIKTOK_API_BASE")
            or os.getenv("DOUYIN_TIKTOK_DOWNLOAD_API_BASE")
            or ""
        ).strip().rstrip("/")

    def _guess_media_ext(self, url: str, content_type: str) -> str:
        parsed_ext = Path(urlparse(url).path).suffix.lower()
        if parsed_ext in {".mp4", ".m4a", ".mp3", ".webm", ".mkv", ".flv"}:
            return parsed_ext
        if "mp4" in content_type:
            return ".mp4"
        if "mpeg" in content_type or "mp3" in content_type:
            return ".mp3"
        if "webm" in content_type:
            return ".webm"
        return ".mp4"

    def _find_media_url(self, value) -> Optional[str]:
        if isinstance(value, str):
            if value.startswith(("http://", "https://")) and re.search(
                r"(\.mp4|\.m4a|\.mp3|\.webm|play|download|video)", value, re.I
            ):
                return value
            return None
        if isinstance(value, list):
            for item in value:
                found = self._find_media_url(item)
                if found:
                    return found
            return None
        if isinstance(value, dict):
            priority = (
                "nwm_video_url",
                "video_url",
                "download_url",
                "play_url",
                "play_addr",
                "download_addr",
                "url",
            )
            for key in priority:
                if key in value:
                    found = self._find_media_url(value[key])
                    if found:
                        return found
            for item in value.values():
                found = self._find_media_url(item)
                if found:
                    return found
        return None

    async def _download_direct_url(
        self,
        media_url: str,
        output_dir: Path,
        unique_id: str,
        site: str,
        platform_cookies: Optional[dict] = None,
    ) -> Path:
        headers = self._platform_headers(site, platform_cookies)

        def _download() -> Path:
            req = Request(media_url, headers=headers, method="GET")
            with urlopen(req, timeout=120) as resp:
                content_type = resp.headers.get("Content-Type", "")
                ext = self._guess_media_ext(resp.geturl() or media_url, content_type)
                out_path = output_dir / f"audio_{unique_id}_api{ext}"
                with open(out_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
                return out_path

        return await asyncio.to_thread(_download)

    async def _download_from_platform_api(
        self,
        url: str,
        output_dir: Path,
        unique_id: str,
        site: str,
        platform_cookies: Optional[dict] = None,
    ) -> Optional[Path]:
        base_url = self._api_base_url()
        if not base_url or site not in {"douyin", "bilibili", "tiktok"}:
            return None

        api_url = f"{base_url}/api/download?{urlencode({'url': url, 'prefix': 'true', 'with_watermark': 'false'})}"
        headers = self._platform_headers(site, platform_cookies)

        def _request():
            req = Request(api_url, headers=headers, method="GET")
            with urlopen(req, timeout=180) as resp:
                content_type = resp.headers.get("Content-Type", "")
                data = resp.read()
                return resp.geturl() or api_url, content_type, data

        try:
            final_url, content_type, data = await asyncio.to_thread(_request)
        except Exception as e:
            logger.warning(f"平台下载 API 请求失败: {e}")
            return None

        if "json" in content_type.lower() or data[:1] in {b"{", b"["}:
            try:
                payload = json.loads(data.decode("utf-8"))
            except Exception as e:
                logger.warning(f"平台下载 API JSON 解析失败: {e}")
                return None

            media_url = self._find_media_url(payload)
            if not media_url:
                logger.warning("平台下载 API 未返回可用媒体链接")
                return None
            return await self._download_direct_url(media_url, output_dir, unique_id, site, platform_cookies)

        ext = self._guess_media_ext(final_url, content_type)
        out_path = output_dir / f"audio_{unique_id}_api{ext}"
        await asyncio.to_thread(out_path.write_bytes, data)
        return out_path
    
    async def fetch_subtitles(
        self,
        url: str,
        output_dir: Path,
        platform_cookies: Optional[dict] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        先尝试从平台获取字幕文本，比下载音频快得多。

        Returns:
            (subtitle_markdown, video_title, language_code)
            subtitle_markdown 为 None 表示无可用字幕。
        """
        import asyncio

        output_dir.mkdir(exist_ok=True)
        unique_id = str(uuid.uuid4())[:8]
        sub_dir = output_dir / f"subs_{unique_id}"
        site = self._site_key(self._extract_video_url(url))
        url = await self._resolve_share_url(url, site, platform_cookies)

        try:
            # 1. 快速探测：获取视频信息和字幕可用性，不下载任何内容
            check_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                **self._platform_ydl_opts(site, platform_cookies),
            }
            with yt_dlp.YoutubeDL(check_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, False)

            video_title = info.get("title", "unknown")
            manual_subs: dict = info.get("subtitles") or {}
            auto_caps: dict = info.get("automatic_captions") or {}

            # 过滤掉 live_chat 等非语音轨道
            manual_langs = [k for k in manual_subs if not k.startswith("live_chat")]
            auto_langs = [k for k in auto_caps if not k.startswith("live_chat")]

            if not manual_langs and not auto_langs:
                logger.info(f"视频无可用字幕: {url}")
                return None, video_title, None

            # 优先手动字幕，其次自动字幕
            prefer_manual = bool(manual_langs)
            candidate_langs = manual_langs if prefer_manual else auto_langs

            # 按优先级选语言：英语 > 简体中文 > 繁体中文 > 其他（取第一个）
            _priority = ["en", "en-orig", "zh-Hans", "zh-Hant", "zh", "ja", "ko", "fr", "de", "es"]
            prefer_lang = next(
                (lang for lang in _priority if lang in candidate_langs),
                candidate_langs[0],
            )
            logger.info(
                f"发现{'手动' if prefer_manual else '自动'}字幕，选用语言: {prefer_lang}"
                f"（候选 {len(candidate_langs)} 种）"
            )

            # 2. 仅下载字幕，跳过音视频
            sub_dir.mkdir(exist_ok=True)
            dl_opts = {
                "writesubtitles": prefer_manual,
                "writeautomaticsub": not prefer_manual,
                "subtitlesformat": "vtt/srt/best",
                "subtitleslangs": [prefer_lang],
                "skip_download": True,
                "outtmpl": str(sub_dir / "sub.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                **self._platform_ydl_opts(site, platform_cookies),
            }
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                await asyncio.to_thread(ydl.download, [url])

            # 3. 查找下载的字幕文件
            sub_files = list(sub_dir.glob("*.vtt")) + list(sub_dir.glob("*.srt"))
            if not sub_files:
                logger.warning("字幕下载后未找到文件，回退音频模式")
                return None, video_title, None

            sub_file = sub_files[0]

            # 从文件名提取语言代码 (e.g. sub.en.vtt → en)
            stem_parts = sub_file.stem.split(".")
            file_lang = stem_parts[-1] if len(stem_parts) > 1 else prefer_lang

            # 4. 解析字幕文件
            if sub_file.suffix == ".vtt":
                entries = self._parse_vtt(str(sub_file))
            else:
                entries = self._parse_srt(str(sub_file))

            if not entries:
                logger.warning("字幕解析结果为空，回退音频模式")
                return None, video_title, None

            # 5. 格式化为与 Whisper 输出兼容的 Markdown
            formatted = self._format_subtitle_entries(entries, file_lang)
            logger.info(f"字幕获取成功: lang={file_lang}, {len(entries)} 条目")
            return formatted, video_title, file_lang

        except Exception as e:
            logger.warning(f"字幕获取失败（将回退至音频下载）: {e}")
            return None, None, None
        finally:
            if sub_dir.exists():
                try:
                    shutil.rmtree(str(sub_dir))
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 字幕解析辅助方法
    # ------------------------------------------------------------------

    def _parse_vtt(self, filepath: str) -> list:
        """解析 WebVTT 字幕文件，返回去重后的条目列表。

        特别处理 YouTube 自动字幕的「滚动追加」格式：
        同一句话会被分成多个 cue 逐字追加，只保留每组的「最终版本」。
        """
        raw_entries = []
        seen_texts: set = set()

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"读取 VTT 文件失败: {e}")
            return []

        # 移除 WEBVTT 文件头，按空行分割 cue 块
        content = re.sub(r"^WEBVTT[^\n]*\n", "", content)
        blocks = re.split(r"\n{2,}", content.strip())

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            lines = block.split("\n")
            timing_idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if timing_idx < 0:
                continue

            timing_line = lines[timing_idx]
            text_lines = lines[timing_idx + 1:]

            match = re.match(
                r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)\s*-->\s*"
                r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)",
                timing_line,
            )
            if not match:
                continue

            start_str = self._normalize_time(match.group(1))
            end_str = self._normalize_time(match.group(2))

            raw_text = " ".join(text_lines)
            # 去除 HTML / VTT 内联标签（包括 YouTube 逐字时间码标签）
            text = re.sub(r"<[^>]+>", "", raw_text)
            text = (
                text.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&nbsp;", " ")
                    .replace("&#39;", "'")
                    .replace("&quot;", '"')
                    .strip()
            )
            # 合并行内多余空白
            text = re.sub(r"\s+", " ", text).strip()

            if not text or text in seen_texts:
                continue

            seen_texts.add(text)
            raw_entries.append({"start": start_str, "end": end_str, "text": text})

        # ── 二次去重：过滤 YouTube「滚动追加」的中间状态 ──────────────────
        # 若条目 i 的文本是条目 i+1 文本的起始子串，则条目 i 是中间状态，丢弃。
        # 同时丢弃纯空白/单字符的噪音条目。
        if not raw_entries:
            return []

        entries = []
        for i, entry in enumerate(raw_entries):
            text = entry["text"]
            if len(text) < 2:
                continue
            # 检查后续若干条是否以当前文本开头（滚动追加的特征）
            is_intermediate = False
            for j in range(i + 1, min(i + 4, len(raw_entries))):
                next_text = raw_entries[j]["text"]
                if next_text.startswith(text) and len(next_text) > len(text):
                    is_intermediate = True
                    break
            if not is_intermediate:
                entries.append(entry)

        return entries

    def _parse_srt(self, filepath: str) -> list:
        """解析 SRT 字幕文件，返回去重后的条目列表。"""
        entries = []
        seen_texts: set = set()

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"读取 SRT 文件失败: {e}")
            return []

        blocks = re.split(r"\n{2,}", content.strip())

        for block in blocks:
            lines = block.strip().split("\n")
            timing_idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if timing_idx < 0:
                continue

            timing_line = lines[timing_idx]
            text_lines = lines[timing_idx + 1:]

            match = re.match(
                r"(\d{1,2}:\d{2}:\d{2}[.,]\d+)\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d+)",
                timing_line,
            )
            if not match:
                continue

            start_str = self._normalize_time(match.group(1))
            end_str = self._normalize_time(match.group(2))

            text = " ".join(text_lines)
            text = re.sub(r"<[^>]+>", "", text).strip()

            if not text or text in seen_texts:
                continue

            seen_texts.add(text)
            entries.append({"start": start_str, "end": end_str, "text": text})

        return entries

    def _normalize_time(self, time_str: str) -> str:
        """将 HH:MM:SS.mmm 或 MM:SS.mmm 统一为 HH:MM:SS / MM:SS 格式。

        不要把小时折进分钟：折算后 1:40:00 会变成 "100:00"，超出下游
        时间戳正则的分钟位宽，整条字幕会被静默丢弃。
        """
        time_str = re.sub(r"[.,]\d+$", "", time_str)
        parts = time_str.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            if h:
                return f"{h:02d}:{m:02d}:{s:02d}"
            return f"{m:02d}:{s:02d}"
        elif len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return f"{m:02d}:{s:02d}"
        return time_str

    def _format_subtitle_entries(self, entries: list, language: str) -> str:
        """将字幕条目格式化为与 Whisper 输出兼容的 Markdown，供下游管道直接使用。"""
        lines = [
            "# Video Transcription",
            "",
            f"**Detected Language:** {language}",
            "**Language Probability:** 1.00",
            "",
            "## Transcription Content",
            "",
        ]
        for entry in entries:
            lines.append(f"**[{entry['start']} - {entry['end']}]**")
            lines.append("")
            lines.append(entry["text"])
            lines.append("")
        return "\n".join(lines)

    async def download_and_convert(
        self,
        url: str,
        output_dir: Path,
        prefetched_title: Optional[str] = None,
        platform_cookies: Optional[dict] = None,
    ) -> tuple[str, str, Optional[str]]:
        """
        下载视频并转换为m4a格式。

        prefetched_title: 若调用方已通过 fetch_subtitles 探测过视频信息，
        可直接传入视频标题，跳过重复的 extract_info 网络请求。
        """
        try:
            # 创建输出目录
            output_dir.mkdir(exist_ok=True)
            site = self._site_key(self._extract_video_url(url))
            url = await self._resolve_share_url(url, site, platform_cookies)
            
            # 生成唯一的文件名
            unique_id = str(uuid.uuid4())[:8]
            output_template = str(output_dir / f"audio_{unique_id}.%(ext)s")
            
            # Let yt-dlp only download the source media. Some platforms, notably
            # TikTok, can fail inside yt-dlp's FFmpegExtractAudio postprocessor
            # when ffprobe cannot identify the intermediate file's codec.
            # Running our own explicit ffmpeg conversion below is more tolerant.
            ydl_opts = {
                "outtmpl": output_template,
                "prefer_ffmpeg": True,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                **self._platform_ydl_opts(site, platform_cookies),
            }
            
            logger.info(f"开始下载视频: {url}")
            
            import asyncio
            info = None
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if prefetched_title:
                    # 标题和时长已在 fetch_subtitles 中获取，直接下载，跳过重复探测
                    video_title = prefetched_title
                    expected_duration = 0
                    logger.info(f"复用预取标题，跳过 extract_info: {video_title}")
                else:
                    # 获取视频信息（放到线程池避免阻塞事件循环）
                    video_title = "unknown"
                    expected_duration = 0
                    last_info_error = None
                    for attempt in range(3):
                        try:
                            info = await asyncio.to_thread(ydl.extract_info, url, False)
                            video_title = info.get('title', 'unknown')
                            expected_duration = info.get('duration') or 0
                            logger.info(f"视频标题: {video_title}")
                            break
                        except Exception as e:
                            last_info_error = e
                            await asyncio.sleep(0.4 * (attempt + 1))
                    if info is None:
                        logger.warning(f"视频信息获取失败，继续尝试直接下载: {last_info_error}")
                
            format_candidates = []
            if info:
                audio_formats = [
                    f for f in (info.get("formats") or [])
                    if f.get("format_id") and f.get("acodec") not in (None, "none")
                ]
                audio_formats.sort(
                    key=lambda f: (
                        0 if (f.get("vcodec") or "").lower() in ("h264", "avc1") else 1,
                        f.get("filesize") or f.get("filesize_approx") or 10**12,
                    )
                )
                format_candidates.extend(f["format_id"] for f in audio_formats[:4])

            format_candidates.extend([
                "download",
                "best[acodec!=none][vcodec=h264]/best[acodec!=none][vcodec^=avc1]/best[acodec!=none][vcodec!=none]",
                "bestaudio/best[acodec!=none]",
            ])

            source_file = None
            last_error = None
            for fmt in format_candidates:
                for old_file in output_dir.glob(f"audio_{unique_id}.*"):
                    if old_file.is_file():
                        try:
                            old_file.unlink()
                        except Exception:
                            pass

                attempt_opts = ydl_opts.copy()
                attempt_opts["format"] = fmt
                try:
                    with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                        await asyncio.to_thread(ydl.download, [url])
                except Exception as e:
                    last_error = e
                    logger.warning(f"下载格式失败，尝试下一个格式: format={fmt}, error={e}")
                    continue

                downloaded_files = [
                    p for p in output_dir.glob(f"audio_{unique_id}.*")
                    if p.is_file() and p.suffix not in {".part", ".ytdl"}
                ]
                if not downloaded_files:
                    last_error = Exception("未找到下载的音频文件")
                    continue

                candidate_file = downloaded_files[0]
                if self._has_audio_stream(candidate_file):
                    source_file = candidate_file
                    break

                last_error = Exception(f"下载文件不包含音频流: {candidate_file.name}")
                logger.warning(str(last_error))

            if source_file is None:
                api_source = await self._download_from_platform_api(
                    url, output_dir, unique_id, site, platform_cookies
                )
                if api_source and self._has_audio_stream(api_source):
                    source_file = api_source
                elif api_source and self._has_video_stream(api_source):
                    source_file = api_source
                else:
                    error_text = str(last_error or "")
                    if site in {"douyin", "bilibili"} and "cookie" in error_text.lower():
                        hint = self._platform_cookie_hint(site)
                        raise Exception(f"{error_text}. {hint}")
                    raise last_error or Exception("未找到可用音频流")

            audio_file = str(output_dir / f"audio_{unique_id}.m4a")
            convert_target = Path(audio_file)
            if source_file.resolve() == convert_target.resolve():
                convert_target = output_dir / f"audio_{unique_id}_converted.m4a"

            convert_cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-i", str(source_file.resolve()),
                "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(convert_target.resolve()),
            ]
            try:
                result = subprocess.run(convert_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if result.returncode != 0:
                    err = (result.stderr or result.stdout or "").strip()
                    raise Exception(err[:800])
                audio_file = str(convert_target)
            except Exception as e:
                raise Exception(f"FFmpeg 转换失败: {e}")
            
            # 校验时长，如果和源视频差异较大，尝试一次ffmpeg规范化重封装
            actual_duration = self._probe_duration(Path(audio_file))
            
            if expected_duration and actual_duration and abs(actual_duration - expected_duration) / expected_duration > 0.1:
                logger.warning(
                    f"音频时长异常，期望{expected_duration}s，实际{actual_duration}s，尝试重封装修复…"
                )
                try:
                    fixed_path = output_dir / f"audio_{unique_id}_fixed.m4a"
                    fix_cmd = [
                        "ffmpeg", "-y", "-nostdin",
                        "-i", str(Path(audio_file).resolve()),
                        "-vn", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                        str(fixed_path.resolve()),
                    ]
                    result = subprocess.run(
                        fix_cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if result.returncode != 0 or not fixed_path.exists():
                        err = (result.stderr or result.stdout or "").strip()
                        raise RuntimeError(err[-800:] or "FFmpeg remux did not create an output file")
                    # 用修复后的文件替换
                    audio_file = str(fixed_path)
                    # 重新探测
                    actual_duration2 = self._probe_duration(fixed_path)
                    logger.info(f"重封装完成，新时长≈{actual_duration2:.2f}s")
                except Exception as e:
                    logger.error(f"重封装失败：{e}")
            
            source_media_file = str(source_file) if self._has_video_stream(source_file) else audio_file
            logger.info(f"音频文件已保存: {audio_file}")
            logger.info(f"媒体文件已保存: {source_media_file}")
            return audio_file, video_title, source_media_file
            
        except Exception as e:
            logger.error(f"下载视频失败: {str(e)}")
            raise Exception(f"下载视频失败: {str(e)}")

    async def download_source_video_only(
        self,
        url: str,
        output_dir: Path,
        platform_cookies: Optional[dict] = None,
    ) -> tuple[str, str]:
        """Download the source video without extracting audio or post-processing for transcription."""
        try:
            output_dir.mkdir(exist_ok=True)
            site = self._site_key(self._extract_video_url(url))
            url = await self._resolve_share_url(url, site, platform_cookies)

            unique_id = str(uuid.uuid4())[:8]
            output_template = str(output_dir / f"download_{unique_id}.%(ext)s")
            ydl_opts = {
                "outtmpl": output_template,
                "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "impersonate": YT_DLP_IMPERSONATE_TARGET,
                **self._platform_ydl_opts(site, platform_cookies),
            }

            logger.info(f"开始仅下载视频: {url}")

            info = None
            video_title = "unknown"
            last_info_error = None
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                for attempt in range(3):
                    try:
                        info = await asyncio.to_thread(ydl.extract_info, url, False)
                        video_title = info.get("title", "unknown")
                        logger.info(f"视频标题: {video_title}")
                        break
                    except Exception as e:
                        last_info_error = e
                        await asyncio.sleep(0.4 * (attempt + 1))
            if info is None:
                logger.warning(f"视频信息获取失败，继续尝试直接下载: {last_info_error}")

            format_candidates = []
            if info:
                video_formats = [
                    f for f in (info.get("formats") or [])
                    if f.get("format_id") and f.get("vcodec") not in (None, "none")
                ]
                video_formats.sort(
                    key=lambda f: (
                        0 if f.get("acodec") not in (None, "none") else 1,
                        0 if (f.get("vcodec") or "").lower().startswith(("h264", "avc1")) else 1,
                        -(f.get("height") or 0),
                        f.get("filesize") or f.get("filesize_approx") or 10**12,
                    )
                )
                format_candidates.extend(f["format_id"] for f in video_formats[:4])

            format_candidates.extend([
                "download",
                "best[acodec!=none][vcodec!=none]",
                "bestvideo*+bestaudio/best[acodec!=none][vcodec!=none]/best",
            ])

            source_file = None
            last_error = None
            for fmt in dict.fromkeys(format_candidates):
                for old_file in output_dir.glob(f"download_{unique_id}.*"):
                    if old_file.is_file():
                        try:
                            old_file.unlink()
                        except Exception:
                            pass

                attempt_opts = ydl_opts.copy()
                attempt_opts["format"] = fmt
                try:
                    with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                        await asyncio.to_thread(ydl.download, [url])
                except Exception as e:
                    last_error = e
                    logger.warning(f"视频下载格式失败，尝试下一个格式: format={fmt}, error={e}")
                    continue

                downloaded_files = [
                    p for p in output_dir.glob(f"download_{unique_id}.*")
                    if p.is_file() and p.suffix not in {".part", ".ytdl"}
                ]
                video_files = [p for p in downloaded_files if self._has_video_stream(p)]
                if video_files:
                    source_file = max(video_files, key=lambda p: p.stat().st_size)
                    break

                last_error = Exception("Downloaded media does not contain a video stream")
                logger.warning(str(last_error))

            if source_file is None:
                api_source = await self._download_from_platform_api(
                    url, output_dir, unique_id, site, platform_cookies
                )
                if api_source and self._has_video_stream(api_source):
                    source_file = api_source
                else:
                    error_text = str(last_error or "")
                    if site in {"douyin", "bilibili"} and "cookie" in error_text.lower():
                        hint = self._platform_cookie_hint(site)
                        raise Exception(f"{error_text}. {hint}")
                    raise last_error or Exception("未找到可用视频流")

            logger.info(f"仅下载视频已保存: {source_file}")
            return video_title, str(source_file)

        except Exception as e:
            logger.error(f"仅下载视频失败: {str(e)}")
            raise Exception(f"下载视频失败: {str(e)}")
