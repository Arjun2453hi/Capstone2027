"""05_agentic_editor: turn gap reports into slide edits and apply them
to a copy of the deck. See CLAUDE.md."""
from .checker import check_edit, check_edits
from .planner import EditGenerator, EditPlanner, GroqEditGenerator, skip_reason
from .schema import AppliedEdit, CheckedEdit, SlideEdit
from .verifier import EditVerifier, verify_checked

__all__ = [
    "EditPlanner",
    "EditGenerator",
    "GroqEditGenerator",
    "skip_reason",
    "check_edit",
    "check_edits",
    "EditVerifier",
    "verify_checked",
    "SlideEdit",
    "CheckedEdit",
    "AppliedEdit",
]
