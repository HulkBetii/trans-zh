"""Ép thuật ngữ về đúng cách dịch đã định."""

from __future__ import annotations

from zhsub.models import GlossaryDoc, GlossaryTerm
from zhsub.stages.s4_translate import apply_glossary

FBI = GlossaryDoc(
    terms=[GlossaryTerm(zh="FBI", vi="Cục Điều tra Liên bang Mỹ (FBI)", en="FBI")]
)


def test_a_rendering_containing_the_source_form_does_not_nest():
    """Model áp glossary đúng như được dặn, rồi hàm này áp lần nữa.

    Đo trên bản dịch thật: "Cục Điều tra Liên bang Mỹ (Cục Điều tra Liên bang Mỹ
    (FBI))" — 102 ký tự trên cue 2.3 giây, ngân sách 48.
    """
    already_done = "Vật chứng quan trọng của Cục Điều tra Liên bang Mỹ (FBI), không được chạm."

    assert apply_glossary(already_done, FBI, "vi") == already_done


def test_the_bare_source_form_is_still_expanded():
    assert (
        apply_glossary("Vật chứng quan trọng của FBI.", FBI, "vi")
        == "Vật chứng quan trọng của Cục Điều tra Liên bang Mỹ (FBI)."
    )


def test_applying_twice_changes_nothing():
    once = apply_glossary("FBI đã đến.", FBI, "vi")

    assert apply_glossary(once, FBI, "vi") == once


def test_a_mix_of_done_and_undone_ends_up_consistent():
    text = "FBI và Cục Điều tra Liên bang Mỹ (FBI) là một."
    expected = "Cục Điều tra Liên bang Mỹ (FBI) và Cục Điều tra Liên bang Mỹ (FBI) là một."

    assert apply_glossary(text, FBI, "vi") == expected


def test_keep_source_terms_are_left_alone():
    doc = GlossaryDoc(terms=[GlossaryTerm(zh="FBI", vi="Cục ...", keep_source=True)])

    assert apply_glossary("FBI đã đến.", doc, "vi") == "FBI đã đến."
