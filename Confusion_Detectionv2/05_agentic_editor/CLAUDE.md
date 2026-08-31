# CLAUDE.md — 05_agentic_editor

Consumes `04_gap_reporting_agent`'s per-topic reports and edits the actual deck
autonomously — new scope that wasn't part of the old build, where a
human was expected to act on the report by hand. Not built yet, and
genuinely open design questions remain here (how an edit is scoped to
a specific slide safely, how a proposed edit gets validated before
being applied, what happens when an edit's confidence is too low to
act on automatically) that need settling when this stage is actually
reached, not guessed at now, per the project root CLAUDE.md's build
order.
