"""Slide references and topic names as a reader sees them."""
from types import SimpleNamespace

from ..src.display import report_text_for_display, topic_title


def test_old_reports_get_slide_numbers_not_ids():
    text = "Topic 2 (slides 16‑21): slide 16 states it; see slides 34, 36 and 54-56; slide range (80-106)."
    assert report_text_for_display(text, None) == (
        "Topic 2 (slides 17‑22): slide 17 states it; see slides 35, 37 and 55-57; slide range (81-107).")
    assert report_text_for_display(text, "id") == report_text_for_display(text, None)


def test_new_reports_are_left_as_written():
    assert report_text_for_display("slide 17", "number") == "slide 17"


def test_numbers_that_are_not_slide_references_are_untouched():
    assert report_text_for_display("Topic 6 has 15 questions and 3 parts.", None) == "Topic 6 has 15 questions and 3 parts."


def _deck(titles):
    slides = {i: SimpleNamespace(title=t) for i, t in enumerate(titles)}
    return SimpleNamespace(get=slides.get)


def test_topic_title_skips_closing_slides_recaps_and_fused_headings():
    deck = _deck(["THANK YOU", "Summary Use namespaces", "Advantages Faster Time to Market – checkins more often",
                  "Binary Translation (Cont.)", "Binary Translation"])
    assert topic_title(deck, range(5)) == "Binary Translation"
    assert topic_title(_deck(["Definition ▪ DevOps is a set of practices"]), [0]) == "Untitled topic"
    assert topic_title(_deck(["DevOps Definition"]), [0]) == "DevOps Definition"
    assert topic_title(_deck([None, ""]), [0, 1], fallback="Unmatched") == "Unmatched"
