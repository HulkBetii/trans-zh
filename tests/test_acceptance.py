"""The five acceptance criteria, tested end to end with fake LLM providers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zhsub.config import Config
from zhsub.jsonio import read_doc, write_doc
from zhsub.llm.cache import TranslationCache
from zhsub.models import (
    AsrDoc,
    EngineInfo,
    GlossaryDoc,
    GlossaryTerm,
    IngestDoc,
    MediaInfo,
    RawSegment,
    Segment,
    SegmentsDoc,
    SourceInfo,
    Token,
)
from zhsub.stages import s4_translate, s5_render
from zhsub.stages.s4_translate import TranslationFailure, translate_segments
from zhsub.subtitle import read_subtitle

from .fake_llm import FakeProvider, GlossaryAwareProvider

SENTENCE = "他是美国越狱史上的扛把子。"


def _make_segments(count: int, dur: float = 2.0, gap: float = 0.5) -> list[Segment]:
    segs = []
    t = 1.0
    for i in range(count):
        segs.append(
            Segment(id=i, start=round(t, 3), end=round(t + dur, 3),
                    text_zh=f"第{i}句{SENTENCE}", token_range=(i * 10, (i + 1) * 10))
        )
        t += dur + gap
    return segs


def _write_job(tmp_path: Path, segments: list[Segment], duration: float) -> Path:
    work = tmp_path / "job"
    work.mkdir(parents=True, exist_ok=True)
    write_doc(work / "ingest.json", IngestDoc(
        job_id="job", created_at="2026-08-08T00:00:00+00:00",
        source=SourceInfo(kind="local", uri="x.mp4", title="clip"),
        media=MediaInfo(wav_path="audio.wav", duration_sec=duration, sample_rate=16000, channels=1),
    ))
    write_doc(work / "segments.json", SegmentsDoc(
        source_asr_sha256="deadbeef", method="llm", segments=segments,
    ))
    return work


# ---------------------------------------------------------------------------
# #1 — every SRT timestamp ascends, never overlaps, never exceeds the video
# ---------------------------------------------------------------------------


def test_criterion_1_timeline_is_monotonic_and_within_the_video(tmp_path: Path):
    duration = 45 * 60.0
    segments = _make_segments(600, dur=2.0, gap=2.5)
    assert segments[-1].end < duration

    work = _write_job(tmp_path, segments, duration)
    cfg = Config()
    provider = FakeProvider()
    _translate_into(work, segments, ["vi"], provider, cfg)

    s5_render.run(work, cfg, ["vi"], tmp_path / "out", formats=("srt",))
    cues = read_subtitle(next((tmp_path / "out").glob("*.vi.srt")))

    assert len(cues) == len(segments)
    for cue in cues:
        assert cue.end > cue.start
        assert cue.end <= duration + 1e-6
    for a, b in zip(cues, cues[1:]):
        assert b.start >= a.start
        assert b.start >= a.end - 1e-6, "cue không được chồng lấn"


# ---------------------------------------------------------------------------
# #2 — vi line count == en line count == segment count
# ---------------------------------------------------------------------------


def test_criterion_2_line_counts_match_segment_count(tmp_path: Path):
    segments = _make_segments(50)
    work = _write_job(tmp_path, segments, 400.0)
    cfg = Config()
    _translate_into(work, segments, ["vi", "en"], FakeProvider(), cfg)

    s5_render.run(work, cfg, ["vi", "en"], tmp_path / "out", formats=("srt",))
    vi = read_subtitle(next((tmp_path / "out").glob("*.vi.srt")))
    en = read_subtitle(next((tmp_path / "out").glob("*.en.srt")))

    assert len(vi) == len(en) == len(segments)


def test_criterion_2_render_refuses_a_mismatched_translation_file(tmp_path: Path):
    """A 1-1 break must stop the render, not silently produce a shifted subtitle."""
    segments = _make_segments(10)
    work = _write_job(tmp_path, segments, 100.0)
    cfg = Config()
    _translate_into(work, segments, ["vi"], FakeProvider(), cfg)

    path = work / "translations.vi.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"].pop()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="1-1"):
        s5_render.run(work, cfg, ["vi"], tmp_path / "out", formats=("srt",))


# ---------------------------------------------------------------------------
# #3 — killing S4 midway then resuming must not re-translate what was cached
# ---------------------------------------------------------------------------


def test_criterion_3_resume_reuses_the_cache(tmp_path: Path):
    segments = _make_segments(40)
    cfg = Config()
    cfg.translate.batch_size = 10
    cfg.translate.review_pass = False
    cache_dir = tmp_path / "cache"

    class _Interrupted(FakeProvider):
        """Dies partway, the way a killed process does."""

        def _call(self, system, user, cache_system, json_mode=False):
            if self.calls >= 2:
                raise KeyboardInterrupt("giả lập bị giết giữa chừng")
            return super()._call(system, user, cache_system, json_mode)

    first = _Interrupted()
    cache = TranslationCache(cache_dir)
    with pytest.raises(KeyboardInterrupt):
        translate_segments(segments, "vi", first, GlossaryDoc(), cfg, cache, "gh")

    # Batches that completed before the interruption are already on disk.
    persisted = TranslationCache(cache_dir)
    cached_before = sum(
        1 for s in segments
        if persisted.get(persisted.make_key(s.text_zh, "vi", "fake-model", "gh", cfg.translate.prompt_version))
    )
    assert cached_before > 0, "phải có batch đã hoàn tất được ghi cache"

    second = FakeProvider()
    resumed = translate_segments(segments, "vi", second, GlossaryDoc(), cfg, TranslationCache(cache_dir), "gh")

    assert len(resumed) == len(segments)
    assert sum(1 for item in resumed if item.cache_hit) == cached_before
    # The resumed run must not re-request anything already paid for.
    assert not (set(second.translated_ids) & {i.id for i in resumed if i.cache_hit})


def test_cache_key_changes_with_prompt_version(tmp_path: Path):
    """Without prompt_version in the key, editing a prompt silently serves stale output."""
    cache = TranslationCache(tmp_path / "c")
    a = cache.make_key("你好", "vi", "m", "gh", 1)
    b = cache.make_key("你好", "vi", "m", "gh", 2)
    assert a != b


# ---------------------------------------------------------------------------
# #4 — a reply missing one id must recover, never crash, never emit a blank line
# ---------------------------------------------------------------------------


def test_criterion_4_missing_id_recovers_without_blank_lines(tmp_path: Path):
    segments = _make_segments(20)
    cfg = Config()
    cfg.translate.batch_size = 20
    cfg.translate.review_pass = False

    # Drops id 7 exactly once, so the retry succeeds — the real-world failure mode.
    provider = FakeProvider(drop_ids={7}, drop_once=True)
    items = translate_segments(segments, "vi", provider, GlossaryDoc(), cfg, None, "gh")

    assert len(items) == len(segments)
    assert [i.id for i in items] == [s.id for s in segments]
    assert all(i.translation.strip() for i in items), "không được có dòng rỗng"
    assert provider.calls >= 2, "phải retry sau khi phát hiện thiếu id"


def test_criterion_4_persistent_missing_id_splits_the_batch(tmp_path: Path):
    segments = _make_segments(8)
    cfg = Config()
    cfg.translate.batch_size = 8
    cfg.translate.max_retries = 2
    cfg.translate.review_pass = False

    # Always drops id 3: retries cannot help, so the batch must bisect around it.
    provider = FakeProvider(drop_ids={3}, drop_once=False)
    with pytest.raises(TranslationFailure, match="id=3"):
        translate_segments(segments, "vi", provider, GlossaryDoc(), cfg, None, "gh")


def test_criterion_4_merging_two_segments_is_never_accepted(tmp_path: Path):
    """A merged reply must be detected and recovered from, not written through.

    Merging breaks the 1-1 relation every cue's timing depends on. The bisection
    path resolves it: splitting down to single-segment batches leaves the model no
    pair to merge, so the output ends up correct without failing the job.
    """
    segments = _make_segments(6)
    cfg = Config()
    cfg.translate.batch_size = 6
    cfg.translate.max_retries = 1
    cfg.translate.review_pass = False

    provider = FakeProvider(merge_first_two=True)
    items = translate_segments(segments, "vi", provider, GlossaryDoc(), cfg, None, "gh")

    assert [i.id for i in items] == [s.id for s in segments]
    assert all(i.translation.strip() for i in items)
    # No translation may carry a second segment's text glued onto it.
    for item, seg in zip(items, segments):
        assert item.translation == f"VI:seg{seg.id}"
    assert provider.calls > len(segments) / cfg.translate.batch_size, "phải có retry và chia batch"


def test_json_wrapped_in_prose_is_still_accepted():
    """Small local models add a preamble no matter what the prompt says."""
    segments = _make_segments(4)
    cfg = Config()
    cfg.translate.batch_size = 4
    cfg.translate.review_pass = False

    items = translate_segments(
        segments, "vi", FakeProvider(wrap_in_prose=True), GlossaryDoc(), cfg, None, "gh"
    )
    assert len(items) == 4


# ---------------------------------------------------------------------------
# #5 — glossary terms appear identically everywhere
# ---------------------------------------------------------------------------


def test_criterion_5_glossary_terms_are_consistent_across_batches(tmp_path: Path):
    term = GlossaryTerm(zh="扛把子", pinyin="káng bǎ zi", vi="trùm sò", en="kingpin", type="term")
    glossary = GlossaryDoc(terms=[term])

    segments = _make_segments(60)
    cfg = Config()
    cfg.translate.batch_size = 10  # forces six separate batches
    cfg.translate.review_pass = False

    provider = GlossaryAwareProvider({"扛把子": "trùm sò"})
    items = translate_segments(segments, "vi", provider, glossary, cfg, None, "gh")

    assert provider.calls >= 6, "phải chia thành nhiều batch mới kiểm được tính nhất quán"
    hits = [i for i in items if "扛把子" in i.text_zh]
    assert hits
    for item in hits:
        assert "trùm sò" in item.translation
        assert "扛把子" not in item.translation


def test_scene_context_stays_outside_the_json_payload():
    """Context inside the JSON made a 7B model echo Chinese instead of translating.

    Measured on qwen2.5-7b: with context as sibling keys of "to_translate" in one
    object, 14 of 20 outputs came back untranslated. Moving it to a plain-text block
    above the JSON dropped that to 1 of 20. The JSON must therefore carry only the
    lines to translate.
    """
    segments = _make_segments(5)
    message = s4_translate._build_user_message(segments[2:4], segments[:2], segments[4:])

    payload = json.loads(message.split("TRANSLATE THIS JSON:", 1)[1])
    assert set(payload) == {"to_translate"}
    assert [i["id"] for i in payload["to_translate"]] == [2, 3]
    assert "SCENE CONTEXT" in message
    assert segments[0].text_zh in message.split("TRANSLATE THIS JSON:", 1)[0]


def test_editing_the_glossary_forces_a_retranslation(tmp_path: Path):
    """The glossary edit loop is the main lever on translation quality.

    Comparing only the segmentation meant `resume --from translate` after a hand
    edit returned the previous file untouched — the edit appeared to do nothing.
    """
    from zhsub.models import TranslationsDoc

    cfg = Config()
    existing = TranslationsDoc(
        lang="vi", model="m", prompt_version=cfg.translate.prompt_version,
        glossary_hash="OLD", segments_hash="SEG", items=[],
    )

    assert s4_translate._staleness(existing, "SEG", "OLD", "m", cfg) == []
    assert s4_translate._staleness(existing, "SEG", "NEW", "m", cfg) == ["glossary.json"]
    assert s4_translate._staleness(existing, "OTHER", "OLD", "m", cfg) == ["segments.json"]
    assert s4_translate._staleness(existing, "SEG", "OLD", "other-model", cfg) == ["model"]

    cfg.translate.prompt_version += 1
    assert s4_translate._staleness(existing, "SEG", "OLD", "m", cfg) == ["prompt_version"]


def test_char_budget_takes_the_tighter_of_the_two_limits():
    """The spec's two limits disagree; the tighter one has to win.

    A 7s cue allows 147 characters at 21 CPS but only 84 in two 42-character lines.
    Measured on a real clip, 24 of 35 Vietnamese translations overflowed because
    only the CPS limit was applied.
    """
    cfg = Config()
    long_cue = Segment(id=0, start=0.0, end=7.0, text_zh="x", token_range=(0, 1))
    short_cue = Segment(id=1, start=0.0, end=2.0, text_zh="x", token_range=(0, 1))

    assert s4_translate.char_budget(long_cue, cfg, "vi") == 84  # line-limited
    assert s4_translate.char_budget(short_cue, cfg, "vi") == 42  # rate-limited


def test_budget_reaches_the_model():
    cfg = Config()
    segments = _make_segments(2)
    budgets = {s.id: s4_translate.char_budget(s, cfg, "vi") for s in segments}
    message = s4_translate._build_user_message(segments, [], [], budgets=budgets)

    payload = json.loads(message.split("TRANSLATE THIS JSON:", 1)[1])
    assert all("max_chars" in entry for entry in payload["to_translate"])


def test_any_leftover_chinese_counts_as_untranslated():
    """A ratio or small-count rule misses the model's most common failure.

    All three of these came out of real qwen2.5-7b output. "trốn狱" welds a single
    Han character into a Vietnamese word — no threshold catches it, yet it is
    exactly the defect that would ship.
    """
    assert not s4_translate.looks_untranslated("Anh ta là trùm sò của giới vượt ngục")
    assert s4_translate.looks_untranslated("他是美国越狱史上的扛把子")
    assert s4_translate.looks_untranslated("càng ngày càng hoang诞无比")
    assert s4_translate.looks_untranslated("hoặc trốn狱, hoặc chết")


def test_keep_source_terms_are_not_flagged_as_untranslated():
    """A glossary entry marked keep_source is supposed to stay in Chinese."""
    assert not s4_translate.looks_untranslated(
        "Món 麻婆豆腐 rất nổi tiếng", keep_source=("麻婆豆腐",)
    )
    assert s4_translate.looks_untranslated(
        "Món 麻婆豆腐 rất 好吃", keep_source=("麻婆豆腐",)
    )


def test_address_terms_are_scoped_to_direct_speech():
    """As a blanket rule they rewrite narration into the first person.

    Measured: 他进行了一番伪装 ("he disguised himself") came back as "mình đã cải
    trang" ("I disguised myself"), and 警方 ("the police") became "các anh" ("you"),
    because the model read the address table as applying everywhere.
    """
    from zhsub.models import AddressTerm

    glossary = GlossaryDoc(address_terms=[
        AddressTerm(speaker="理查德", addressee="弟弟", vi_self="anh", vi_other="em")
    ])
    prompt = s4_translate.build_system_prompt(glossary, "vi")

    assert "ONLY inside direct speech" in prompt
    assert "NEVER use these in narration" in prompt
    assert "third person" in prompt


def test_target_language_is_named_explicitly_in_the_prompt():
    prompt = s4_translate.build_system_prompt(GlossaryDoc(), "vi")
    assert "VIETNAMESE" in prompt
    assert "NEVER copy Chinese characters" in prompt


def test_glossary_substitution_also_covers_cached_entries(tmp_path: Path):
    """A cache hit must not be able to smuggle an untranslated term through.

    The cache holds raw model output, so the substitution has to happen on read.
    Applying it only to freshly translated batches left three Chinese place names in
    a real run the moment those segments came back from cache.
    """
    glossary = GlossaryDoc(terms=[
        GlossaryTerm(zh="美国空军", vi="Không quân Hoa Kỳ", en="US Air Force", type="org"),
    ])
    segments = [Segment(id=0, start=0.0, end=3.0, text_zh="加入美国空军", token_range=(0, 6))]
    cfg = Config()
    cfg.translate.review_pass = False
    cache = TranslationCache(tmp_path / "cache")

    # Seed the cache with output that left the term in Chinese.
    key = cache.make_key(segments[0].text_zh, "vi", "fake-model", "gh", cfg.translate.prompt_version)
    cache.put(key, "gia nhập 美国空军")

    provider = FakeProvider()
    items = translate_segments(segments, "vi", provider, glossary, cfg, cache, "gh")

    assert provider.calls == 0, "phải là cache hit"
    assert items[0].cache_hit
    assert items[0].translation == "gia nhập Không quân Hoa Kỳ"


def test_glossary_terms_left_in_chinese_are_substituted():
    """The glossary defines these strings, so applying it is the definition winning.

    Observed with gpt-4o-mini: good Vietnamese overall, but 俄克拉荷马州 and 美国空军
    were copied verbatim despite both being defined in its own prompt.
    """
    glossary = GlossaryDoc(terms=[
        GlossaryTerm(zh="俄克拉荷马州", vi="Bang Oklahoma", en="Oklahoma", type="place"),
        GlossaryTerm(zh="美国空军", vi="Không quân Hoa Kỳ", en="US Air Force", type="org"),
    ])
    text = "sinh ra ở 俄克拉荷马州, sau vào 美国空军."

    assert s4_translate.apply_glossary(text, glossary, "vi") == (
        "sinh ra ở Bang Oklahoma, sau vào Không quân Hoa Kỳ."
    )
    assert "Oklahoma" in s4_translate.apply_glossary(text, glossary, "en")


def test_keep_source_terms_are_left_alone_by_substitution():
    glossary = GlossaryDoc(terms=[
        GlossaryTerm(zh="麻婆豆腐", vi="đậu phụ Ma Bà", type="dish", keep_source=True),
    ])
    text = "Món 麻婆豆腐 rất ngon"

    assert s4_translate.apply_glossary(text, glossary, "vi") == text


def test_longer_glossary_terms_are_substituted_first():
    # Substituting the shorter term first would leave a mangled fragment behind.
    glossary = GlossaryDoc(terms=[
        GlossaryTerm(zh="北达科他州", vi="Bang Bắc Dakota", type="place"),
        GlossaryTerm(zh="北达科他州麦诺特空军基地", vi="Căn cứ Không quân Minot", type="place"),
    ])

    assert s4_translate.apply_glossary("ở 北达科他州麦诺特空军基地", glossary, "vi") == (
        "ở Căn cứ Không quân Minot"
    )


def test_glossary_goes_into_the_system_prompt_verbatim():
    """Identical across batches is what makes prompt caching work."""
    glossary = GlossaryDoc(terms=[
        GlossaryTerm(zh="麻婆豆腐", pinyin="má pó dòu fu", vi="đậu phụ Ma Bà", en="Mapo Tofu", type="dish")
    ])
    vi = s4_translate.build_system_prompt(glossary, "vi")
    en = s4_translate.build_system_prompt(glossary, "en")

    assert "đậu phụ Ma Bà" in vi and "麻婆豆腐" in vi
    assert "Mapo Tofu" in en
    assert vi == s4_translate.build_system_prompt(glossary, "vi"), "phải ổn định từng byte"


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def _translate_into(work: Path, segments, langs, provider, cfg) -> None:
    from zhsub.models import TranslationsDoc

    for lang in langs:
        items = translate_segments(segments, lang, provider, GlossaryDoc(), cfg, None, "gh")
        write_doc(work / f"translations.{lang}.json", TranslationsDoc(
            lang=lang, model=provider.model, prompt_version=cfg.translate.prompt_version,
            glossary_hash="gh", items=items,
        ))
