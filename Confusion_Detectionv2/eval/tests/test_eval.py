"""Evaluation harness: metrics checked against hand-computed values."""
import json

import pytest

import eval.predictions as preds_mod
from eval import score as score_mod
from eval.common import SlideEquivalence, load_labels, parse_int_list, question_id
from eval.metrics.calibration import calibration_scores
from eval.metrics.detection import binary_detection, gap_type_scores
from eval.metrics.localization import localization_scores
from eval.metrics.stats import cluster_bootstrap
from eval.predictions import build_predictions
from eval.split import make_split, topic_groups

EQ = SlideEquivalence([{"first": 127, "last": 187, "offset": 61}])


def test_binary_detection_hand_computed():
    gold = ["missing", "thin", "covered", "covered", "off_topic", "scattered"]
    pred = ["thin", "covered", "thin", "covered", "covered", "scattered"]
    d = binary_detection(gold, pred)
    # gaps: gold idx 0,1,5; predicted gaps idx 0,2,5 -> TP 2, FP 1, FN 1, TN 2
    assert (d["tp"], d["fp"], d["fn"], d["tn"]) == (2, 1, 1, 2)
    assert d["precision"] == pytest.approx(2 / 3) and d["recall"] == pytest.approx(2 / 3)
    assert d["f1"] == pytest.approx(2 / 3)
    # Always-gap: TP 3, FP 3 -> P .5, R 1, F1 2/3
    assert d["always_gap_baseline"]["f1"] == pytest.approx(2 / 3)
    assert d["predicted_gap_rate"] == pytest.approx(0.5)


def test_gap_type_macro_f1_only_over_present_classes():
    t = gap_type_scores(["missing", "missing", "covered"], ["missing", "covered", "covered"])
    # missing: P=1 R=.5 F1=2/3; covered: P=.5 R=1 F1=2/3
    assert t["macro_f1"] == pytest.approx(2 / 3)
    assert set(t["classes_absent"]) == {"thin", "scattered", "off_topic"}
    assert t["confusion_matrix"]["missing"]["covered"] == 1


def test_none_prediction_is_not_a_gap():
    assert binary_detection(["missing"], [None])["fn"] == 1


def test_duplicate_slides_are_equivalent():
    assert EQ.canonical(188) == 127 and EQ.canonical(248) == 187
    assert EQ.canonical(127) == 127 and EQ.canonical(249) == 249 and EQ.canonical(10) == 10
    loc = localization_scores([[1]], [[2, 1]], [[150]], [[211]], EQ)  # 211 is the copy of 150
    assert loc["slide_top1"] == 1.0 and loc["topic_top1"] == 0.0 and loc["topic_top3"] == 1.0
    assert loc["topic_mrr"] == pytest.approx(0.5)


def test_calibration_hand_computed():
    c = calibration_scores([0.9, 0.9, 0.1, 0.1], [True, False, False, False], n_bins=5)
    # bin .8-1: conf .9 vs acc .5 -> .4; bin 0-.2: conf .1 vs acc 0 -> .1; ECE = .5*.4 + .5*.1
    assert c["ece"] == pytest.approx(0.25)
    assert c["brier"] == pytest.approx((0.01 + 0.81 + 0.01 + 0.01) / 4)


def test_parse_slide_numbers():
    assert parse_int_list("3; 5-7") == [3, 5, 6, 7]
    assert parse_int_list("") is None and parse_int_list(None) is None


def test_cluster_bootstrap_degenerate_and_basic():
    def mean(xs):
        return sum(xs) / len(xs) if xs else None

    one = cluster_bootstrap([1, 1], ["a", "a"], mean)
    assert one["low"] is None and one["point"] == 1
    ci = cluster_bootstrap([0, 0, 1, 1], ["a", "a", "b", "b"], mean, n_resamples=200)
    assert 0 <= ci["low"] <= ci["point"] <= ci["high"] <= 1


MAPPING = {
    "topics": [
        {"topic_id": 0, "slide_ids": [0, 1, 2], "matched_questions": [
            {"question": "What is X?", "score": 0.9, "best_slide_id": 1},
            {"question": "How does Y work?", "score": 0.5, "best_slide_id": 2}]},
        {"topic_id": 1, "slide_ids": [3, 4], "matched_questions": [
            {"question": "How does Y work?", "score": 0.7, "best_slide_id": 4}]},
    ],
    "unmatched_questions": [{"question": "What is normalization?", "best_score": 0.3, "best_topic_id": 1}],
}
REPORTS = {0: {"gap_type": "covered", "confidence": 0.9}, 1: {"gap_type": "shallow_coverage", "confidence": 0.6},
           -1: {"gap_type": "complete_omission", "confidence": 0.35}}


def test_question_takes_verdict_of_its_best_scoring_topic():
    p = build_predictions(MAPPING, REPORTS)
    y = p[question_id("How does Y work?")]
    assert y["topic_ranking"] == [1, 0] and y["slide_ranking"] == [4, 2]
    assert y["gap_type"] == "thin" and y["confidence"] == 0.6
    assert p[question_id("What is X?")]["gap_type"] == "covered"


