"""Pulling the JSON payload out of a model reply."""

from __future__ import annotations

import json

from zhsub.llm.base import extract_json


def test_plain_object():
    assert json.loads(extract_json('{"a": 1}')) == {"a": 1}


def test_plain_array():
    assert json.loads(extract_json('[{"id": 0}]')) == [{"id": 0}]


def test_object_containing_an_array_is_not_truncated():
    """Preferring "[" over "{" silently discards the surrounding object.

    Real S3 output looked exactly like this: the style block came back empty on
    every run because the extractor returned only the address_terms array and threw
    the rest away. The two other callers hid it, since both accept a bare list.
    """
    raw = """{
      "style": {"speech_register": "kể chuyện bình dân", "narrator_self_vi": "mình"},
      "address_terms": [{"speaker": "A", "addressee": "B"}]
    }"""
    data = json.loads(extract_json(raw))

    assert set(data) == {"style", "address_terms"}
    assert data["style"]["narrator_self_vi"] == "mình"
    assert len(data["address_terms"]) == 1


def test_markdown_fence_is_stripped():
    raw = 'Đây là kết quả:\n```json\n{"translations": [{"id": 0}]}\n```\nHy vọng hữu ích!'
    assert json.loads(extract_json(raw)) == {"translations": [{"id": 0}]}


def test_prose_around_bare_json_is_stripped():
    raw = 'Sure! {"a": [1, 2]} — hope that helps.'
    assert json.loads(extract_json(raw)) == {"a": [1, 2]}


def test_array_before_object_still_wins_when_it_comes_first():
    raw = '[{"a": {"b": 1}}]'
    assert json.loads(extract_json(raw)) == [{"a": {"b": 1}}]


def test_text_without_json_is_returned_unchanged():
    assert extract_json("không có json ở đây") == "không có json ở đây"
