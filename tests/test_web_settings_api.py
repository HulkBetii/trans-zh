from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from zhsub.config import Config
from zhsub.credentials import CredentialStoreError
from zhsub.jobs import JobStore
from zhsub.web.server import create_app


ENV_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AI33_API_KEY")


class FakeCredentialStore:
    def __init__(
        self, secrets: dict[str, str] | None = None, *, available: bool = True
    ) -> None:
        self.secrets = dict(secrets or {})
        self.is_available = available

    def available(self) -> bool:
        return self.is_available

    def get_secret(self, env_name: str) -> str | None:
        return self.secrets.get(env_name)

    def set_secret(self, env_name: str, secret: str) -> None:
        if not self.is_available:
            raise RuntimeError("vault unavailable")
        self.secrets[env_name] = secret

    def delete_secret(self, env_name: str) -> None:
        if not self.is_available:
            raise RuntimeError("vault unavailable")
        self.secrets.pop(env_name, None)


class FailingReadCredentialStore(FakeCredentialStore):
    def get_secret(self, env_name: str) -> str | None:
        raise CredentialStoreError(f"cannot read {env_name}")


class BlockingCredentialStore(FakeCredentialStore):
    def __init__(self) -> None:
        super().__init__()
        self.write_started = threading.Event()
        self.release_write = threading.Event()
        self.concurrent_revision_read = threading.Event()
        self._write_lock = threading.Lock()
        self._first_write = True

    def get_secret(self, env_name: str) -> str | None:
        value = super().get_secret(env_name)
        if (
            env_name == "AI33_API_KEY"
            and self.write_started.is_set()
            and not self.release_write.is_set()
        ):
            self.concurrent_revision_read.set()
        return value

    def set_secret(self, env_name: str, secret: str) -> None:
        with self._write_lock:
            first_write = self._first_write
            self._first_write = False
        if first_write:
            self.write_started.set()
            if not self.release_write.wait(timeout=5):
                raise RuntimeError("timed out waiting to release credential write")
        super().set_secret(env_name, secret)


@pytest.fixture(autouse=True)
def clear_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)


def settings_config(tmp_path: Path) -> Config:
    config = Config()
    config.paths.jobs_db = str(tmp_path / "jobs.db")
    config.paths.work_dir = str(tmp_path / "work")
    config.paths.cache_dir = str(tmp_path / "cache")
    config.llm.segment.provider = "openai"
    config.llm.segment.model = "gpt-segment"
    config.llm.segment.base_url = "https://openai.test/v1"
    config.llm.segment.api_key_env = "OPENAI_API_KEY"
    config.llm.translate.provider = "anthropic"
    config.llm.translate.model = "claude-translate"
    config.llm.translate.base_url = "https://anthropic.test"
    config.llm.translate.api_key_env = "ANTHROPIC_API_KEY"
    config.dub.base_url = "https://ai33.test"
    config.dub.api_key_env = "AI33_API_KEY"
    config.dub.voice_id = "SET_ME"
    return config


def settings_client(
    tmp_path: Path,
    credential_store: FakeCredentialStore,
) -> TestClient:
    return TestClient(
        create_app(
            config=settings_config(tmp_path),
            credential_store=credential_store,
            start_scheduler=False,
            enforce_readiness=False,
            file_roots=[tmp_path],
        )
    )


def credentials_by_id(payload: dict) -> dict[str, dict]:
    return {item["id"]: item for item in payload["credentials"]}


def providers_by_id(payload: dict) -> dict[str, dict]:
    return {item["id"]: item for item in payload["providers"]}


def test_settings_masks_credentials_and_describes_read_only_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_secret = "openai-canary-secret"
    anthropic_secret = "anthropic-canary-secret"
    monkeypatch.setenv("OPENAI_API_KEY", openai_secret)
    vault = FakeCredentialStore({"ANTHROPIC_API_KEY": anthropic_secret})

    with settings_client(tmp_path, vault) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert openai_secret not in response.text
    assert anthropic_secret not in response.text
    payload = response.json()
    credentials = credentials_by_id(payload)
    assert credentials["openai"] == {
        "id": "openai",
        "label": "OpenAI",
        "configured": True,
        "source": "environment",
        "editable": False,
        "masked_value": "********",
        "env_name": "OPENAI_API_KEY",
    }
    assert credentials["anthropic"]["source"] == "credential_store"
    assert credentials["anthropic"]["editable"] is True
    assert credentials["anthropic"]["masked_value"] == "********"
    assert credentials["ai33"]["configured"] is False
    assert credentials["ai33"]["masked_value"] is None

    providers = providers_by_id(payload)
    assert providers["segment"] == {
        "id": "segment",
        "label": "Phân đoạn (S2)",
        "provider": "openai",
        "model": "gpt-segment",
        "base_url": "https://openai.test/v1",
        "credential_id": "openai",
    }
    assert providers["translate"]["credential_id"] == "anthropic"
    assert providers["tts"]["credential_id"] == "ai33"
    assert providers["tts"]["model"] is None


