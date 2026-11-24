import hashlib
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from common import deck_workspace
from ..src.web_api import create_app

# The tests run against the Cloud Computing deck workspace (real data, 288 slides, 140 questions).
TEST_DECK = "cc-unit2"
SOURCE = deck_workspace.load(TEST_DECK).edit_source


@pytest.fixture
def client(tmp_path):
    app = create_app(state_dir=tmp_path, deck_id=TEST_DECK, include_saved=True)
    with TestClient(app) as client:
        client.headers["x-workspace-token"] = client.get("/api/workspace").json()["token"]
        yield client


def test_workspace_and_evidence_are_real_and_do_not_expose_credentials(client):
    response = client.get("/api/workspace")
    assert response.status_code == 200
    data = response.json()
    assert data["slide_count"] == 288 and data["question_count"] == 140
    assert "api_key" not in data and "gsk_" not in response.text
    topic = next(t for t in data["topics"] if t["eligible"])
    response = client.get(f"/api/topics/{topic['id']}")
    assert response.status_code == 200 and response.json()["questions"]
    evidence = response.json()
    assert len(evidence["slides"]) == len(topic["slide_ids"])
    assert [q["question"] for q in evidence["question_links"]] == evidence["questions"]
    assert all(q["slide_id"] is None or q["slide_id"] in topic["slide_ids"] for q in evidence["question_links"])
    assert any(q["slide_id"] is not None for q in evidence["question_links"])

    assert client.get("/api/topics/9999").status_code == 404
    assert client.get("/api/slides/-1/image").status_code == 404


def test_mutations_require_session_token_and_allowed_origin(client):
    key = client.get("/api/proposals").json()[0]["id"]
    assert client.post(f"/api/proposals/{key}/decision", json={"decision": "rejected"}, headers={"x-workspace-token": "wrong"}).status_code == 403
    assert client.post(f"/api/proposals/{key}/decision", json={"decision": "rejected"}, headers={"origin": "https://untrusted.example"}).status_code == 403
    assert client.post(f"/api/proposals/{key}/decision", json={"decision": "rejected", "title": "injected"}).status_code == 422


def test_check_findings_are_advisory_but_explicit_approval_is_required(client):
    rows = client.get("/api/proposals").json()
    assert all(p["decision"] == "pending" for p in rows)
    blocked = next(p for p in rows if not p["accepted"])
    assert client.post("/api/apply", json={"proposal_ids": [rows[0]["id"]]}).status_code == 409
    assert client.post("/api/apply", json={"proposal_ids": [rows[0]["id"], rows[0]["id"]]}).status_code == 422
    assert client.post("/api/apply", json={"proposal_ids": ["unknown"]}).status_code == 404
    assert client.get("/api/exports/not-a-real-export/deck").status_code == 404
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert client.post(f"/api/proposals/{blocked['id']}/decision", json={"decision": "approved"}).status_code == 200
    approved = next(p for p in client.get('/api/proposals').json() if p['id'] == blocked['id'])
    assert not approved['accepted'] and approved['problems'] == blocked['problems']
    response = client.post('/api/apply', json={'proposal_ids': [blocked['id']]})
    assert response.status_code == 200, response.text
    assert client.get(response.json()['download_url']).content.startswith(b'PK')
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before


def test_clearing_explicit_approval_disables_export_again(client):
    row = next(p for p in client.get('/api/proposals').json() if p['accepted'])
    endpoint = f"/api/proposals/{row['id']}/decision"
    assert client.post(endpoint, json={'decision': 'approved'}).status_code == 200
    assert client.post(endpoint, json={'decision': 'pending'}).status_code == 200
    assert client.post('/api/apply', json={'proposal_ids': [row['id']]}).status_code == 409


def test_real_export_requires_approval_and_preserves_source(client):
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    rows = client.get("/api/proposals").json()
    proposal = next(p for p in rows if p["accepted"] and p["operation"] == "insert_slide_after")
    decision = client.post(f"/api/proposals/{proposal['id']}/decision", json={"decision": "approved"})
    assert decision.status_code == 200, decision.text
    response = client.post("/api/apply", json={"proposal_ids": [proposal["id"]]})
    assert response.status_code == 200, response.text
    result = response.json()
    deck = client.get(result["download_url"])
    assert deck.status_code == 200 and deck.content.startswith(b"PK")
    changes = client.get(result["changes_url"]).json()
    assert len(changes["edits"]) == 1
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before


