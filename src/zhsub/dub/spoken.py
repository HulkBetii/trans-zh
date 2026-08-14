"""Revisioned spoken-text overrides for Vietnamese TTS."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Iterable, Mapping

from ..jsonio import read_doc, sha256_json_canonical, sha256_text, write_doc
from ..models import (
    EffectiveSpokenItem,
    SpeechChange,
    SpeechDoc,
    SpeechEvaluation,
    SpeechOverride,
    SpeechState,
)
from ..overrides import effective_items
from .text import normalize_for_speech

SPEECH_LANG = "vi"


class SpeechConflictError(RuntimeError):
    def __init__(self, base_revision: str, current_revision: str) -> None:
        self.base_revision = base_revision
        self.current_revision = current_revision
        super().__init__(
            "speech revision conflict: "
            f"expected {base_revision!r}, current revision is {current_revision!r}"
        )


class SpeechValidationError(ValueError):
    pass


_locks_guard = Lock()
_locks: dict[Path, RLock] = {}


def _speech_path(work_dir: str | Path) -> Path:
    return Path(work_dir) / "speech.vi.json"


def _speech_lock(path: Path) -> RLock:
    key = path.resolve(strict=False)
    with _locks_guard:
        return _locks.setdefault(key, RLock())


def _load_doc(work_dir: str | Path, fallback_voice_id: str | None = None) -> SpeechDoc:
    path = _speech_path(work_dir)
    if path.is_file():
        return read_doc(path, SpeechDoc)
    if fallback_voice_id is None or not fallback_voice_id.strip():
        raise SpeechValidationError("Vietnamese speech voice has not been selected")
    return SpeechDoc(voice_id=fallback_voice_id)


def load_speech_doc(work_dir: str | Path) -> SpeechDoc | None:
    path = _speech_path(work_dir)
    if not path.is_file():
        return None
    return read_doc(path, SpeechDoc)


def resolve_voice_id(
    work_dir: str | Path,
    fallback_voice_id: str | None = None,
    *,
    override_voice_id: str | None = None,
) -> str:
    """Resolve the immutable job voice, accepting a fallback for legacy jobs."""
    stored = load_speech_doc(work_dir)
    if stored is not None:
        if override_voice_id is not None and override_voice_id.strip() != stored.voice_id:
            raise SpeechValidationError(
                f"job voice is already {stored.voice_id!r}; save a new selection first"
            )
        return stored.voice_id
    selected = (override_voice_id or fallback_voice_id or "").strip()
    if not selected:
        raise SpeechValidationError("Vietnamese speech voice has not been selected")
    return selected


def ensure_speech_doc(work_dir: str | Path, voice_id: str) -> SpeechDoc:
    """Persist the legacy config voice once, without replacing a job selection."""
    path = _speech_path(work_dir)
    with _speech_lock(path):
        if path.is_file():
            return read_doc(path, SpeechDoc)
        doc = SpeechDoc(voice_id=voice_id)
        write_doc(path, doc)
        return doc


def _base_items(work_dir: str | Path) -> list[EffectiveSpokenItem]:
    return [
        EffectiveSpokenItem(
            segment_id=item.segment_id,
            source_text=item.source_text,
            subtitle_text=item.effective_text,
            base_spoken_text=normalize_for_speech(item.effective_text),
            effective_spoken_text=normalize_for_speech(item.effective_text),
        )
        for item in effective_items(work_dir, SPEECH_LANG)
    ]


def _status(
    override: SpeechOverride,
    source_text: str | None,
    base_spoken_text: str | None,
) -> str:
    if source_text is None or base_spoken_text is None:
        return "stale"
    if override.source_text_hash != sha256_text(source_text):
        return "stale"
    if override.base_spoken_hash != sha256_text(base_spoken_text):
        return "base_changed"
    return "valid"


def _revision(doc: SpeechDoc, base_items: list[EffectiveSpokenItem]) -> str:
    return sha256_json_canonical(
        {
            "speech": doc.model_dump(by_alias=True, mode="json"),
            "source": [
                [item.segment_id, sha256_text(item.source_text)] for item in base_items
            ],
            "base_spoken": [
                [item.segment_id, sha256_text(item.base_spoken_text)] for item in base_items
            ],
        }
    )


def get_speech_state(
    work_dir: str | Path,
    voice_id: str | None = None,
) -> SpeechState:
    doc = _load_doc(work_dir, voice_id)
    base_items = _base_items(work_dir)
    source_by_id = {item.segment_id: item.source_text for item in base_items}
    base_by_id = {item.segment_id: item.base_spoken_text for item in base_items}
    evaluations = [
        SpeechEvaluation(
            segment_id=item.segment_id,
            text=item.text,
            status=_status(
                item,
                source_by_id.get(item.segment_id),
                base_by_id.get(item.segment_id),
            ),
        )
        for item in doc.overrides
    ]
    return SpeechState(
        voice_id=doc.voice_id,
        revision=_revision(doc, base_items),
        overrides=doc.overrides,
        evaluations=evaluations,
    )


def effective_spoken_items(
    work_dir: str | Path,
    voice_id: str | None = None,
) -> list[EffectiveSpokenItem]:
    doc = _load_doc(work_dir, voice_id)
    rows = _base_items(work_dir)
    overrides = {item.segment_id: item for item in doc.overrides}

    result: list[EffectiveSpokenItem] = []
    for row in rows:
        override = overrides.get(row.segment_id)
        status = (
            _status(override, row.source_text, row.base_spoken_text)
            if override is not None
            else None
        )
        applies = status in {"valid", "base_changed"}
        result.append(
            row.model_copy(
                update={
                    "effective_spoken_text": (
                        override.text if override is not None and applies else row.base_spoken_text
                    ),
                    "overridden": applies,
                    "stale": status == "stale",
                    "base_changed": status == "base_changed",
                }
            )
        )
    return result


def effective_spoken_hash(items: Iterable[EffectiveSpokenItem]) -> str:
    return sha256_json_canonical(
        [
            {"segment_id": item.segment_id, "text": item.effective_spoken_text}
            for item in items
        ]
    )


def _coerce_changes(
    changes: Iterable[SpeechChange | Mapping[str, object]],
) -> list[SpeechChange]:
    parsed = [
        change if isinstance(change, SpeechChange) else SpeechChange.model_validate(change)
        for change in changes
    ]
    segment_ids = [change.segment_id for change in parsed]
    if len(segment_ids) != len(set(segment_ids)):
        raise SpeechValidationError("each segment may appear only once per speech update")
    return parsed


def apply_spoken_changes(
    work_dir: str | Path,
    voice_id: str,
    base_revision: str,
    changes: Iterable[SpeechChange | Mapping[str, object]],
) -> SpeechState:
    """Atomically update the job voice and spoken overrides."""
    path = _speech_path(work_dir)
    parsed_changes = _coerce_changes(changes)

    with _speech_lock(path):
        doc = _load_doc(work_dir, voice_id)
        base_items = _base_items(work_dir)
        current_revision = _revision(doc, base_items)
        if base_revision != current_revision:
            raise SpeechConflictError(base_revision, current_revision)

        source_by_id = {item.segment_id: item.source_text for item in base_items}
        base_by_id = {item.segment_id: item.base_spoken_text for item in base_items}
        overrides = {item.segment_id: item for item in doc.overrides}
        changed = doc.voice_id != voice_id.strip()
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        for change in parsed_changes:
            if change.text is None:
                changed = overrides.pop(change.segment_id, None) is not None or changed
                continue
            if change.segment_id not in source_by_id:
                raise SpeechValidationError(f"unknown subtitle segment id: {change.segment_id}")
            replacement = SpeechOverride(
                segment_id=change.segment_id,
                source_text_hash=sha256_text(source_by_id[change.segment_id]),
                base_spoken_hash=sha256_text(base_by_id[change.segment_id]),
                text=change.text,
                updated_at=updated_at,
            )
            previous = overrides.get(change.segment_id)
            comparable = ("source_text_hash", "base_spoken_hash", "text")
            if previous is not None and all(
                getattr(previous, field) == getattr(replacement, field)
                for field in comparable
            ):
                continue
            overrides[change.segment_id] = replacement
            changed = True

        if changed or not path.is_file():
            doc = SpeechDoc(
                voice_id=voice_id,
                overrides=sorted(overrides.values(), key=lambda item: item.segment_id),
            )
            write_doc(path, doc)
        return get_speech_state(work_dir)


def save_speech(
    work_dir: str | Path,
    voice_id: str,
    revision: str,
    changes: Iterable[SpeechChange | Mapping[str, object]],
) -> SpeechState:
    return apply_spoken_changes(work_dir, voice_id, revision, changes)
