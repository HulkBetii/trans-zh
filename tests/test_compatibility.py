import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import transcriber as transcriber_module  # noqa: E402
import video_processor as video_processor_module  # noqa: E402


class VideoCommandCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.processor = video_processor_module.VideoProcessor()

    def test_ffprobe_arguments_preserve_unicode_path(self):
        media = Path(r"C:\Video Co Dau\Tieng Viet 中文\clip test.mp4")
        completed = SimpleNamespace(returncode=0, stdout="12.5\n", stderr="")
        with mock.patch.object(video_processor_module.subprocess, "run", return_value=completed) as run:
            self.assertEqual(self.processor._probe_duration(media), 12.5)
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertEqual(args[0][-1], str(media.resolve()))
        self.assertNotIn("shell", kwargs)

    def test_ffmpeg_arguments_preserve_unicode_path(self):
        with tempfile.TemporaryDirectory(prefix="duong dan co dau ") as tmp:
            root = Path(tmp)
            source = root / "nguon 中文 co dau.mp4"
            source.write_bytes(b"input")

            def fake_run(command, **kwargs):
                self.assertIsInstance(command, list)
                self.assertIn(str(source.resolve()), command)
                self.assertNotIn("shell", kwargs)
                Path(command[-1]).write_bytes(b"output")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.object(video_processor_module.subprocess, "run", side_effect=fake_run):
                output = asyncio.run(self.processor.normalize_local_media_to_m4a(source, root / "dich 中文"))
            self.assertTrue(Path(output).exists())

    def test_backend_has_no_shell_string_subprocess_patterns(self):
        for path in BACKEND.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("shell=True", source, path.name)
            self.assertNotIn("shlex.quote", source, path.name)


class WhisperRuntimeTests(unittest.TestCase):
    def test_cpu_defaults_to_int8_and_uses_model_env(self):
        with mock.patch.dict(os.environ, {
            "WHISPER_MODEL_SIZE": "small",
            "WHISPER_DEVICE": "cpu",
            "WHISPER_COMPUTE_TYPE": "",
        }, clear=False), mock.patch.object(transcriber_module.ctranslate2, "get_cuda_device_count", return_value=1):
            instance = transcriber_module.Transcriber()
            self.assertEqual(instance.model_size, "small")
            self.assertEqual(instance._resolve_runtime(), ("cpu", "int8"))

    def test_cuda_is_opt_in(self):
        with mock.patch.dict(os.environ, {"WHISPER_DEVICE": "cuda", "WHISPER_COMPUTE_TYPE": ""}, clear=False), \
                mock.patch.object(transcriber_module.ctranslate2, "get_cuda_device_count", return_value=1):
            instance = transcriber_module.Transcriber("base")
            self.assertEqual(instance._resolve_runtime(), ("cuda", "float16"))

    def test_cuda_initialization_falls_back_to_cpu(self):
        calls = []

        def fake_model(model_size, device, compute_type):
            calls.append((model_size, device, compute_type))
            if device == "cuda":
                raise RuntimeError("CUDA initialization failed")
            return object()

        with mock.patch.dict(os.environ, {"WHISPER_DEVICE": "cuda", "WHISPER_COMPUTE_TYPE": "float16"}, clear=False), \
                mock.patch.object(transcriber_module.ctranslate2, "get_cuda_device_count", return_value=1), \
                mock.patch.object(transcriber_module, "WhisperModel", side_effect=fake_model):
            instance = transcriber_module.Transcriber("base")
            instance._load_model()
        self.assertEqual(calls, [("base", "cuda", "float16"), ("base", "cpu", "int8")])
        self.assertEqual(instance.active_device, "cpu")
        self.assertEqual(instance.active_compute_type, "int8")


class TaskPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="uodate backend tests ")
        os.environ["APP_TEMP_DIR"] = cls._temp.name
        import main as backend_main
        cls.main = backend_main

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_invalid_integer_env_uses_safe_default(self):
        with mock.patch.dict(os.environ, {"WORKER_THREADS": "not-a-number"}, clear=False):
            self.assertEqual(self.main._env_int("WORKER_THREADS", 8, 1, 32), 8)
        with mock.patch.dict(os.environ, {"UPLOAD_MAX_MB": "999999"}, clear=False):
            self.assertEqual(self.main._env_int("UPLOAD_MAX_MB", 200, 1, 10240), 200)

    def test_atomic_persistence_redacts_secrets_and_recovers_backup(self):
        with tempfile.TemporaryDirectory(prefix="task store 中文 ") as tmp:
            task_file = Path(tmp) / "tasks.json"
            backup_file = Path(tmp) / "tasks.json.bak"
            payload = {
                "task-1": {
                    "status": "completed",
                    "api_key": "legacy-top-level-secret",
                    "settings": {
                        "ai": {"apiKey": "secret-openai", "baseUrl": "http://localhost/v1"},
                        "sources": {"douyinCookie": "secret-douyin", "bilibiliCookie": "secret-bili"},
                        "tts": {"elevenLabsApiKey": "secret-eleven", "fptApiKey": "secret-fpt"},
                    },
                }
            }
            with mock.patch.object(self.main, "TASKS_FILE", task_file), \
                    mock.patch.object(self.main, "TASKS_BACKUP_FILE", backup_file):
                # force=True: plain save_tasks() coalesces rapid writes, and this
                # test asserts on the file contents right away.
                self.main.save_tasks(payload, force=True)
                raw = task_file.read_text(encoding="utf-8")
                for secret in ("legacy-top-level-secret", "secret-openai", "secret-douyin", "secret-bili", "secret-eleven", "secret-fpt"):
                    self.assertNotIn(secret, raw)
                self.assertTrue(backup_file.exists())
                task_file.write_text("{corrupt", encoding="utf-8")
                recovered = self.main.load_tasks()
            self.assertEqual(recovered["task-1"]["status"], "completed")
            self.assertEqual(recovered["task-1"]["settings"]["ai"]["baseUrl"], "http://localhost/v1")


class LongVideoTimestampTests(unittest.TestCase):
    """Regression: cues past 1h40m used to be silently dropped."""

    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="uodate timestamp tests ")
        os.environ["APP_TEMP_DIR"] = cls._temp.name
        import main as backend_main
        cls.main = backend_main
        cls.processor = video_processor_module.VideoProcessor()

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_normalize_time_keeps_hours(self):
        # Folding hours into minutes produced "100:00", which no longer parses.
        self.assertEqual(self.processor._normalize_time("01:40:00.500"), "01:40:00")
        self.assertEqual(self.processor._normalize_time("00:05:03.250"), "05:03")
        self.assertEqual(self.processor._normalize_time("05:03.250"), "05:03")

    def test_extract_timed_cues_parses_three_digit_minutes(self):
        # Task records written before the fix still contain this shape.
        cues = self.main._extract_timed_cues("**[100:00 - 100:03]**\n\nlate cue\n")
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0]["start"], 6000.0)
        self.assertEqual(cues[0]["text"], "late cue")

    def test_extract_timed_cues_parses_hour_form(self):
        cues = self.main._extract_timed_cues("**[01:40:00 - 01:40:03]**\n\nlate cue\n")
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0]["start"], 6000.0)


class ModelBaseUrlValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="uodate ssrf tests ")
        os.environ["APP_TEMP_DIR"] = cls._temp.name
        import main as backend_main
        cls.main = backend_main

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def _validate(self, url):
        return asyncio.run(self.main._validate_model_base_url(url))

    def test_loopback_is_allowed_for_local_llm_runtimes(self):
        # Ollama / LM Studio / llama.cpp must keep working.
        self.assertEqual(self._validate("http://127.0.0.1:11434/v1"), "http://127.0.0.1:11434/v1")
        self.assertEqual(self._validate("http://localhost:1234/v1"), "http://localhost:1234/v1")

    def test_cloud_metadata_and_lan_are_blocked(self):
        from fastapi import HTTPException
        for url in ("http://169.254.169.254/latest/meta-data/", "http://192.168.1.1/", "http://10.0.0.5:8080/v1"):
            with self.subTest(url=url):
                with self.assertRaises(HTTPException) as ctx:
                    self._validate(url)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_non_http_scheme_is_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self._validate("file:///etc/passwd")


class DubAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="uodate dub tests ")
        os.environ["APP_TEMP_DIR"] = cls._temp.name
        import main as backend_main
        cls.main = backend_main

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    THREE_CUES = (
        "**[00:00.00 - 00:02.00]**\n\nAlpha\n\n"
        "**[00:02.00 - 00:04.00]**\n\nBravo\n\n"
        "**[00:04.00 - 00:06.00]**\n\nCharlie\n"
    )

    def test_one_to_one_alignment(self):
        task = {
            "timed_transcript": self.THREE_CUES,
            "timed_translation": (
                "**[00:00.00 - 00:02.00]**\n\nAA\n\n"
                "**[00:02.00 - 00:04.00]**\n\nBB\n\n"
                "**[00:04.00 - 00:06.00]**\n\nCC\n"
            ),
        }
        aligned, translated, missing = self.main._dub_cues_from_original_timing(task, 0.0)
        self.assertEqual([cue["text"] for cue in aligned], ["AA", "BB", "CC"])
        self.assertEqual((translated, missing), (3, 0))

    def test_merged_translation_does_not_shift_later_cues(self):
        # Index pairing put "CC" on the middle cue and left the last one wrong.
        task = {
            "timed_transcript": self.THREE_CUES,
            "timed_translation": "**[00:00.00 - 00:04.00]**\n\nAABB\n\n**[00:04.00 - 00:06.00]**\n\nCC\n",
        }
        aligned, translated, missing = self.main._dub_cues_from_original_timing(task, 0.0)
        self.assertEqual(aligned[2]["text"], "CC")
        # The merged cue is claimed once, so the uncovered cue is reported.
        self.assertEqual((translated, missing), (2, 1))

    def test_translation_cue_is_never_used_twice(self):
        task = {
            "timed_transcript": self.THREE_CUES,
            "timed_translation": "**[00:00.00 - 00:04.00]**\n\nAABB\n",
        }
        aligned, _, _ = self.main._dub_cues_from_original_timing(task, 0.0)
        self.assertEqual(sum(1 for cue in aligned if cue["text"] == "AABB"), 1)


class TaskStoreDebounceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="uodate debounce tests ")
        os.environ["APP_TEMP_DIR"] = cls._temp.name
        import main as backend_main
        cls.main = backend_main

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_rapid_saves_are_coalesced_and_force_writes(self):
        with tempfile.TemporaryDirectory(prefix="debounce store ") as tmp:
            task_file = Path(tmp) / "tasks.json"
            backup_file = Path(tmp) / "tasks.json.bak"
            data = {"t1": {"status": "processing", "progress": 1}}
            with mock.patch.object(self.main, "TASKS_FILE", task_file), \
                    mock.patch.object(self.main, "TASKS_BACKUP_FILE", backup_file), \
                    mock.patch.object(self.main, "TASKS_SAVE_INTERVAL", 60.0), \
                    mock.patch.object(self.main, "_last_save_ts", 0.0):
                self.assertTrue(self.main.save_tasks(data))
                first = task_file.stat().st_mtime_ns

                data["t1"]["progress"] = 2
                self.main.save_tasks(data)
                self.assertEqual(task_file.stat().st_mtime_ns, first, "second write should be debounced")
                self.assertTrue(self.main._tasks_dirty)

                data["t1"]["progress"] = 3
                self.assertTrue(self.main.save_tasks(data, force=True))
                self.assertFalse(self.main._tasks_dirty)
            self.assertEqual(json.loads(task_file.read_text(encoding="utf-8"))["t1"]["progress"], 3)


class OcrSmokeTests(unittest.TestCase):
    def test_tesseract_reads_vietnamese_text(self):
        executable = ROOT / ".runtime" / "tesseract" / "tesseract.exe"
        tessdata = ROOT / ".runtime" / "tessdata"
        if not executable.exists():
            self.skipTest("Portable Tesseract is not installed")
        from PIL import Image, ImageDraw, ImageFont

        with tempfile.TemporaryDirectory(prefix="ocr smoke ") as tmp:
            image_path = Path(tmp) / "tieng-viet.png"
            image = Image.new("RGB", (1500, 260), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype(str(ROOT / "static" / "fonts" / "NotoSans-Regular.ttf"), 96)
            draw.text((40, 55), "Xin ch\u00e0o Vi\u1ec7t Nam", font=font, fill="black")
            image.save(image_path)
            result = __import__("subprocess").run(
                [
                    str(executable), str(image_path), "stdout",
                    "--tessdata-dir", str(tessdata), "-l", "vie", "--psm", "7",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Vi\u1ec7t Nam", result.stdout)


if __name__ == "__main__":
    unittest.main()
