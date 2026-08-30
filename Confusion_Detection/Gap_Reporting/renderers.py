"""ReportRenderer abstraction + JSONRenderer + MarkdownRenderer.

Same DI posture as every prior module. A future DocxRenderer (via the
project's `docx` skill) could be added behind this same interface if a
Word deliverable is wanted later — not built now, per claude.md
Section 5.4.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import asdict

from .schema import GapReport

# Real clusters (is_noise=False) are always shown in full -- there are
# only ~36 of them on the real dataset, each backed by multiple
# students, so all of them are worth an instructor's attention.
# Singletons are the long tail (409 on real data) where most are
# individually low-value by construction (Gap_Verification's own
# framing: "far less weight than a 51-question cluster") -- capping to
# the strongest MAX_SINGLETON_ENTRIES_SHOWN by severity keeps the
# human-facing report readable without hiding the genuinely high-
# severity singletons. 15 is a round, revisitable number (claude.md
# Section 5.2), not derived from anything more principled than "a
# reasonable-length top-N list."
MAX_SINGLETON_ENTRIES_SHOWN = 15


class ReportRenderer(ABC):
    @abstractmethod
    def render(self, report: GapReport) -> str:
        raise NotImplementedError


class JSONRenderer(ReportRenderer):
    """Canonical, complete, machine-readable output. Every entry,
    including every low-confidence singleton, always appears here --
    this is the source of truth; MarkdownRenderer's curation is a
    presentation choice layered on top, never a data-loss one."""

    def render(self, report: GapReport) -> str:
        return json.dumps(asdict(report), indent=2, ensure_ascii=False)


class MarkdownRenderer(ReportRenderer):
    """The human-facing report: grouped by Module -> Slide Range,
    severity-sorted, singleton entries capped (see
    MAX_SINGLETON_ENTRIES_SHOWN above)."""

    def render(self, report: GapReport) -> str:
        lines = [
            "# Gap Report",
            "",
            f"Generated: {report.generated_at}",
            f"Topics considered: {report.total_topics_considered}",
            f"Gaps reported: {report.total_gaps_reported}",
            "",
        ]

        if not report.module_grouping_available:
            lines.append(
                "> Module grouping is not available yet — Step 1's module "
                "grouper hasn't been built (`module_id` is `null` on every "
                "slide). Entries below are grouped by slide range only."
            )
            lines.append("")

        real_entries = [e for e in report.entries if not e.is_noise]
        singleton_entries = [e for e in report.entries if e.is_noise]
        shown_singletons = singleton_entries[:MAX_SINGLETON_ENTRIES_SHOWN]
        hidden_singleton_count = len(singleton_entries) - len(shown_singletons)

        shown_entries = real_entries + shown_singletons
        # Re-sort: real_entries + shown_singletons were each already
        # severity-sorted internally (both are slices of the
        # already-sorted report.entries), but concatenating the two
        # groups doesn't preserve one global severity order across
        # them -- re-sort once so the rendered list truly reads
        # highest-severity-first regardless of real-vs-singleton origin.
        shown_entries.sort(key=lambda e: -e.severity)

        by_module = defaultdict(list)
        for entry in shown_entries:
            by_module[entry.module_id].append(entry)

        # None (ungrouped / module grouping unavailable) last only if
        # real modules exist alongside it; with today's real data every
        # entry is None, so this is the only section that renders.
        module_keys = sorted((k for k in by_module if k is not None)) + (
            [None] if None in by_module else []
        )

        for module_id in module_keys:
            header = f"## Module {module_id}" if module_id is not None else "## Ungrouped"
            lines.append(header)
            lines.append("")
            for entry in by_module[module_id]:
                lines.extend(self._render_entry(entry))

        if hidden_singleton_count > 0:
            lines.append("---")
            lines.append(
                f"{hidden_singleton_count} additional low-confidence "
                f"single-question findings recorded in gap_report.json."
            )

        return "\n".join(lines)

    @staticmethod
    def _render_entry(entry) -> list:
        singleton_tag = " (singleton)" if entry.is_noise else ""
        block = [
            f"### {entry.slide_range_label} — {entry.gap_type} (severity {entry.severity:.2f})",
            f"- Backed by {entry.backed_by_questions} question(s){singleton_tag}",
            f"- Confidence: {entry.confidence:.2f}",
            f"- Guidance: {entry.guidance}",
            f"- Suggested addition: {entry.suggested_addition or '(none drafted)'}",
            "",
        ]
        return block