def test_chatgpt_web_provider_never_claims_an_api_credential(tmp_path: Path) -> None:
    config = settings_config(tmp_path)
    config.llm.segment.provider = "chatgpt_web"
    config.llm.segment.api_key_env = "OPENAI_API_KEY"
    vault = FakeCredentialStore()
    with TestClient(
        create_app(
            config=config,
            credential_store=vault,
            start_scheduler=False,
            enforce_readiness=False,
        )
    ) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 200
    assert providers_by_id(response.json())["segment"]["credential_id"] is None


def test_put_delete_and_revision_track_secret_fingerprints(tmp_path: Path) -> None:
    vault = FakeCredentialStore()
    first_secret = "ai33-first-canary"
    second_secret = "ai33-second-canary"

    with settings_client(tmp_path, vault) as client:
        initial = client.get("/api/v1/settings").json()
        first = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": initial["revision"], "secret": first_secret},
        )
        second = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": first.json()["revision"], "secret": second_secret},
        )
        stale = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": initial["revision"], "secret": "unused-canary"},
        )
        deleted = client.request(
            "DELETE",
            "/api/v1/settings/credentials/ai33",
            json={"revision": second.json()["revision"]},
        )

    assert first.status_code == second.status_code == deleted.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first_secret not in first.text
    assert second_secret not in second.text
    assert first.json()["revision"] != second.json()["revision"]
    assert credentials_by_id(second.json())["ai33"]["masked_value"] == "********"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert stale.headers["cache-control"] == "no-store"
    assert credentials_by_id(deleted.json())["ai33"]["configured"] is False
    assert "AI33_API_KEY" not in vault.secrets


def test_environment_managed_credentials_cannot_be_mutated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment_secret = "environment-canary"
    monkeypatch.setenv("AI33_API_KEY", environment_secret)
    vault = FakeCredentialStore()

    with settings_client(tmp_path, vault) as client:
        revision = client.get("/api/v1/settings").json()["revision"]
        response = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": revision, "secret": "replacement-canary"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "action_not_allowed"
    assert response.json()["detail"]["reason"] == "managed_by_environment"
    assert environment_secret not in response.text
    assert vault.secrets == {}


def test_settings_mutations_require_loopback_and_an_idle_scheduler(
    tmp_path: Path,
) -> None:
    vault = FakeCredentialStore()
    with settings_client(tmp_path, vault) as client:
        revision = client.get("/api/v1/settings").json()["revision"]
        invalid_host = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": revision, "secret": "host-canary"},
            headers={"host": "attacker.example"},
        )
        invalid_get = client.get(
            "/api/v1/settings",
            headers={"host": "attacker.example"},
        )
        invalid_origin = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": revision, "secret": "origin-canary"},
            headers={"origin": "https://attacker.example"},
        )
        jobs = JobStore(tmp_path / "jobs.db")
        jobs.upsert("active-job", "movie.mp4", str(tmp_path / "work"), ["vi"])
        active_run = jobs.create_run("active-job", "pipeline", "ingest")
        blocked = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": revision, "secret": "active-canary"},
            headers={"origin": "http://127.0.0.1"},
        )

    assert invalid_host.status_code == 403
    assert invalid_host.json()["detail"]["code"] == "invalid_host"
    assert invalid_get.status_code == 403
    assert invalid_get.json()["detail"]["code"] == "invalid_host"
    assert invalid_origin.status_code == 403
    assert invalid_origin.json()["detail"]["code"] == "invalid_origin"
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {
        "code": "action_not_allowed",
        "message": "Settings cannot change while a run is active",
        "reason": "active_run",
        "run_id": active_run.run_id,
    }
    assert vault.secrets == {}


