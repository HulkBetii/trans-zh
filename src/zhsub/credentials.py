"""Environment-first API credential resolution backed by the OS vault."""

from __future__ import annotations

import os
import threading
from typing import Protocol, runtime_checkable

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE_NAME = "zhsub"
SUPPORTED_ACCOUNTS = frozenset(("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AI33_API_KEY"))


class CredentialStoreError(RuntimeError):
    """Raised when credential vault access cannot be completed."""


@runtime_checkable
class CredentialStore(Protocol):
    def get_secret(self, env_name: str) -> str | None: ...

    def set_secret(self, env_name: str, secret: str) -> None: ...

    def delete_secret(self, env_name: str) -> None: ...

    def available(self) -> bool: ...


def _validate_account(env_name: str) -> str:
    account = env_name.strip()
    if account not in SUPPORTED_ACCOUNTS:
        raise ValueError(f"Unsupported credential account: {account!r}")
    return account


class SystemCredentialStore:
    """Store supported API keys in the platform credential manager."""

    service_name = SERVICE_NAME

    def available(self) -> bool:
        try:
            return float(keyring.get_keyring().priority) > 0
        except (KeyringError, RuntimeError, TypeError, ValueError):
            return False

    def get_secret(self, env_name: str) -> str | None:
        account = _validate_account(env_name)
        try:
            value = keyring.get_password(self.service_name, account)
        except (KeyringError, RuntimeError) as exc:
            raise CredentialStoreError(
                f"Could not read credential account {account}"
            ) from exc
        if value is None:
            return None
        secret = value.strip()
        return secret or None

    def set_secret(self, env_name: str, secret: str) -> None:
        account = _validate_account(env_name)
        value = secret.strip()
        if not value:
            raise ValueError("Credential secret must not be blank")
        try:
            keyring.set_password(self.service_name, account, value)
        except (KeyringError, RuntimeError) as exc:
            raise CredentialStoreError(
                f"Could not store credential account {account}"
            ) from exc

    def delete_secret(self, env_name: str) -> None:
        account = _validate_account(env_name)
        try:
            keyring.delete_password(self.service_name, account)
        except PasswordDeleteError:
            return
        except (KeyringError, RuntimeError) as exc:
            raise CredentialStoreError(
                f"Could not delete credential account {account}"
            ) from exc


_default_store: CredentialStore | None = None
_default_store_lock = threading.Lock()


def get_default_credential_store() -> CredentialStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = SystemCredentialStore()
        return _default_store


def resolve_secret(
    env_name: str,
    credential_store: CredentialStore | None = None,
) -> str | None:
    """Resolve one credential without exposing it through configuration models."""
    if not env_name:
        return None
    environment_value = os.environ.get(env_name, "").strip()
    if environment_value:
        return environment_value
    if env_name not in SUPPORTED_ACCOUNTS:
        return None
    store = credential_store or get_default_credential_store()
    return store.get_secret(env_name)
