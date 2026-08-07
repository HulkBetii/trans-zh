import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Optional
from urllib import error, request

logger = logging.getLogger(__name__)


class VieNeuTTS:
    """Small lazy wrapper around VieNeu-TTS.

    The import and model initialization are delayed until the first dub export,
    so normal transcription/export flows do not pay the TTS startup cost.
    """

    def __init__(self) -> None:
        self._engine = None

    def _load_engine(self):
        if self._engine is not None:
            return self._engine

        try:
            from vieneu import Vieneu
        except Exception as exc:
            raise RuntimeError(
                "VieNeu-TTS is not installed. Rebuild the app image after installing "
                "the torch-free ONNX VieNeu-TTS dependency."
            ) from exc

        mode = os.getenv("VIENEU_MODE", "v3turbo")
        backend = os.getenv("VIENEU_BACKEND", "onnx")
        precision = os.getenv("VIENEU_PRECISION", "int8")
        kwargs = {"mode": mode, "backend": backend, "precision": precision}

        try:
            self._engine = Vieneu(**kwargs)
        except TypeError:
            # Older releases may not accept keyword selection; use their default.
            self._engine = Vieneu()

        logger.info("VieNeu-TTS engine loaded")
        return self._engine

    def _synthesize_sync(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        style: Optional[str] = None,
        ref_audio: Optional[str] = None,
        denoise: bool = True,
    ) -> None:
        engine = self._load_engine()
        voice_name = voice or os.getenv("VIENEU_VOICE", "").strip() or None
        style_name = style or os.getenv("VIENEU_STYLE", "tu_nhien").strip() or None

        infer_kwargs = {}
        if voice_name:
            infer_kwargs["voice"] = voice_name
        if style_name:
            infer_kwargs["style"] = style_name
        if ref_audio:
            infer_kwargs["ref_audio"] = ref_audio
            infer_kwargs["denoise"] = denoise

        try:
            audio = engine.infer(text, **infer_kwargs)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            engine.save(audio, str(output_path))
        except ModuleNotFoundError as exc:
            if exc.name in {"torch", "torchaudio"}:
                raise RuntimeError(
                    "VieNeu clone voice requires torch and torchaudio. Rebuild the app image after installing clone dependencies."
                ) from exc
            raise
        except Exception as exc:
            raise RuntimeError(f"VieNeu-TTS failed: {exc}") from exc

    async def synthesize_to_file(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        style: Optional[str] = None,
        ref_audio: Optional[str] = None,
        denoise: bool = True,
    ) -> None:
        await asyncio.to_thread(
            self._synthesize_sync,
            text,
            output_path,
            voice,
            style,
            ref_audio,
            denoise,
        )


class ElevenLabsTTS:
    """Small ElevenLabs TTS client.

    The API returns compressed audio by default; this wrapper converts it to
    WAV so the existing ffmpeg alignment/mixing pipeline stays unchanged.
    """

    def _synthesize_sync(
        self,
        text: str,
        output_path: Path,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        effective_key = (api_key or "").strip() or os.getenv("ELEVENLABS_API_KEY", "").strip()
        effective_voice = (voice_id or "").strip() or os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        effective_model = (model_id or "").strip() or os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
        if not effective_key:
            raise RuntimeError("ElevenLabs API key is missing")
        if not effective_voice:
            raise RuntimeError("ElevenLabs voice ID is missing")

        url = (
            "https://api.elevenlabs.io/v1/text-to-speech/"
            f"{effective_voice}?output_format=mp3_44100_128"
        )
        payload = json.dumps({
            "text": text,
            "model_id": effective_model,
        }).encode("utf-8")
        req = request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "xi-api-key": effective_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_mp3 = output_path.with_suffix(".elevenlabs.mp3")
        try:
            with request.urlopen(req, timeout=120) as resp:
                tmp_mp3.write_bytes(resp.read())
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ElevenLabs TTS failed: HTTP {exc.code} {detail[:500]}") from exc
        except Exception as exc:
            raise RuntimeError(f"ElevenLabs TTS failed: {exc}") from exc

        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(tmp_mp3),
            "-ar", "48000",
            "-ac", "1",
            str(output_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        tmp_mp3.unlink(missing_ok=True)
        if result.returncode != 0 or not output_path.exists():
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Convert ElevenLabs audio failed: {err[-800:]}")

    async def synthesize_to_file(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        style: Optional[str] = None,
        ref_audio: Optional[str] = None,
        denoise: bool = True,
        api_key: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._synthesize_sync,
            text,
            output_path,
            voice,
            model_id,
            api_key,
        )


class FptTTS:
    """FPT.AI text-to-speech client.

    FPT returns an async audio URL; this client polls that URL and converts the
    downloaded audio to WAV for the existing dub pipeline.
    """

    def _synthesize_sync(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        speed: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        effective_key = (api_key or "").strip() or os.getenv("FPT_API_KEY", "").strip()
        effective_voice = (voice or "").strip() or os.getenv("FPT_VOICE", "banmai").strip()
        effective_speed = (speed or "").strip() or os.getenv("FPT_SPEED", "0").strip()
        if not effective_key:
            raise RuntimeError("FPT API key is missing")
        if not effective_voice:
            raise RuntimeError("FPT voice is missing")

        req = request.Request(
            "https://api.fpt.ai/hmi/tts/v5",
            data=(text or "").encode("utf-8"),
            method="POST",
            headers={
                "api_key": effective_key,
                "voice": effective_voice,
                "speed": effective_speed or "0",
                "format": "mp3",
                "Cache-Control": "no-cache",
            },
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"FPT TTS failed: HTTP {exc.code} {detail[:500]}") from exc
        except Exception as exc:
            raise RuntimeError(f"FPT TTS failed: {exc}") from exc

        if int(payload.get("error", 1) or 1) != 0:
            raise RuntimeError(f"FPT TTS failed: {payload.get('message') or payload}")
        audio_url = str(payload.get("async") or "").strip()
        if not audio_url:
            raise RuntimeError("FPT TTS returned no async audio URL")

        tmp_audio = output_path.with_suffix(".fpt.mp3")
        last_error = ""
        attempts = 24
        for attempt in range(attempts):
            try:
                with request.urlopen(audio_url, timeout=30) as resp:
                    content_type = resp.headers.get("content-type", "")
                    data = resp.read()
                if resp.status == 200 and data and "json" not in content_type.lower():
                    tmp_audio.write_bytes(data)
                    break
                last_error = f"unexpected response content-type={content_type}"
            except error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
            except Exception as exc:
                last_error = str(exc)
            if attempt == attempts - 1:
                break  # no point sleeping after the final attempt
            time.sleep(5 if attempt < 6 else 8)
        if not tmp_audio.exists() or tmp_audio.stat().st_size == 0:
            raise RuntimeError(f"FPT TTS audio was not ready: {last_error}")

        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(tmp_audio),
            "-ar", "48000",
            "-ac", "1",
            str(output_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        tmp_audio.unlink(missing_ok=True)
        if result.returncode != 0 or not output_path.exists():
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Convert FPT audio failed: {err[-800:]}")

    async def synthesize_to_file(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        style: Optional[str] = None,
        ref_audio: Optional[str] = None,
        denoise: bool = True,
        api_key: Optional[str] = None,
        speed: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._synthesize_sync,
            text,
            output_path,
            voice,
            speed,
            api_key,
        )
