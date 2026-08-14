"""Cứu khối style khi phần sau của JSON vỡ."""

from __future__ import annotations

from zhsub.stages.s3_glossary import _salvage_style

# Nguyên văn kiểu trả lời đã làm hỏng một video thật: style nguyên vẹn ở đầu,
# address_terms phía sau lọt dấu nháy không escape trong trường "basis".
BROKEN = (
    '{"style":{"speech_register":"kể chuyện án hình sự nghiêm túc",'
    '"narrator_self_vi":"mình","audience_vi":"các bạn",'
    '"subject_third_person_vi":"cô ta"},'
    '"address_terms":[{"speaker":"A","addressee":"B","basis":"gọi "anh" cho thân"}]}'
)


def test_the_intact_style_block_is_recovered_from_broken_json():
    data = _salvage_style(BROKEN)

    assert data["style"]["subject_third_person_vi"] == "cô ta"
    assert data["style"]["narrator_self_vi"] == "mình"


def test_valid_json_still_works():
    data = _salvage_style('{"style":{"audience_vi":"các bạn"},"address_terms":[]}')

    assert data["style"]["audience_vi"] == "các bạn"


def test_nested_braces_inside_style_do_not_end_it_early():
    raw = '{"style":{"a":"x","b":{"c":"y"}},"address_terms":[broken'

    assert _salvage_style(raw)["style"]["b"] == {"c": "y"}


def test_a_reply_without_a_style_block_gives_nothing():
    assert _salvage_style('{"address_terms":[]}') is None
    assert _salvage_style("xin lỗi, tôi không thể trả lời") is None


def test_a_broken_style_block_itself_gives_nothing():
    """Cứu được thì cứu, hỏng hẳn thì đừng đoán."""
    assert _salvage_style('{"style":{"a":"x" "b":"y"},') is None
