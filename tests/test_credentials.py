from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from keyring.errors import KeyringError, PasswordDeleteError

import zhsub.credentials as credentials
from zhsub.config import Config
from zhsub.jobs import JobStore
from zhsub.web.tts_service import TtsService


@dataclass
class FakeCredentialStore:
    values: dict[str, str] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    def get_secret(self, env_name: str) -> str | None:
        self.reads.append(env_name)
        return self.values.get(env_name)

    def set_secret(self, env_name: str, secret: str) -> None:
        self.values[env_name] = secret

    def delete_secret(self, env_name: str) -> None:
        self.values.pop(env_name, None)

    def available(self) -> bool:
        return True


def test_environment_wins_without_reading_the_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeCredentialStore({"OPENAI_API_KEY": "vault-secret"})
    config = Config().bind_credential_store(store)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")

    assert config.llm.segment.api_key() == "environment-secret"
    assert store.reads == []


def test_supported_accounts_fall_back_to_one_bound_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in credentials.SUPPORTED_ACCOUNTS:
        monkeypatch.delenv(env_name, raising=False)
    store = FakeCredentialStore(
        {
            "OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "AI33_API_KEY": "ai33-secret",
        }
    )
    config = Config().bind_credential_store(store)
    config.llm.translate.provider = "anthropic"
    config.llm.translate.api_key_env = "ANTHROPIC_API_KEY"

    assert config.llm.segment.api_key() == "openai-secret"
    assert config.llm.translate.api_key() == "anthropic-secret"
    assert config.dub.api_key() == "ai33-secret"
    assert config.dub.credential_available() is True


def test_custom_environment_names_remain_environment_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeCredentialStore({"CUSTOM_API_KEY": "must-not-be-read"})
    config = Config().bind_credential_store(store)
    config.llm.segment.api_key_env = "CUSTOM_API_KEY"
    monkeypatch.setenv("CUSTOM_API_KEY", "custom-environment-secret")

    assert config.llm.segment.api_key() == "custom-environment-secret"
    monkeypatch.delenv("CUSTOM_API_KEY")
    with pytest.raises(RuntimeError, match="CUSTOM_API_KEY"):
        config.llm.segment.api_key()
    assert store.reads == []


def test_config_load_accepts_an_injected_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "zhsub.toml"
    config_path.write_text(
        "[llm.segment]\nmodel = 'segment-model'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = FakeCredentialStore({"OPENAI_API_KEY": "stored-secret"})

    config = Config.load(config_path, credential_store=store)

    assert config.llm.segment.api_key() == "stored-secret"


def test_tts_readiness_uses_the_bound_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI33_API_KEY", raising=False)
    config = Config().bind_credential_store(
        FakeCredentialStore({"AI33_API_KEY": "stored-secret"})
    )
    service = TtsService(
        config,
        JobStore(tmp_path / "jobs.db"),
        subtitle_signature=lambda _job: "signature",
    )

    assert service.provider_ready is True


def test_system_store_uses_the_zhsub_service_and_supported_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        credentials.keyring,
        "get_keyring",
        lambda: type("Backend", (), {"priority": 5})(),
    )
    monkeypatch.setattr(
        credentials.keyring,
        "get_password",
        lambda service, account: calls.append(("get", service, account)) or "secret",
    )
    monkeypatch.setattr(
        credentials.keyring,
        "set_password",
        lambda service, account, secret: calls.append(
            ("set", service, account, secret)
        ),
    )
    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda service, account: calls.append(("delete", service, account)),
    )
    store = credentials.SystemCredentialStore()

    assert store.available() is True
    assert store.get_secret("AI33_API_KEY") == "secret"
    store.set_secret("AI33_API_KEY", "new-secret")
    store.delete_secret("AI33_API_KEY")

    assert calls == [
        ("get", "zhsub", "AI33_API_KEY"),
        ("set", "zhsub", "AI33_API_KEY", "new-secret"),
        ("delete", "zhsub", "AI33_API_KEY"),
    ]
    with pytest.raises(ValueError, match="Unsupported credential account"):
        store.get_secret("CUSTOM_API_KEY")


def test_system_store_delete_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_service: str, _account: str) -> None:
        raise PasswordDeleteError("missing")

    monkeypatch.setattr(credentials.keyring, "delete_password", missing)

    credentials.SystemCredentialStore().delete_secret("OPENAI_API_KEY")


def test_system_store_read_failure_is_not_reported_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_service: str, _account: str) -> str | None:
        raise KeyringError("vault unavailable")

    monkeypatch.setattr(credentials.keyring, "get_password", unavailable)

    with pytest.raises(credentials.CredentialStoreError, match="OPENAI_API_KEY"):
        credentials.SystemCredentialStore().get_secret("OPENAI_API_KEY")