def test_api_rejects_a_remote_peer_with_a_loopback_host(tmp_path: Path) -> None:
    vault = FakeCredentialStore()
    app = create_app(
        config=settings_config(tmp_path),
        credential_store=vault,
        start_scheduler=False,
        enforce_readiness=False,
        file_roots=[tmp_path],
    )
    with TestClient(app) as local_client:
        revision = local_client.get("/api/v1/settings").json()["revision"]

    with TestClient(
        app,
        base_url="http://localhost",
        client=("203.0.113.9", 50000),
    ) as remote_client:
        responses = [
            remote_client.get("/api/v1/meta"),
            remote_client.get("/api/v1/settings"),
            remote_client.put(
                "/api/v1/settings/credentials/ai33",
                json={"revision": revision, "secret": "remote-canary"},
            ),
            remote_client.post(
                "/api/v1/settings/credentials/ai33/test",
                json={"secret": "remote-test-canary"},
            ),
        ]

    assert all(response.status_code == 403 for response in responses)
    assert all(
        response.json()["detail"]["code"] == "invalid_client" for response in responses
    )
    assert vault.secrets == {}


def test_concurrent_settings_updates_use_one_revision_atomically(
    tmp_path: Path,
) -> None:
    vault = BlockingCredentialStore()
    with settings_client(tmp_path, vault) as client:
        revision = client.get("/api/v1/settings").json()["revision"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                client.put,
                "/api/v1/settings/credentials/ai33",
                json={"revision": revision, "secret": "first-race-canary"},
            )
            assert vault.write_started.wait(timeout=2)
            second = executor.submit(
                client.put,
                "/api/v1/settings/credentials/ai33",
                json={"revision": revision, "secret": "second-race-canary"},
            )
            vault.concurrent_revision_read.wait(timeout=1)
            vault.release_write.set()
            responses = [first.result(timeout=5), second.result(timeout=5)]

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"]["code"] == "revision_conflict"


def test_settings_write_blocks_run_enqueue_until_the_vault_write_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = BlockingCredentialStore()
    app = create_app(
        config=settings_config(tmp_path),
        credential_store=vault,
        start_scheduler=False,
        enforce_readiness=False,
        file_roots=[tmp_path],
    )
    with TestClient(app) as client:
        revision = client.get("/api/v1/settings").json()["revision"]
        runtime = app.state.runtime
        runtime.store.upsert("queued-job", "movie.mp4", str(tmp_path / "work"), ["vi"])
        create_run_called = threading.Event()
        original_create_run = runtime.store.create_run

        def observed_create_run(*args, **kwargs):
            create_run_called.set()
            return original_create_run(*args, **kwargs)

        monkeypatch.setattr(runtime.store, "create_run", observed_create_run)
        with ThreadPoolExecutor(max_workers=2) as executor:
            settings_future = executor.submit(
                client.put,
                "/api/v1/settings/credentials/ai33",
                json={"revision": revision, "secret": "locked-write-canary"},
            )
            assert vault.write_started.wait(timeout=2)
            enqueue_future = executor.submit(
                runtime.enqueue,
                "queued-job",
                "pipeline",
                "ingest",
            )
            try:
                assert not create_run_called.wait(timeout=0.2)
            finally:
                vault.release_write.set()
            settings_response = settings_future.result(timeout=5)
            run = enqueue_future.result(timeout=5)

    assert settings_response.status_code == 200
    assert create_run_called.is_set()
    assert run.status == "queued"


def test_unavailable_credential_store_fails_closed(tmp_path: Path) -> None:
    vault = FakeCredentialStore(available=False)
    with settings_client(tmp_path, vault) as client:
        current = client.get("/api/v1/settings")
        response = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": current.json()["revision"], "secret": "closed-canary"},
        )

    assert current.status_code == 200
    assert current.json()["credential_store_available"] is False
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credential_store_unavailable"
    assert vault.secrets == {}


def test_credential_store_read_failure_returns_503_and_safe_readiness(
    tmp_path: Path,
) -> None:
    vault = FailingReadCredentialStore()
    with settings_client(tmp_path, vault) as client:
        settings = client.get("/api/v1/settings")
        meta = client.get("/api/v1/meta")

    assert settings.status_code == 503
    assert settings.json()["detail"]["code"] == "credential_store_unavailable"
    assert meta.status_code == 200
    readiness = {item["id"]: item["status"] for item in meta.json()["readiness"]}
    assert readiness["llm_segment"] == "blocked"
    assert readiness["llm_translate"] == "blocked"
    assert readiness["tts_ai33"] == "warning"


