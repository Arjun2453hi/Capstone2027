"""JSONRenderer + MarkdownRenderer tests: completeness, module fallback,
singleton curation."""
from __future__ import annotations

import json
import re

from ..aggregation import SimpleAggregator
from ..renderers import MAX_SINGLETON_ENTRIES_SHOWN, JSONRenderer, MarkdownRenderer
from ..report_builder import ReportBuilder
from ..severity import DefaultSeverityScorer
from .fixtures.sample_verdicts import ALL_VERDICTS, REAL_CLUSTER_VERDICTS, SINGLETON_VERDICTS, make_deck


def _build_report(deck=None, verdicts=ALL_VERDICTS):
    builder = ReportBuilder(DefaultSeverityScorer(), SimpleAggregator(), deck or make_deck())
    return builder.build(verdicts)


def test_json_renderer_includes_every_entry_including_all_singletons():
    report = _build_report()
    rendered = json.loads(JSONRenderer().render(report))
    assert len(rendered["entries"]) == len(REAL_CLUSTER_VERDICTS) + len(SINGLETON_VERDICTS)
    # No curation in JSON -- every singleton topic_id must be present.
    rendered_topic_ids = {e["topic_id"] for e in rendered["entries"]}
    for v in SINGLETON_VERDICTS:
        assert v.topic_id in rendered_topic_ids


def test_json_renderer_never_includes_covered_verdicts():
    report = _build_report()
    rendered = json.loads(JSONRenderer().render(report))
    assert all(e["gap_type"] != "covered" for e in rendered["entries"])


def test_markdown_states_module_grouping_unavailable_when_null_everywhere():
    report = _build_report(deck=make_deck(with_modules=False))
    rendered = MarkdownRenderer().render(report)
    print(rendered[:400])
    assert "Module grouping is not available" in rendered
    assert "## Ungrouped" in rendered
    assert report.module_grouping_available is False


def test_markdown_does_not_crash_and_groups_by_module_when_available():
    report = _build_report(deck=make_deck(with_modules=True))
    rendered = MarkdownRenderer().render(report)
    assert report.module_grouping_available is True
    assert "## Module 3" in rendered
    assert "## Module 7" in rendered
    assert "Module grouping is not available" not in rendered


def test_markdown_caps_singleton_entries_and_notes_the_remainder():
    report = _build_report()
    rendered = MarkdownRenderer().render(report)

    # Count singleton headers by their known slide numbers (slide_ids 150..169 -> slide_numbers 151..170)
    singleton_headers_shown = sum(1 for v in SINGLETON_VERDICTS if f"Slide {v.slide_ids[0] + 1}" in rendered)

    print(f"singleton headers shown in markdown: {singleton_headers_shown} (cap={MAX_SINGLETON_ENTRIES_SHOWN})")
    assert singleton_headers_shown == MAX_SINGLETON_ENTRIES_SHOWN
    expected_hidden = len(SINGLETON_VERDICTS) - MAX_SINGLETON_ENTRIES_SHOWN
    assert f"{expected_hidden} additional low-confidence single-question findings recorded in gap_report.json." in rendered


def test_markdown_always_shows_all_real_cluster_entries_uncapped():
    report = _build_report()
    rendered = MarkdownRenderer().render(report)
    for v in REAL_CLUSTER_VERDICTS:
        assert f"Slide {v.slide_ids[0] + 1}" in rendered or f"Slides {v.slide_ids[0] + 1}" in rendered


def test_markdown_never_mentions_covered_verdicts():
    report = _build_report()
    rendered = MarkdownRenderer().render(report)
    assert "adequately covered" not in rendered


def test_markdown_lists_entries_in_severity_descending_order():
    report = _build_report()
    rendered = MarkdownRenderer().render(report)
    severities_in_order = [float(s) for s in re.findall(r"severity (-?\d+\.\d+)", rendered)]
    assert severities_in_order == sorted(severities_in_order, reverse=True)
