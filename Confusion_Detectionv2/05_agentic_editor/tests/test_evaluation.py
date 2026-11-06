"""Numeric evaluation checks, including missing-label and stale-review failures."""
import copy

import pytest

from ..src.evaluation import evaluate, render_markdown, review_template


def proposal(accepted=True, revisions=0, operation="insert_slide_after", topic=1):
    return {"topic_id": topic, "after_slide_id": 5, "title": "An explanation", "bullets": ["A point"],
            "addresses_questions": ["Why?"], "operation": operation,
            "accepted": accepted, "problems": [] if accepted else ["fact-check, bullet 1: Wrong"],
            "revisions": revisions}


def sample():
    return {"edits": [proposal(), proposal(False, 2), proposal(True, 1),
                      proposal(operation="repair_slide", topic=-2)]}


def test_automatic_counts_include_blocked_and_do_not_count_mirrored_slides():
    snapshot = sample()
    snapshot["edits"][0]["mirrored_after_slide_ids"] = [50]
    result = evaluate(snapshot)
    a = result["automatic"]
    assert a["proposals"] == 4 and a["blocked"] == 1
    assert a["recorded_pass_rate"]["value"] == .75
    assert a["revised_proposal_rate"]["value"] == .5
    assert a["accepted_among_revised"]["value"] == .5
    assert a["mean_recorded_revision_round"]["value"] == .75
    assert a["topics_with_accepted_gap_edits"] == 1
    assert a["claimed_questions_in_accepted_edits"] == 1
    assert a["by_operation"]["repair_slide"] == {"proposals": 1, "accepted": 1}
    assert result["human"] is None


def test_empty_run_has_undefined_rates_not_perfect_scores():
    result = evaluate({"edits": []})
    assert result["automatic"]["recorded_pass_rate"]["value"] is None
    assert "Not measured" in render_markdown(result)


def test_supports_demo_format_and_keeps_repairs():
    result = evaluate({"topics": {"1": [proposal(False)]}, "repairs": {"5": [proposal(operation="repair_slide")]}})
    assert result["automatic"]["proposals"] == 2
    assert result["automatic"]["recorded_pass_rate"]["value"] == .5


def test_unreviewed_template_does_not_infer_usefulness_or_coverage():
    snapshot = sample()
    template = review_template(snapshot, ["Why?", "Why?", "Other?"])
    h = evaluate(snapshot, template)["human"]
    assert len(template["questions"]) == 2
    assert h["edit_review_completion"]["value"] == 0
    assert h["usable_rate_among_reviewed"]["value"] is None
    assert h["coverage"]["before"]["value"] is None
    assert h["coverage"]["improvement_percentage_points"] is None


def test_partial_reviews_expose_both_denominators_and_criterion_counts():
    snapshot = sample()
    template = review_template(snapshot)
    template["reviewer"] = "Instructor A"
    for row in template["edits"][:2]:
        row["criteria"] = dict.fromkeys(row["criteria"], True)
    template["edits"][1]["criteria"]["factual_correctness"] = False
    template["edits"][2]["criteria"]["readability"] = False
    h = evaluate(snapshot, template)["human"]
    assert h["edit_review_completion"]["value"] == .5
    assert h["usable_rate_among_reviewed"]["value"] == .5
    assert h["confirmed_usable_yield_all_proposals"]["value"] == .25
    assert h["usable_rate_among_reviewed_accepted"]["value"] == 1
    assert h["accepted_edit_review_completion"]["value"] == 1 / 3
    assert h["criterion_pass_rates"]["readability"]["value"] == 2 / 3


def test_coverage_uses_paired_labels_and_reports_regressions():
    snapshot = sample()
    template = review_template(snapshot, ["q1", "q2", "q3", "unpaired", "unreviewed"])
    template.update(reviewer="Instructor A", original_deck="original.pptx", edited_deck="actual-output.pptx")
    for row, before, after in zip(template["questions"], [False, False, True, True], [True, True, False, None]):
        row.update(before=before, after=after)
    c = evaluate(snapshot, template)["human"]["coverage"]
    assert c["paired_review_completion"]["value"] == 3 / 5
    assert c["before"]["value"] == 1 / 3
    assert c["after"]["value"] == 2 / 3
    assert c["improvement_percentage_points"] == pytest.approx(100 / 3)
    assert c["newly_answered"] == 2 and c["regressions"] == 1


def test_rejects_reviews_for_changed_snapshot():
    snapshot = sample()
    template = review_template(snapshot)
    snapshot["edits"][0]["bullets"] = ["Changed"]
    with pytest.raises(ValueError, match="snapshot"):
        evaluate(snapshot, template)


@pytest.mark.parametrize("mutation", ["omitted", "duplicate", "unknown"])
def test_cannot_filter_proposals_out_of_review_denominator(mutation):
    snapshot = sample()
    template = review_template(snapshot)
    if mutation == "omitted":
        template["edits"].pop()
    elif mutation == "duplicate":
        template["edits"].append(copy.deepcopy(template["edits"][0]))
    else:
        template["edits"][0]["edit_id"] = "unknown"
    with pytest.raises(ValueError, match="every proposal"):
        evaluate(snapshot, template)


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_rejects_non_boolean_human_labels(value):
    snapshot = sample()
    template = review_template(snapshot)
    template["edits"][0]["criteria"]["readability"] = value
    with pytest.raises(ValueError, match="true, false, or null"):
        evaluate(snapshot, template)


def test_labels_need_review_provenance_and_named_decks():
    snapshot = sample()
    template = review_template(snapshot, ["q1"])
    template["questions"][0].update(before=False, after=True)
    with pytest.raises(ValueError, match="reviewer"):
        evaluate(snapshot, template)
    template["reviewer"] = "Instructor A"
    with pytest.raises(ValueError, match="original_deck"):
        evaluate(snapshot, template)


def test_duplicate_questions_are_rejected_and_zero_change_is_valid():
    snapshot = sample()
    template = review_template(snapshot, ["q1"])
    template.update(reviewer="Instructor A", original_deck="before", edited_deck="after")
    template["questions"][0].update(before=False, after=False)
    assert evaluate(snapshot, template)["human"]["coverage"]["improvement_percentage_points"] == 0
    template["questions"].append(copy.deepcopy(template["questions"][0]))
    with pytest.raises(ValueError, match="unique"):
        evaluate(snapshot, template)


def test_rejects_inconsistent_gate_results_and_bare_change_logs():
    snapshot = sample()
    snapshot["edits"][0]["problems"] = ["Blocked"]
    with pytest.raises(ValueError, match="contradicts"):
        evaluate(snapshot)
    with pytest.raises(ValueError, match="accepted"):
        evaluate({"edits": [{k: v for k, v in proposal().items() if k != "accepted"}]})


@pytest.mark.parametrize("field", ["edits", "questions", "criteria"])
def test_malformed_uploaded_forms_raise_readable_validation_errors(field):
    snapshot = sample()
    template = review_template(snapshot)
    if field == "criteria":
        template["edits"][0]["criteria"] = None
    else:
        template[field] = [None]
    with pytest.raises(ValueError):
        evaluate(snapshot, template)


def test_non_object_upload_is_rejected():
    with pytest.raises(ValueError, match="JSON object"):
        evaluate(sample(), [])