def test_unmatched_rule_is_explicit():
    qid = question_id("What is normalization?")
    assert build_predictions(MAPPING, REPORTS)[qid]["gap_type"] == "missing"
    assert build_predictions(MAPPING, REPORTS, unmatched_as="off_topic")[qid]["gap_type"] == "off_topic"


def test_split_never_puts_one_group_on_both_sides():
    topics, questions, mapping_topics = [], [], []
    for i in range(10):
        qs = [f"question {i}-{j}" for j in range(3)]
        questions += qs
        topics.append({"topic_id": i, "slide_ids": [i]})
        mapping_topics.append({"topic_id": i, "slide_ids": [i],
                               "matched_questions": [{"question": q, "score": 0.9, "best_slide_id": i} for q in qs]})
    preds = build_predictions({"topics": mapping_topics, "unmatched_questions": []}, {})
    s = make_split(questions, preds, topics, SlideEquivalence(), 0.3, seed=1)
    assert not set(s["dev"]) & set(s["held_out"])
    assert len(s["dev"]) + len(s["held_out"]) == 30
    for members in s["groups"].values():
        assert set(members) <= set(s["dev"]) or set(members) <= set(s["held_out"])
    # Same seed, same split.
    assert make_split(questions, preds, topics, SlideEquivalence(), 0.3, seed=1)["held_out"] == s["held_out"]


def test_split_merges_duplicate_lecture_topics():
    topics = [{"topic_id": 0, "slide_ids": [130, 131]}, {"topic_id": 1, "slide_ids": [191, 192]},
              {"topic_id": 2, "slide_ids": [5]}]
    g = topic_groups(topics, EQ)
    assert g[0] == g[1] != g[2]


def test_score_end_to_end(tmp_path, monkeypatch):
    (tmp_path / "reports").mkdir()
    for tid, r in REPORTS.items():
        name = "topic_unmatched_report.json" if tid == -1 else f"topic_{tid:02d}_report.json"
        (tmp_path / "reports" / name).write_text(json.dumps({"topic_id": tid, **r}))
    (tmp_path / "mapping.json").write_text(json.dumps(MAPPING))
    qids = [question_id(q) for q in ("What is X?", "How does Y work?", "What is normalization?")]
    (tmp_path / "split.json").write_text(json.dumps(
        {"dev": qids, "held_out": [], "groups": {"g0": qids[:1], "g1": qids[1:2], "u": qids[2:]}}))
    labels = tmp_path / "labels.csv"
    labels.write_text("question_id,question,gap_type,slide_numbers,topic_ids,annotator,notes\n"
                      f"{qids[0]},,covered,2,,,\n{qids[1]},,thin,5,,,\n{qids[2]},,off_topic,,,,\n")

    def fake_resolve(config, key):
        return tmp_path / config[key]

    monkeypatch.setattr(score_mod, "resolve", fake_resolve)
    monkeypatch.setattr(score_mod, "load_pipeline_predictions",
                        lambda c, u="report": build_predictions(MAPPING, preds_mod.load_reports(tmp_path / "reports"), u))
    config = {"question_mapping": "mapping.json", "reports_dir": "reports", "split": "split.json"}
    m = score_mod.score(config, load_labels(labels), qids, n_resamples=50)
    assert m["counts"]["labelled"] == 3
    # X covered/covered; Y thin/thin; normalization gold off_topic, predicted missing -> a false gap.
    assert (m["detection"]["tp"], m["detection"]["fp"]) == (1, 1)
    assert m["localization"]["slide_top1"] == 1.0  # slide number 5 = slide_id 4 = Y's top slide
    assert m["stage3_unmatched_vs_off_topic"]["recall"] == 1.0
    md = score_mod.render_markdown({"deck_id": "t", "split": "dev", "run_id": "r", "git_revision": "",
                                    "git_dirty": False, "labels": "l", "unmatched_as": "report"}, m)
    assert "Gap detection" in md


def test_unmatched_per_question_verdicts_override_the_group_type():
    reports = dict(REPORTS)
    reports[-1] = {"gap_type": "shallow_coverage", "confidence": 0.7,
                   "question_verdicts": [{"question": "What is normalization?", "verdict": "off_topic"}]}
    p = build_predictions(MAPPING, reports)
    assert p[question_id("What is normalization?")]["gap_type"] == "off_topic"


def test_failed_investigation_gives_no_verdict():
    reports = dict(REPORTS)
    reports[1] = {"gap_type": "shallow_coverage", "confidence": 0.0}
    p = build_predictions(MAPPING, reports)
    assert p[question_id("How does Y work?")]["gap_type"] is None
    assert p[question_id("What is X?")]["gap_type"] == "covered"


def test_a_question_uses_its_own_verdict_when_the_report_has_one():
    reports = dict(REPORTS)
    reports[0] = {"gap_type": "shallow_coverage", "confidence": 0.8,
                  "question_verdicts": [{"question": "What is X?", "verdict": "covered"}]}
    p = build_predictions(MAPPING, reports)
    assert p[question_id("What is X?")]["gap_type"] == "covered"   # its own verdict, not the topic's "thin"
