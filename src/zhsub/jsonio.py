"""Đọc/ghi file JSON trung gian.

Ghi luôn theo kiểu atomic (temp file cùng thư mục + ``os.replace``). Trên Windows
``os.replace`` gọi ``MoveFileEx`` với ``MOVEFILE_REPLACE_EXISTING`` nên thao tác
là nguyên tử khi cùng volume. Đây là điều kiện để ``batch`` resume được sau khi
crash hoặc mất điện: một stage hoặc chưa ghi gì, hoặc đã ghi xong hoàn chỉnh —
không bao giờ để lại file JSON cụt.
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
    """Ghi pydantic model, dùng alias để trường ``schema_version`` ra thành ``schema``."""
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
    """Hash nội dung JSON đã chuẩn hoá.

    Dùng cho ``glossary_hash``: người dùng sửa ``glossary.json`` bằng tay thường
    quên bump trường ``version``, nên cache invalidation phải dựa vào nội dung
    thật chứ không tin số version.
    """
    canon = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(canon)