def test_credential_test_uses_non_paid_endpoints_and_sanitizes_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs) -> httpx.Response:
        calls.append((url, kwargs))
        request = httpx.Request("GET", url)
        if "openai.test" in url:
            return httpx.Response(200, request=request, json={"data": []})
        if "anthropic.test" in url:
            secret = kwargs["headers"]["x-api-key"]
            return httpx.Response(401, request=request, text=f"rejected {secret}")
        secret = kwargs["headers"]["xi-api-key"]
        raise httpx.ConnectError(f"network failure for {secret}", request=request)

    monkeypatch.setattr("zhsub.web.server.httpx.get", fake_get)
    vault = FakeCredentialStore()
    drafts = {
        "openai": "openai-test-canary",
        "anthropic": "anthropic-test-canary",
        "ai33": "ai33-test-canary",
    }
    with settings_client(tmp_path, vault) as client:
        responses = {
            credential_id: client.post(
                f"/api/v1/settings/credentials/{credential_id}/test",
                json={"secret": secret},
            )
            for credential_id, secret in drafts.items()
        }

    assert responses["openai"].json()["code"] == "ok"
    assert responses["anthropic"].json()["code"] == "invalid_credentials"
    assert responses["ai33"].json()["code"] == "provider_unavailable"
    assert all(response.status_code == 200 for response in responses.values())
    assert all(
        response.headers["cache-control"] == "no-store"
        for response in responses.values()
    )
    for secret, response in zip(drafts.values(), responses.values(), strict=True):
        assert secret not in response.text
        assert secret not in caplog.text

    assert calls[0][0] == "https://openai.test/v1/models"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer openai-test-canary"
    assert calls[1][0] == "https://anthropic.test/v1/models"
    assert calls[1][1]["headers"]["anthropic-version"] == "2023-06-01"
    assert calls[2][0] == "https://ai33.test/v3/voices"
    assert calls[2][1]["params"] == {
        "provider": "vbee",
        "language": "Vietnamese",
        "page_size": 1,
    }


def test_credential_test_requires_a_draft_or_current_secret(tmp_path: Path) -> None:
    vault = FakeCredentialStore()
    with settings_client(tmp_path, vault) as client:
        response = client.post(
            "/api/v1/settings/credentials/ai33/test",
            json={},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "credential_not_configured"
    assert response.headers["cache-control"] == "no-store"


def test_saved_credential_survives_a_new_app_instance(tmp_path: Path) -> None:
    vault = FakeCredentialStore()
    saved_secret = "persistent-ai33-canary"
    with settings_client(tmp_path, vault) as first_client:
        initial = first_client.get("/api/v1/settings").json()
        saved = first_client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": initial["revision"], "secret": saved_secret},
        )
        saved_revision = saved.json()["revision"]

    with settings_client(tmp_path, vault) as restarted_client:
        restored = restarted_client.get("/api/v1/settings")
        meta = restarted_client.get("/api/v1/meta")

    assert restored.status_code == 200
    assert restored.json()["revision"] == saved_revision
    assert credentials_by_id(restored.json())["ai33"]["source"] == "credential_store"
    assert saved_secret not in restored.text
    assert "settings" in meta.json()["capabilities"]
    tts_readiness = next(
        item for item in meta.json()["readiness"] if item["id"] == "tts_ai33"
    )
    assert tts_readiness["status"] == "ready"


def test_invalid_secret_is_rejected_without_echoing_it(tmp_path: Path) -> None:
    vault = FakeCredentialStore()
    invalid_secret = "invalid canary secret"
    with settings_client(tmp_path, vault) as client:
        revision = client.get("/api/v1/settings").json()["revision"]
        response = client.put(
            "/api/v1/settings/credentials/ai33",
            json={"revision": revision, "secret": invalid_secret},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_secret"
    assert invalid_secret not in response.text
    assert vault.secrets == {}


def test_create_app_copies_shared_config_before_binding_credentials(
    tmp_path: Path,
) -> None:
    shared_config = settings_config(tmp_path)
    first_vault = FakeCredentialStore({"AI33_API_KEY": "first-app-canary"})
    second_vault = FakeCredentialStore({"AI33_API_KEY": "second-app-canary"})
    first_app = create_app(
        config=shared_config,
        credential_store=first_vault,
        start_scheduler=False,
        enforce_readiness=False,
    )
    second_app = create_app(
        config=shared_config,
        credential_store=second_vault,
        start_scheduler=False,
        enforce_readiness=False,
    )

    with TestClient(first_app) as first_client:
        assert first_client.get("/api/v1/meta").status_code == 200
        assert first_app.state.runtime.config.dub.api_key() == "first-app-canary"
    with TestClient(second_app) as second_client:
        assert second_client.get("/api/v1/meta").status_code == 200
        assert second_app.state.runtime.config.dub.api_key() == "second-app-canary"

    assert first_app.state.runtime.config is not shared_config
    assert second_app.state.runtime.config is not shared_config
    assert first_app.state.runtime.config.dub.api_key() == "first-app-canary"