def test_evaluation_keeps_human_results_unmeasured_and_rejects_stale_reviews(client):
    metrics = client.get("/api/evaluation").json()
    assert metrics["human"] is None
    template = client.get("/api/evaluation/template").json()
    assert len(template["questions"]) == 140
    result = client.post("/api/evaluation", json=template)
    assert result.status_code == 200
    assert result.json()["human"]["usable_rate_among_reviewed"]["value"] is None
    template["snapshot_id"] = "changed"
    assert client.post("/api/evaluation", json=template).status_code == 422


def test_api_render_endpoints_return_png(client):
    response = client.get("/api/slides/0/image")
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
    row = next(p for p in client.get("/api/proposals").json() if p["accepted"])
    response = client.get(f"/api/proposals/{row['id']}/image")
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")


def test_unknown_task_and_unknown_cancellation_are_rejected(client):
    assert client.post("/api/runs/unknown/cancel").status_code == 409
    assert client.get("/api/runs/unknown").status_code == 404
    assert client.post("/api/runs", json={"kind": "shell", "target_id": 1}).status_code == 422


def wait_for_terminal(client, key):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(f"/api/runs/{key}").json()
        if result["status"] not in ("queued", "running"):
            return result
        time.sleep(.02)
    raise AssertionError("Run did not finish")


@pytest.mark.parametrize("targeted", [False, True])
def test_live_worker_checkpoints_without_applying_any_edits(tmp_path, targeted):
    answer = {}
    prompts = []
    class Scripted:
        model = "test-fixture"
        max_tokens = 3000
        def generate(self, system, user):
            prompts.append((system, user))
            return '{"problems": []}' if 'You fact-check' in system else json.dumps({"edits": [answer]})
    app = create_app(state_dir=tmp_path, generator_factory=Scripted, deck_id=TEST_DECK, include_saved=True)
    with TestClient(app) as client:
        client.headers["x-workspace-token"] = client.get('/api/workspace').json()['token']
        row = next(p for p in client.get('/api/proposals').json() if p['accepted'] and p['operation'] == 'insert_slide_after')
        answer.update({k: row[k] for k in ('operation', 'after_slide_id', 'title', 'bullets', 'addresses_questions')})
        body = {'kind': 'gap', 'target_id': row['topic_id']}
        if targeted:
            body['question'] = row['addresses_questions'][0]
            assert client.post('/api/runs', json={**body, 'question': 'Not a question from this topic'}).status_code == 422
        run = client.post('/api/runs', json=body).json()
        result = wait_for_terminal(client, run['id'])
        assert result['status'] == 'completed', result
        if targeted:
            assert result['question'] == body['question']
            planner_prompt = next(user for system, user in prompts if 'You write new lecture slides' in system)
            assert planner_prompt.split('Student questions matched to this topic:\n')[1].strip() == '- ' + body['question']

        assert result['proposal_ids']
        assert any(e['event'] == 'initial_review' for e in result['events'])
        assert any(e['event'] == 'checkpoint_saved' for e in result['events'])
        assert not (tmp_path / 'exports').exists()
        assert all(p['decision'] == 'pending' for p in client.get('/api/proposals').json())


