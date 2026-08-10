"""Khối glossary bơm vào system prompt của S4."""

from __future__ import annotations

from zhsub.models import AddressTerm, GlossaryDoc, StyleDecision
from zhsub.stages.s4_translate import _glossary_block


def test_third_person_form_is_pinned_for_narration():
    """Rule 3 chỉ liệt kê thực đơn "anh ta" / "cô ta" / "ông ấy" nên mỗi batch chọn
    lại từ đầu. Đo trên video 472 câu: dạng trội chỉ chiếm 77-90% tuỳ cách gộp
    thread, và có lần model chốt vào "anh" — dạng rule 3 còn không liệt kê.
    """
    doc = GlossaryDoc(style=StyleDecision(subject_third_person_vi="anh ta"))

    block = _glossary_block(doc, "vi")

    assert "narration calls the main subject: anh ta" in block
    assert "never switch to another third-person form" in block


def test_nothing_is_pinned_when_the_field_is_empty():
    doc = GlossaryDoc(style=StyleDecision(speech_register="kể chuyện"))

    block = _glossary_block(doc, "vi")

    assert "tone: kể chuyện" in block
    assert "main subject" not in block


def test_the_register_block_is_vietnamese_only():
    """Các trường xưng hô đều là quyết định riêng của tiếng Việt."""
    doc = GlossaryDoc(style=StyleDecision(subject_third_person_vi="anh ta"))

    assert _glossary_block(doc, "en") == ""


def test_address_terms_stay_out_of_narration():
    """Thả tự do thì model áp cả vào lời dẫn và lật ngôi: 他进行了一番伪装 từng ra
    'mình đã cải trang'. Ràng buộc này phải còn nguyên sau khi thêm trường mới."""
    doc = GlossaryDoc(
        style=StyleDecision(subject_third_person_vi="anh ta"),
        address_terms=[
            AddressTerm(speaker="A", addressee="B", vi_self="tôi", vi_other="anh")
        ],
    )

    block = _glossary_block(doc, "vi")

    assert "apply ONLY inside direct speech" in block
    assert "NEVER use these in narration" in block
