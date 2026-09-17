"""Reading and writing the intermediate JSON files.

Writes are always atomic (temp file in the same directory + ``os.replace``). On
Windows ``os.replace`` maps to ``MoveFileEx`` with ``MOVEFILE_REPLACE_EXISTING``,
so it is atomic within a volume. This is what makes ``batch`` resumable after a
crash or power loss: a stage has either written nothing or written a complete
file — never a truncated JSON that a later stage would happily parse.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
_WINDOWS_REPLACE_ERRORS = {5, 32}
_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4)


def replace_file_with_retry(source: str | Path, destination: str | Path) -> None:
    """Atomically replace a file, tolerating short-lived Windows file locks."""
    source = Path(source)
    destination = Path(destination)
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            retryable = getattr(exc, "winerror", None) in _WINDOWS_REPLACE_ERRORS
            if not retryable or attempt == len(_REPLACE_RETRY_DELAYS):
                if retryable:
                    raise OSError(
                        f"Không thể ghi đè file {destination} sau nhiều lần thử: {exc}"
                    ) from exc
                raise
            time.sleep(_REPLACE_RETRY_DELAYS[attempt])


def write_json_atomic(path: str | Path, data: dict | list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        replace_file_with_retry(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_doc(path: str | Path, doc: BaseModel) -> None:
    """Write a pydantic model, using aliases so ``schema_version`` serialises as ``schema``."""
    write_json_atomic(path, doc.model_dump(by_alias=True, mode="json"))


def read_doc(path: str | Path, model: type[T]) -> T:
    with open(path, encoding="utf-8") as f:
        return model.model_validate(json.load(f))


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json_canonical(data: dict | list) -> str:
    """Hash of canonicalised JSON content.

    Used for ``glossary_hash``: users editing ``glossary.json`` by hand routinely
    forget to bump the ``version`` field, so cache invalidation must key off the
    actual content rather than a number we cannot trust.
    """
    canon = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(canon)