def test_bounded_queue_duplicate_prevention_and_cancellation(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class Waiting:
        model = 'test-fixture'
        def generate(self, system, user):
            entered.set()
            release.wait(5)
            return '{"edits": []}'
    app = create_app(state_dir=tmp_path, generator_factory=Waiting, deck_id=TEST_DECK, include_saved=True)
    with TestClient(app) as client:
        workspace = client.get('/api/workspace').json()
        client.headers['x-workspace-token'] = workspace['token']
        targets = [t['id'] for t in workspace['topics'] if t['eligible']][:5]
        response = client.post('/api/runs', json={'kind': 'gap', 'target_id': targets[0]})
        first = response.json()['id']
        assert entered.wait(2)
        assert client.post('/api/runs', json={'kind': 'gap', 'target_id': targets[0]}).status_code == 409
        ids = [first]
        for target in targets[1:4]:
            response = client.post('/api/runs', json={'kind': 'gap', 'target_id': target})
            assert response.status_code == 202
            ids.append(response.json()['id'])
        assert client.post('/api/runs', json={'kind': 'gap', 'target_id': targets[4]}).status_code == 429
        for key in ids:
            assert client.post(f'/api/runs/{key}/cancel').status_code == 200
        release.set()
        for key in ids:
            result = wait_for_terminal(client, key)
            assert result['status'] == 'cancelled'
            assert not any(e['event'] == 'checkpoint_saved' for e in result['events'])


def test_request_body_and_host_limits(client):
    response = client.post('/api/evaluation', content=b'x' * 1_000_001, headers={'content-type': 'application/json'})
    assert response.status_code == 413
    assert client.get('/api/workspace', headers={'host': 'untrusted.example'}).status_code == 400


def test_by_default_no_pre_generated_suggestions_are_shown(tmp_path):
    with TestClient(create_app(state_dir=tmp_path, deck_id=TEST_DECK, include_saved=True)) as client:
        assert any(p["source"] == "saved" for p in client.get("/api/proposals").json())
    # Same workspace state, reopened without saved suggestions: they are gone.
    with TestClient(create_app(state_dir=tmp_path, deck_id=TEST_DECK)) as client:
        assert client.get("/api/proposals").json() == []


def test_assessment_covers_every_question_without_changing_approvals(tmp_path):
    class Assessor:
        model = "test-assessment"
        max_tokens = 1500
        def generate(self, system, user):
            evidence = json.loads(user)
            slide_id = evidence["original_slides"][0]["slide_id"]
            return json.dumps({"verdicts": [{"question": q, "verdict": "covered",
                "reason": "The original source explains this question.", "slide_ids": [slide_id]}
                for q in evidence["questions"]]})
    app = create_app(state_dir=tmp_path, generator_factory=Assessor, deck_id=TEST_DECK, include_saved=True)
    with TestClient(app) as client:
        workspace = client.get('/api/workspace').json()
        client.headers['x-workspace-token'] = workspace['token']
        target = next(t for t in workspace['topics'] if t['id'] >= 0 and t['question_count'] > 3)['id']
        before = client.get('/api/proposals').json()
        response = client.post('/api/runs', json={'kind': 'assessment', 'target_id': target})
        assert response.status_code == 202
        result = wait_for_terminal(client, response.json()['id'])
        assert result['status'] == 'completed', result
        evidence = client.get(f'/api/topics/{target}').json()
        assert [v['question'] for v in evidence['report']['question_verdicts']] == list(dict.fromkeys(evidence['questions']))
        assert all(v['verdict'] == 'covered' and v['reason'] and v['slide_ids'] for v in evidence['report']['question_verdicts'])
        assert client.get('/api/proposals').json() == before
        assert not (tmp_path / 'exports').exists()


def test_draft_is_a_new_full_deck_without_approving_pending_proposals(client):
    rows = client.get('/api/proposals').json()
    checked = next(row for row in rows if row['accepted'] and row['operation'] == 'insert_slide_after')
    original = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    response = client.post('/api/draft', json={'proposal_ids': [checked['id']]})
    assert response.status_code == 200, response.text
    assert response.json()['draft'] is True
    content = client.get(response.json()['download_url']).content
    from pptx import Presentation
    import io
    assert len(Presentation(io.BytesIO(content)).slides) > len(Presentation(str(SOURCE)).slides)
    assert client.get('/api/proposals').json() == rows
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == original
    assert client.post('/api/apply', json={'proposal_ids': [checked['id']]}).status_code == 409
    assert client.post(f"/api/proposals/{checked['id']}/decision", json={'decision': 'rejected'}).status_code == 200
    assert client.post('/api/draft', json={'proposal_ids': [checked['id']]}).status_code == 409
