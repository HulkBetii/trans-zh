import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
TEST_TEMP = tempfile.TemporaryDirectory(prefix="uodate api smoke ")
os.environ["APP_TEMP_DIR"] = TEST_TEMP.name

import main as backend_main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class ApiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(backend_main.app)
        cls.client.__enter__()
        cls.temp = Path(TEST_TEMP.name)
        cls.task_id = "smoke-task"
        cls.media_name = "video co dau 中文.mp4"
        cls.media_path = cls.temp / cls.media_name
        command = [
            "ffmpeg", "-y", "-nostdin",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1.5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(cls.media_path),
        ]
        subprocess.run(command, check=True, capture_output=True)
        backend_main.tasks.clear()
        backend_main.tasks[cls.task_id] = {
            "task_id": cls.task_id,
            "status": "completed",
            "progress": 100,
            "video_title": "Smoke Test",
            "safe_title": "smoke_test",
            "short_id": "smoke1",
            "media_filename": cls.media_name,
            "media_url": f"/api/media/{cls.media_name}",
            "script": "**[00:00.00 - 00:01.20]**\n\nXin chao Viet Nam 中文",
            "timed_transcript": "**[00:00.00 - 00:01.20]**\n\nXin chao Viet Nam 中文",
            "summary": "Safe summary",
            "settings": {},
        }
        backend_main.save_tasks(backend_main.tasks)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        TEST_TEMP.cleanup()

    def test_root_and_health(self):
        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertIn("AI Video Transcriber", root.text)
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        payload = health.json()
        self.assertIn(payload["status"], {"ok", "degraded"})
        self.assertTrue(payload["ffmpeg"]["available"])
        self.assertTrue(payload["ffprobe"]["available"])
        self.assertNotIn("api_key", json.dumps(payload).lower())

    def test_task_settings_redact_credentials(self):
        payload = {
            "ai": {"apiKey": "do-not-save", "baseUrl": "http://127.0.0.1:11434/v1", "model": "test"},
            "sources": {"douyinCookie": "cookie-a", "bilibiliCookie": "cookie-b"},
            "tts": {"elevenLabsApiKey": "eleven", "fptApiKey": "fpt", "provider": "vieneu"},
        }
        response = self.client.put(f"/api/task-settings/{self.task_id}", json=payload)
        self.assertEqual(response.status_code, 200)
        serialized = json.dumps(response.json())
        for secret in ("do-not-save", "cookie-a", "cookie-b", "eleven", "fpt"):
            self.assertNotIn(secret, serialized)
        loaded = self.client.get(f"/api/task-settings/{self.task_id}")
        self.assertEqual(loaded.json()["settings"]["ai"]["baseUrl"], "http://127.0.0.1:11434/v1")

    def test_media_range_request(self):
        response = self.client.get(
            f"/api/media/{self.media_name}",
            headers={"Range": "bytes=0-99"},
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 100)
        self.assertTrue(response.headers["content-range"].startswith("bytes 0-99/"))

    def test_auth_is_off_by_default(self):
        # APP_AUTH_TOKEN unset must leave local usage completely unchanged.
        self.assertEqual(backend_main.APP_AUTH_TOKEN, "")
        self.assertEqual(self.client.get("/api/tasks/recent").status_code, 200)

    def test_auth_token_gates_api_but_not_health(self):
        with mock.patch.object(backend_main, "APP_AUTH_TOKEN", "secret-token"):
            self.assertEqual(self.client.get("/api/health").status_code, 200)
            self.assertEqual(self.client.get("/api/tasks/recent").status_code, 401)
            self.assertEqual(
                self.client.get("/api/tasks/recent", headers={"X-API-Token": "wrong"}).status_code, 401
            )
            self.assertEqual(
                self.client.get("/api/tasks/recent", headers={"X-API-Token": "secret-token"}).status_code, 200
            )
            self.assertEqual(
                self.client.get("/api/tasks/recent", headers={"Authorization": "Bearer secret-token"}).status_code,
                200,
            )
            # Browser-native loads (<audio src>, download links) cannot set headers.
            self.assertEqual(self.client.get(f"/api/media/{self.media_name}").status_code, 401)
            self.assertEqual(
                self.client.get(f"/api/media/{self.media_name}?token=secret-token").status_code, 200
            )

    def test_subtitle_and_video_export(self):
        subtitle = self.client.get(f"/api/export-subtitles/{self.task_id}?format=srt&source=transcript")
        self.assertEqual(subtitle.status_code, 200)
        self.assertIn("Xin chao Viet Nam", subtitle.text)

        exported = self.client.post(
            f"/api/export-video/{self.task_id}",
            data={
                "subtitle_mode": "single",
                "subtitle_source": "transcript",
                "font_size": "24",
                "position": "bottom",
            },
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        result = exported.json()
        output = self.temp / result["filename"]
        self.assertTrue(output.exists())
        self.assertGreater(output.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
