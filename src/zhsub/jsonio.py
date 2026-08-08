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
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def write_json_atomic(path: str | Path, data: dict | list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
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
