"""Glossary extraction: what belongs in it, and what must be kept out."""

from __future__ import annotations

from zhsub.stages.s3_glossary import _merge_terms


def test_everyday_words_are_dropped():
    """The prompt names these as examples to skip and the model returns them anyway.

    Measured on a 35-minute clip: 美国, 监狱, 警察, 越狱 and 逃跑 all came back despite
    being listed verbatim in the prompt as things to leave out. Pinning them to one
    rendering hurts the translation — 美国 is "America", "American" or "the US"
    depending on the sentence — so the filter has to be deterministic.
    """
    raw = [[
        {"zh": "美国", "vi": "Mỹ", "en": "United States", "type": "place"},
        {"zh": "监狱", "vi": "nhà tù", "en": "prison", "type": "term"},
        {"zh": "越狱", "vi": "vượt ngục", "en": "prison break", "type": "term"},
        {"zh": "警察", "vi": "cảnh sát", "en": "police", "type": "term"},
        {"zh": "逃跑", "vi": "chạy trốn", "en": "flee", "type": "term"},
    ]]
    assert _merge_terms(raw) == []


def test_proper_nouns_survive():
    raw = [[
        {"zh": "理查德", "vi": "Richard", "en": "Richard", "type": "person"},
        {"zh": "佛罗伦斯超级监狱", "vi": "Nhà tù siêu cấp Florence",
         "en": "Florence Supermax Prison", "type": "org"},
    ]]
    terms = _merge_terms(raw)

    assert {t.zh for t in terms} == {"理查德", "佛罗伦斯超级监狱"}


def test_a_named_prison_is_kept_even_though_plain_prison_is_not():
    """The distinction the filter has to draw: a specific name passes, the generic
    word does not."""
    raw = [[
        {"zh": "监狱", "vi": "nhà tù", "en": "prison", "type": "term"},
        {"zh": "波洛克联邦监狱", "vi": "Nhà tù liên bang Pollock",
         "en": "Pollock Federal Prison", "type": "org"},
    ]]
    assert [t.zh for t in _merge_terms(raw)] == ["波洛克联邦监狱"]


def test_duplicate_entries_merge_keeping_the_richest_fields():
    """Different batches see the same name in different contexts; take the fullest."""
    raw = [
        [{"zh": "理查德", "vi": "Richard", "en": "", "pinyin": "", "type": "person"}],
        [{"zh": "理查德", "vi": "Richard", "en": "Richard Lee McNair",
          "pinyin": "Lǐchádé", "type": "person"}],
    ]
    terms = _merge_terms(raw)

    assert len(terms) == 1
    assert terms[0].en == "Richard Lee McNair"
    assert terms[0].pinyin == "Lǐchádé"


def test_malformed_entry_does_not_sink_the_batch():
    raw = [[
        {"zh": "理查德", "vi": "Richard", "en": "Richard", "type": "person"},
        {"zh": "坏了", "type": "không-phải-loại-hợp-lệ"},
    ]]
    assert [t.zh for t in _merge_terms(raw)] == ["理查德"]
