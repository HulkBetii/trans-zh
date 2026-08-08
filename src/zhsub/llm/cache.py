"""On-disk cache so re-running a job costs no tokens.

Keyed on everything that can change the answer. ``prompt_version`` is in the key
deliberately: without it, editing a prompt and re-running silently serves the old
translations, which is the worst kind of stale — invisible.

Stored as one JSON file per shard of the hash rather than SQLite: the cache is
written from one process per job but may be read by several concurrent jobs, and
whole-file atomic replacement sidesteps writer-lock contention entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class TranslationCache:
    def __init__(self, cache_dir: str | Path, shards: int = 256) -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.shards = shards
        self._loaded: dict[str, dict[str, str]] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(
        text: str,
        target_lang: str,
        model: str,
        glossary_hash: str,
        prompt_version: int,
    ) -> str:
        raw = "\x1f".join([text, target_lang, model, glossary_hash, str(prompt_version)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _shard_path(self, key: str) -> Path:
        return self.dir / f"{key[:2]}.json"

    def _shard(self, key: str) -> dict[str, str]:
        name = key[:2]
        if name not in self._loaded:
            path = self._shard_path(key)
            if path.is_file():
                try:
                    self._loaded[name] = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    # A corrupt shard costs tokens, not correctness — start it over.
                    self._loaded[name] = {}
            else:
                self._loaded[name] = {}
        return self._loaded[name]

    def get(self, key: str) -> str | None:
        value = self._shard(key).get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def put(self, key: str, value: str) -> None:
        self._shard(key)[key] = value

    def flush(self) -> None:
        """Persist loaded shards. Called after each batch so a crash mid-job keeps
        everything already paid for — which is what makes ``resume`` cheap."""
        for name, data in self._loaded.items():
            path = self.dir / f"{name}.json"
            tmp = path.with_name(path.name + f".tmp{os.getpid()}")
            try:
                # Re-read before writing: another job may have added entries to this
                # shard since it was loaded, and clobbering them just wastes tokens.
                merged = dict(data)
                if path.is_file():
                    try:
                        merged = {**json.loads(path.read_text(encoding="utf-8")), **data}
                    except (json.JSONDecodeError, OSError):
                        pass
                tmp.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
