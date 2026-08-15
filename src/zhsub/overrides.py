"""Manual subtitle overrides layered on top of generated translations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Iterable, Mapping

from .jsonio import read_doc, sha256_json_canonical, sha256_text, write_doc
from .models import (
    EffectiveTranslationItem,
    Lang,
    OverrideChange,
    OverrideEvaluation,
    OverrideState,
    OverridesDoc,
    SegmentsDoc,
    SubtitleOverride,
    TranslationsDoc,
)

SUPPORTED_LANGS = frozenset(("vi", "en"))


class OverrideConflictError(RuntimeError):
    """Raised when a write is based on an obsolete override revision."""

    def __init__(self, base_revision: str, current_revision: str) -> None:
        self.base_revision = base_revision
        self.current_revision = current_revision
        super().__init__(
            "override revision conflict: "
            f"expected {base_revision!r}, current revision is {current_revision!r}"
        )


class OverrideValidationError(ValueError):
    """Raised when an override change cannot target the current subtitle set."""


_locks_guard = Lock()
_locks: dict[Path, RLock] = {}


def _override_lock(path: Path) -> RLock:
    key = path.resolve(strict=False)
    with _locks_guard:
        return _locks.setdefault(key, RLock())


def _validate_lang(lang: str) -> Lang:
    if lang not in SUPPORTED_LANGS:
        raise OverrideValidationError(f"unsupported subtitle language: {lang!r}")
    return lang  # type: ignore[return-value]


def _override_path(work_dir: str | Path, lang: str) -> Path:
    valid_lang = _validate_lang(lang)
    return Path(work_dir) / f"overrides.{valid_lang}.json"


def _empty_doc(lang: str) -> OverridesDoc:
    return OverridesDoc(lang=_validate_lang(lang))


def _load_doc(work_dir: str | Path, lang: str) -> OverridesDoc:
    path = _override_path(work_dir, lang)
    if not path.is_file():
        return _empty_doc(lang)
    doc = read_doc(path, OverridesDoc)
    if doc.lang != lang:
        raise OverrideValidationError(
            f"{path.name} declares language {doc.lang!r}, expected {lang!r}"
        )
    return doc


def _revision(
    doc: OverridesDoc,
    segments: SegmentsDoc,
    translations: TranslationsDoc,
) -> str:
    return sha256_json_canonical(
        {
            "overrides": doc.model_dump(by_alias=True, mode="json"),
            "source": [
                [segment.id, sha256_text(segment.text_zh)]
                for segment in segments.segments
            ],
            "translations": [
                [item.id, sha256_text(item.translation)]
                for item in translations.items
            ],
        }
    )


def _load_base(
    work_dir: str | Path,
    lang: str,
) -> tuple[SegmentsDoc, TranslationsDoc]:
    valid_lang = _validate_lang(lang)
    root = Path(work_dir)
    segments = read_doc(root / "segments.json", SegmentsDoc)
    translations = read_doc(root / f"translations.{valid_lang}.json", TranslationsDoc)
    if translations.lang != valid_lang:
        raise OverrideValidationError(
            f"translations.{valid_lang}.json declares language {translations.lang!r}"
        )

    segment_ids = [segment.id for segment in segments.segments]
    translation_ids = [item.id for item in translations.items]
    if len(segment_ids) != len(set(segment_ids)):
        raise OverrideValidationError("segments.json contains duplicate segment ids")
    if len(translation_ids) != len(set(translation_ids)):
        raise OverrideValidationError(
            f"translations.{valid_lang}.json contains duplicate segment ids"
        )
    if set(segment_ids) != set(translation_ids):
        missing = sorted(set(segment_ids) - set(translation_ids))
        extra = sorted(set(translation_ids) - set(segment_ids))
        raise OverrideValidationError(
            f"translations.{valid_lang}.json breaks the required 1-1 relation "
            "with segments.json "
            f"(missing={missing[:8]}, extra={extra[:8]})"
        )
    return segments, translations


def _override_status(
    override: SubtitleOverride,
    source_text: str | None,
    model_text: str | None,
) -> str:
    if source_text is None or model_text is None:
        return "stale"
    if override.source_text_hash != sha256_text(source_text):
        return "stale"
    if override.base_translation_hash != sha256_text(model_text):
        return "base_changed"
    return "valid"


def get_override_state(work_dir: str | Path, lang: str) -> OverrideState:
    """Load overrides and evaluate them against the current generated artifacts."""
    doc = _load_doc(work_dir, lang)
    segments, translations = _load_base(work_dir, lang)
    source_by_id = {segment.id: segment.text_zh for segment in segments.segments}
    model_by_id = {item.id: item.translation for item in translations.items}
    evaluations = [
        OverrideEvaluation(
            segment_id=item.segment_id,
            text=item.text,
            status=_override_status(
                item,
                source_by_id.get(item.segment_id),
                model_by_id.get(item.segment_id),
            ),
        )
        for item in doc.items
    ]
    return OverrideState(
        lang=doc.lang,
        revision=_revision(doc, segments, translations),
        items=doc.items,
        evaluations=evaluations,
    )


def effective_items(
    work_dir: str | Path,
    lang: str,
) -> list[EffectiveTranslationItem]:
    """Return one effective translation row for every current segment."""
    doc = _load_doc(work_dir, lang)
    segments, translations = _load_base(work_dir, lang)
    model_by_id = {item.id: item.translation for item in translations.items}
    override_by_id = {item.segment_id: item for item in doc.items}

    rows: list[EffectiveTranslationItem] = []
    for segment in segments.segments:
        model_text = model_by_id[segment.id]
        override = override_by_id.get(segment.id)
        status = (
            _override_status(override, segment.text_zh, model_text)
            if override is not None
            else None
        )
        applies = status in {"valid", "base_changed"}
        rows.append(
            EffectiveTranslationItem(
                segment_id=segment.id,
                source_text=segment.text_zh,
                model_text=model_text,
                effective_text=override.text if override is not None and applies else model_text,
                overridden=applies,
                stale=status == "stale",
                base_changed=status == "base_changed",
            )
        )
    return rows


def effective_text_map(work_dir: str | Path, lang: str) -> dict[int, str]:
    return {item.segment_id: item.effective_text for item in effective_items(work_dir, lang)}


def effective_items_hash(items: Iterable[EffectiveTranslationItem]) -> str:
    payload = [
        {"segment_id": item.segment_id, "text": item.effective_text}
        for item in items
    ]
    return sha256_json_canonical(payload)


def effective_hash(work_dir: str | Path, lang: str) -> str:
    return effective_items_hash(effective_items(work_dir, lang))


def effective_translations_hash(work_dir: str | Path, lang: str) -> str:
    """Backend-friendly alias for :func:`effective_hash`."""
    return effective_hash(work_dir, lang)


def _coerce_changes(
    changes: Iterable[OverrideChange | Mapping[str, object]],
) -> list[OverrideChange]:
    parsed = [
        change if isinstance(change, OverrideChange) else OverrideChange.model_validate(change)
        for change in changes
    ]
    segment_ids = [change.segment_id for change in parsed]
    if len(segment_ids) != len(set(segment_ids)):
        raise OverrideValidationError("each segment may appear only once per override update")
    return parsed


def apply_override_changes(
    work_dir: str | Path,
    lang: str,
    base_revision: str,
    changes: Iterable[OverrideChange | Mapping[str, object]],
) -> OverrideState:
    """Apply a revision-checked batch and atomically replace the override artifact."""
    path = _override_path(work_dir, lang)
    parsed_changes = _coerce_changes(changes)

    with _override_lock(path):
        doc = _load_doc(work_dir, lang)
        segments, translations = _load_base(work_dir, lang)
        current_revision = _revision(doc, segments, translations)
        if base_revision != current_revision:
            raise OverrideConflictError(base_revision, current_revision)

        source_by_id = {segment.id: segment.text_zh for segment in segments.segments}
        model_by_id = {item.id: item.translation for item in translations.items}
        items_by_id = {item.segment_id: item for item in doc.items}
        changed = False
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        for change in parsed_changes:
            if change.text is None:
                changed = items_by_id.pop(change.segment_id, None) is not None or changed
                continue
            if change.segment_id not in source_by_id:
                raise OverrideValidationError(
                    f"unknown subtitle segment id: {change.segment_id}"
                )

            replacement = SubtitleOverride(
                segment_id=change.segment_id,
                source_text_hash=sha256_text(source_by_id[change.segment_id]),
                base_translation_hash=sha256_text(model_by_id[change.segment_id]),
                text=change.text,
                updated_at=updated_at,
            )
            previous = items_by_id.get(change.segment_id)
            comparable_fields = ("source_text_hash", "base_translation_hash", "text")
            if previous is not None and all(
                getattr(previous, field) == getattr(replacement, field)
                for field in comparable_fields
            ):
                continue
            items_by_id[change.segment_id] = replacement
            changed = True

        if changed:
            doc = OverridesDoc(lang=_validate_lang(lang), items=sorted(
                items_by_id.values(), key=lambda item: item.segment_id
            ))
            write_doc(path, doc)
        return get_override_state(work_dir, lang)


def save_overrides(
    work_dir: str | Path,
    lang: str,
    revision: str,
    changes: Iterable[OverrideChange | Mapping[str, object]],
) -> OverrideState:
    """Backend-friendly alias for :func:`apply_override_changes`."""
    return apply_override_changes(work_dir, lang, revision, changes)
