from datetime import timedelta, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from fastapi import Response
from sqlalchemy import select, delete
from app.main import app
from app.config import config
from app.db import Base, engine, SessionLocal, Space, AccessSession, Goal, Task, Record, Run, Job, Notification, now, uid
from app.services import default_settings, seed_demo, queue, new_run, add_record
from app.auth import create_session
from app.planning import next_contact, store_observations, evaluate_progress, undo_latest
from app.worker import claim_job, process_claim, heartbeat, send_one_notification
from app.adapters import Observation


async def run_test_job(job_id):
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=6)
        job.payload = {**job.payload, "lease_token": "test"}
    await process_claim(job_id, "test")
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "completed", job.error


@pytest.fixture
def product():
    Base.metadata.create_all(engine)
    ids = []
    def create(role="demo"):
        with SessionLocal.begin() as db:
            space = Space(id=uid(), role=role, settings=default_settings())
            db.add(space)
            db.flush()
            if role == "demo":
                seed_demo(db, space)
            response = Response()
            auth = create_session(db, space, response)
            cookie = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)
            ids.append(space.id)
        client = TestClient(app)
        client.cookies.set(*cookie)
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        if role == "demo":
            client.headers["X-Qianyan-Mode"] = "demo"
        return client, space.id
    yield create
    with SessionLocal.begin() as db:
        for table in (AccessSession, Notification, Job, Run, Record):
            db.execute(delete(table).where(table.space_id.in_(ids)))
        goal_ids = list(db.scalars(select(Goal.id).where(Goal.space_id.in_(ids))))
        db.execute(delete(Task).where(Task.goal_id.in_(goal_ids)))
        db.execute(delete(Goal).where(Goal.space_id.in_(ids)))
        db.execute(delete(Space).where(Space.id.in_(ids)))


def state(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    return response.json()


def test_space_isolation_and_csrf(product):
    a, _ = product()
    b, _ = product()
    goal = state(a)["goals"][0]
    assert b.get("/api/goals/" + goal["id"]).status_code == 404
    assert b.post("/api/goals/" + goal["id"] + "/pause", json={"version": goal["plan_version"]}).status_code == 404
    csrf = a.headers.pop("X-CSRF-Token")
    assert a.post("/api/memory", json={"content": "no csrf"}).status_code == 403
    a.headers["X-CSRF-Token"] = csrf
    a.headers["Origin"] = "https://untrusted.example"
    assert a.post("/api/memory", json={"content": "bad origin"}).status_code == 403


def test_demo_cookie_does_not_select_owner(product):
    owner, owner_id = product("owner")
    demo, demo_id = product()
    for cookie in owner.cookies:
        demo.cookies.set(cookie, owner.cookies.get(cookie))
    assert state(demo)["space"]["id"] == demo_id
    assert state(owner)["space"]["id"] == owner_id
    goal = state(demo)["goals"][0]
    assert demo.post("/api/goals/" + goal["id"] + "/sources", json={"version": goal["plan_version"], "github": {"owner": "example", "repo": "repo"}}).status_code == 403


def test_versions_pause_complete_and_notification_policy(product):
    client, space_id = product()
    goal = state(client)["goals"][0]
    path = "/api/goals/" + goal["id"]
    r = client.post(path + "/pause", json={"version": goal["plan_version"]})
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert client.post(path + "/resume", json={"version": goal["plan_version"]}).status_code == 409
    r = client.post(path + "/resume", json={"version": r.json()["plan_version"]})
    assert r.status_code == 200
    r = client.post(path + "/complete", json={"version": r.json()["plan_version"]})
    assert r.status_code == 200 and r.json()["status"] == "done"
    with SessionLocal() as db:
        assert not list(db.scalars(select(Job).where(Job.goal_id == goal["id"], Job.kind.in_(["observe", "followup"]), Job.status == "pending")))


@pytest.mark.asyncio
async def test_replay_closed_loop_and_dedup(product):
    client, space_id = product()
    async def event(name):
        result = client.post("/api/demo/events", json={"event": name})
        assert result.status_code == 200
        with SessionLocal.begin() as db:
            job = db.scalar(select(Job).where(Job.space_id == space_id, Job.payload["run_id"].as_string() == result.json()["run_id"]))
            job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=6)
            job.payload = {**job.payload, "lease_token": "test"}
            job_id = job.id
        await process_claim(job_id, "test")
        run = client.get("/api/runs/" + result.json()["run_id"]).json()
        assert run["status"] == "succeeded", run.get("error")
        return state(client)
    initial = await event("incomplete_readme")
    assert next(t for t in initial["goals"][0]["tasks"] if t["criteria"]["kind"] == "readme")["status"] == "needs_review"
    updated = await event("readme_complete")
    assert next(t for t in updated["goals"][0]["tasks"] if t["criteria"]["kind"] == "readme")["status"] == "done"
    failed = await event("deployment_failed")
    goal = failed["goals"][0]
    assert next(t for t in goal["tasks"] if t["criteria"]["kind"] == "deployment")["status"] == "blocked"
    assert len([t for t in goal["tasks"] if t["criteria"].get("blocker_for")]) == 1
    version, count = goal["plan_version"], len(failed["notifications"])
    repeated = await event("deployment_failed")
    assert repeated["goals"][0]["plan_version"] == version
    assert len(repeated["notifications"]) == count
    recovered = await event("deployment_recovered")
    assert next(t for t in recovered["goals"][0]["tasks"] if t["criteria"]["kind"] == "deployment")["status"] == "done"
    assert all(n["send_state"] == "in_app" for n in recovered["notifications"])
    # Replay source timestamps are historical and clearly separate from observation.
    evidence = recovered["goals"][0]["evidence"]
    assert all(e["body"]["data_mode"] == "replay" for e in evidence if e["body"].get("facts"))


@pytest.mark.asyncio
async def test_remember_edit_forget_removes_origin_and_model_context(product):
    from app.agent import build_context
    client, space_id = product()
    goal = state(client)["goals"][0]
    response = client.post("/api/messages", json={"text": "记住：private preference sentinel", "goal_id": goal["id"]})
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.space_id == space_id, Job.kind == "message"))
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=6)
        job.payload = {**job.payload, "lease_token": "test"}
        job_id = job.id
    await process_claim(job_id, "test")
    memory = next(m for m in state(client)["memories"] if "sentinel" in m["body"].get("content", ""))
    response = client.patch("/api/memory/" + memory["id"], json={"version": memory["version"], "content": "replacement preference"})
    assert response.status_code == 200
    replacement = response.json()
    with SessionLocal() as db:
        ctx = build_context(db, db.get(Space, space_id), db.get(Goal, goal["id"]))
        assert "sentinel" not in str(ctx)
        assert "replacement preference" in str(ctx)
    assert client.delete("/api/memory/" + replacement["id"], params={"version": replacement["version"]}).status_code == 200
    with SessionLocal() as db:
        ctx = build_context(db, db.get(Space, space_id), db.get(Goal, goal["id"]))
        assert "replacement preference" not in str(ctx)


def test_export_no_credentials(product):
    client, _ = product()
    body = client.get("/api/export").json()
    assert "settings" in body and "goals" in body
    for key in ("owner_password_hash", "csrf_token", "session_secret", "github_token"):
        assert key not in str(body)


def test_quiet_window_and_overnight():
    settings = default_settings()
    moment = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)  # Shanghai23:00
    assert next_contact(settings, moment) == datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)
    settings.update(notification_start="22:00", notification_end="07:00")
    assert next_contact(settings, moment) == moment


def test_evidence_same_source_independent_goals_and_return_to_old_version(product):
    client, space_id = product("owner")
    with SessionLocal.begin() as db:
        goals = [Goal(id=uid(), space_id=space_id, title="Goal " + str(i), intent="test", status="active") for i in range(2)]
        db.add_all(goals)
        db.flush()
        a = Observation("github", "same/repo", "A", {"kind": "repo", "exists": True})
        b = Observation("github", "same/repo", "B", {"kind": "repo", "exists": False})
        for goal in goals:
            assert store_observations(db, goal, [a])
        assert store_observations(db, goals[0], [b])
        assert store_observations(db, goals[0], [a])
        active = list(db.scalars(select(Record).where(Record.goal_id == goals[0].id, Record.active.is_(True))))
        assert len(active) == 1 and active[0].version == "A"


@pytest.mark.asyncio
async def test_demo_outbox_cannot_contact_real_user(product, monkeypatch):
    client, space_id = product()
    goal = state(client)["goals"][0]
    with SessionLocal.begin() as db:
        note = Notification(id=uid(), space_id=space_id, goal_id=goal["id"], category="action", reason_key=uid(), body={}, due_at=now(), send_state="pending")
        db.add(note)
        note_id = note.id
    def forbidden():
        raise AssertionError("Demo must never instantiate a sending adapter")
    monkeypatch.setattr("app.worker.wecom_adapter", forbidden)
    assert await send_one_notification()
    with SessionLocal() as db:
        assert db.get(Notification, note_id).send_state == "cancelled"


@pytest.mark.asyncio
async def test_model_function_call_installs_draft_and_uses_budget(product, monkeypatch):
    import json
    import httpx
    from app.adapters import TokenFactoryAdapter
    client, space_id = product("owner")
    plan = {"tasks": [{"key": "research", "title": "确认需求", "priority": 1, "criteria": {"kind": "user"}},
                      {"key": "build", "title": "完成原型", "depends_on": ["research"], "criteria": {"kind": "user"}}],
            "next_action": "确认需求", "reason": "先明确验收条件再做原型"}
    def respond(request):
        payload = json.loads(request.content)
        assert payload["model"].startswith("nvidia/") and payload["tool_choice"]["function"]["name"] == "propose_plan"
        assert "Authorization" in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "call", "type": "function", "function": {"name": "propose_plan", "arguments": json.dumps(plan)}}]}}],
            "model": "nvidia/mock-nemotron", "usage": {"total_tokens": 123}})
    cfg = config()
    monkeypatch.setattr(cfg, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.agent.TokenFactoryAdapter", lambda key, model, base: TokenFactoryAdapter(key, model, base, transport=httpx.MockTransport(respond)))
    r = client.post("/api/goals", json={"title": "原型目标", "intent": "我希望完整完成一个原型", "deadline": (now() + timedelta(days=5)).isoformat()})
    assert r.status_code == 200
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.space_id == space_id, Job.kind == "initial_plan"))
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=6)
        job.payload = {**job.payload, "lease_token": "test"}
        job_id = job.id
    await process_claim(job_id, "test")
    goal = client.get("/api/goals/" + r.json()["goal_id"]).json()
    assert goal["status"] == "draft" and len(goal["tasks"]) == 2
    assert client.get("/api/runs/" + r.json()["run_id"]).json()["metrics"]["daily_calls"] == 1
    second = next(t for t in goal["tasks"] if t["title"] == "完成原型")
    first = next(t for t in goal["tasks"] if t["title"] == "确认需求")
    assert second["depends_on"] == [first["id"]]


def test_demo_reset_preserves_model_budget(product, monkeypatch):
    from app.agent import reserve_call
    from app.adapters import AdapterError
    client, space_id = product()
    monkeypatch.setattr(config(), "demo_daily_calls", 1)
    with SessionLocal.begin() as db:
        goal = db.scalar(select(Goal).where(Goal.space_id == space_id))
        run = new_run(db, space_id, "message", goal.id)
        run_id = run.id
    reserve_call(run_id, 100)
    assert client.post("/api/demo/reset", json={}).status_code == 200
    with SessionLocal.begin() as db:
        run = new_run(db, space_id, "message")
        next_id = run.id
    with pytest.raises(AdapterError, match="budget"):
        reserve_call(next_id, 100)


def test_source_change_invalidates_old_project_evidence(product):
    client, space_id = product("owner")
    with SessionLocal.begin() as db:
        goal = Goal(id=uid(), space_id=space_id, title="Source switch", intent="test", status="active", source_bindings={"github": {"owner": "old", "repo": "repo"}})
        db.add(goal)
        db.flush()
        task = Task(id=uid(), goal_id=goal.id, title="README", status="done", criteria={"kind": "readme"})
        db.add(task)
        store_observations(db, goal, [Observation("github", "old/readme", "A", {"kind": "readme", "exists": True, "content": "old readme"})])
        goal_id, version = goal.id, goal.plan_version
    response = client.post("/api/goals/" + goal_id + "/sources", json={"version": version, "vercel": {"project_id": "new_project"}})
    assert response.status_code == 200
    assert response.json()["evidence"] == []
    assert response.json()["tasks"][0]["status"] == "needs_review"


@pytest.mark.asyncio
async def test_forget_during_inference_discards_stale_response(product, monkeypatch):
    import asyncio
    from app.agent import ResponseProposal
    client, space_id = product()
    goal_id = state(client)["goals"][0]["id"]
    memory = client.post("/api/memory", json={"content": "private stale sentinel", "goal_id": goal_id}).json()
    result = client.post("/api/messages", json={"text": "What do you remember?", "goal_id": goal_id})
    started, release = asyncio.Event(), asyncio.Event()
    async def pending_response(run_id, context, schema, purpose, validator=None):
        assert "private stale sentinel" in str(context)
        started.set()
        await release.wait()
        return ResponseProposal(reply="private stale sentinel should never be written back")
    monkeypatch.setattr("app.worker.generate", pending_response)
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.space_id == space_id, Job.kind == "message"))
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=6)
        job.payload = {**job.payload, "lease_token": "test"}
        job_id = job.id
    running = asyncio.create_task(process_claim(job_id, "test"))
    await asyncio.wait_for(started.wait(), 2)
    assert client.delete("/api/memory/" + memory["id"], params={"version": memory["version"]}).status_code == 200
    release.set()
    await asyncio.wait_for(running, 2)
    messages = state(client)["messages"]
    assert not any("private stale sentinel" in m["body"].get("text", "") for m in messages)
    assert client.get("/api/runs/" + result.json()["run_id"]).json()["status"] == "failed"


def test_postgres_space_lock_serializes_forget(product):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    client, space_id = product()
    memory = client.post("/api/memory", json={"content": "protected context"}).json()
    started = Event()
    def forget():
        started.set()
        return client.delete("/api/memory/" + memory["id"], params={"version": memory["version"]})
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with SessionLocal.begin() as db:
            db.scalar(select(Space).where(Space.id == space_id).with_for_update())
            future = pool.submit(forget)
            assert started.wait(2)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
            assert db.get(Record, memory["id"]).active
        assert future.result(timeout=3).status_code == 200
    finally:
        pool.shutdown(wait=True)


@pytest.mark.asyncio
async def test_pause_after_claim_cancels_inflight_followup(product):
    client, space_id = product()
    goal = state(client)["goals"][0]
    with SessionLocal.begin() as db:
        job = queue(db, space_id, "followup", {}, goal["id"])
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=6)
        job.payload = {"lease_token": "test"}
        job_id = job.id
    assert client.post("/api/goals/" + goal["id"] + "/pause", json={"version": goal["plan_version"]}).status_code == 200
    await process_claim(job_id, "test")
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "cancelled"
        assert not list(db.scalars(select(Notification).where(Notification.goal_id == goal["id"])))


@pytest.mark.asyncio
async def test_chat_delegation_saves_unconfirmed_goal_from_actual_user_instruction(product, monkeypatch):
    import json
    import httpx
    from app.adapters import TokenFactoryAdapter
    client, space_id = product("owner")
    instruction = "请一直跟进我的读书计划，具体日期我稍后决定。"
    proposal = {"reply": "我已整理目标草稿，请补充截止日期。", "capture_goal": {
        "title": "读书计划", "intent": "model summarized intent", "deadline": None}}
    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "call", "type": "function", "function": {"name": "propose_plan_patch", "arguments": json.dumps(proposal)}}]}}],
            "model": "nvidia/mock-nemotron", "usage": {"total_tokens": 100}})
    monkeypatch.setattr(config(), "nebius_api_key", "test-key")
    monkeypatch.setattr("app.agent.TokenFactoryAdapter", lambda key, model, base: TokenFactoryAdapter(key, model, base, transport=httpx.MockTransport(respond)))
    response = client.post("/api/messages", json={"text": instruction})
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.space_id == space_id, Job.kind == "message"))
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=6)
        job.payload = {**job.payload, "lease_token": "test"}
        job_id = job.id
    await process_claim(job_id, "test")
    result = state(client)
    assert len(result["goals"]) == 1
    goal = result["goals"][0]
    assert goal["status"] == "draft" and goal["deadline"] is None and goal["intent"] == instruction
    assert client.get("/api/runs/" + response.json()["run_id"]).json()["status"] == "succeeded"
    with SessionLocal() as db:
        assert db.scalar(select(Job).where(Job.goal_id == goal["id"], Job.kind == "initial_plan", Job.status == "pending"))


def test_task_edit_is_atomic_versioned_and_preserves_manual_priority(product):
    client, space_id = product()
    goal = state(client)["goals"][0]
    first, second = goal["tasks"][:2]
    path = "/api/tasks/" + first["id"]
    # A self-cycle rejects the entire change, including its unrelated title.
    rejected = client.patch(path, json={"version": goal["plan_version"], "title": "must not persist",
                                      "depends_on": [first["id"]]})
    assert rejected.status_code == 422
    assert client.get("/api/goals/" + goal["id"]).json()["plan_version"] == goal["plan_version"]
    changed = client.patch(path, json={"version": goal["plan_version"], "title": "用户设置的任务",
        "criteria": {"kind": "user", "description": "由我验收"}, "estimate_hours": 2.5,
        "priority": 4, "priority_mode": "manual", "depends_on": []})
    assert changed.status_code == 200
    item = next(t for t in changed.json()["tasks"] if t["id"] == first["id"])
    assert (item["title"], item["estimate_hours"], item["priority"]) == ("用户设置的任务", 2.5, 4)
    assert item["criteria"]["priority_locked_by_user"] is True
    assert client.patch(path, json={"version": goal["plan_version"], "priority": 1}).status_code == 409
    from app.agent import apply_patch, PlanPatch
    with SessionLocal.begin() as db:
        current = db.get(Goal, goal["id"])
        candidate = PlanPatch(goal_id=current.id, base_plan_version=current.plan_version,
                              changes=[{"task_id": first["id"], "priority": 1}], reason="Model priority proposal")
        with pytest.raises(ValueError, match="user-selected priority"):
            apply_patch(db, current, candidate)
        assert db.get(Task, first["id"]).priority == 4


def test_editing_completion_rule_revokes_old_confirmation(product):
    client, _ = product()
    goal = state(client)["goals"][0]
    task = goal["tasks"][0]
    path = "/api/tasks/" + task["id"]
    completed = client.patch(path, json={"version": goal["plan_version"], "status": "done"}).json()
    # Resaving the same rule must not undo a deliberate user confirmation.
    unchanged = client.patch(path, json={"version": completed["plan_version"],
        "criteria": {"kind": task["criteria"]["kind"], "description": ""}}).json()
    assert next(t for t in unchanged["tasks"] if t["id"] == task["id"])["status"] == "done"
    changed = client.patch(path, json={"version": unchanged["plan_version"],
        "criteria": {"kind": "user", "description": "新的验收条件"}}).json()
    item = next(t for t in changed["tasks"] if t["id"] == task["id"])
    assert item["status"] == "needs_review" and not item["criteria"].get("confirmed_by_user")
    with SessionLocal() as db:
        assert not list(db.scalars(select(Record).where(Record.task_id == task["id"], Record.source == "user", Record.active.is_(True))))


def test_draft_deletion_keeps_history_and_respects_dependencies(product):
    client, space_id = product("owner")
    with SessionLocal.begin() as db:
        goal = Goal(id=uid(), space_id=space_id, title="Draft", intent="Review first", status="draft")
        db.add(goal)
        db.flush()
        a = Task(id=uid(), goal_id=goal.id, title="First", criteria={"kind": "user"}, depends_on=[])
        b = Task(id=uid(), goal_id=goal.id, title="Second", criteria={"kind": "user"}, depends_on=[a.id])
        db.add_all([a, b])
        db.flush()
        record = add_record(db, space_id, "decision", {"summary": "Historical task reference"}, goal.id, task_id=a.id)
        goal_id, task_id, dependent_id, record_id, version = goal.id, a.id, b.id, record.id, goal.plan_version
    assert client.delete("/api/tasks/" + task_id, params={"version": version}).status_code == 422
    updated = client.patch("/api/tasks/" + dependent_id, json={"version": version, "depends_on": []}).json()
    deleted = client.delete("/api/tasks/" + task_id, params={"version": updated["plan_version"]})
    assert deleted.status_code == 200 and len(deleted.json()["tasks"]) == 1
    with SessionLocal() as db:
        assert db.get(Record, record_id).task_id is None
    active, _ = product()
    active_goal = state(active)["goals"][0]
    assert active.delete("/api/tasks/" + active_goal["tasks"][0]["id"],
                         params={"version": active_goal["plan_version"]}).status_code == 422


def test_confirmation_conflict_preserves_done_and_requires_current_version(product):
    client, space_id = product()
    goal = state(client)["goals"][0]
    task = next(t for t in goal["tasks"] if t["criteria"]["kind"] == "readme")
    completed = client.patch("/api/tasks/" + task["id"], json={"version": goal["plan_version"], "status": "done"}).json()
    from app.worker import replay_observation
    with SessionLocal.begin() as db:
        current = db.get(Goal, goal["id"])
        store_observations(db, current, [replay_observation("incomplete_readme")], "replay")
        assert evaluate_progress(db, current) == []
        assert db.get(Task, task["id"]).status == "done"
        note = db.scalar(select(Notification).where(Notification.goal_id == current.id, Notification.category == "decision"))
        note_id = note.id
    assert client.post("/api/notifications/" + note_id + "/actions", json={"action": "keep_open"}).status_code == 409
    review = client.post("/api/notifications/" + note_id + "/actions",
                         json={"action": "keep_open", "version": completed["plan_version"]})
    assert review.status_code == 200
    refreshed = client.get("/api/goals/" + goal["id"]).json()
    item = next(t for t in refreshed["tasks"] if t["id"] == task["id"])
    assert item["status"] == "needs_review" and not item["criteria"].get("confirmed_by_user")


def test_keep_confirmation_acknowledges_observed_failure_without_repeat(product):
    client, space_id = product()
    goal = state(client)["goals"][0]
    task = next(t for t in goal["tasks"] if t["criteria"]["kind"] == "deployment")
    completed = client.patch("/api/tasks/" + task["id"], json={"version": goal["plan_version"], "status": "done"}).json()
    from app.worker import replay_observation
    with SessionLocal.begin() as db:
        current = db.get(Goal, goal["id"])
        store_observations(db, current, [replay_observation("deployment_failed")], "replay")
        evaluate_progress(db, current)
        note = db.scalar(select(Notification).where(Notification.goal_id == current.id, Notification.category == "decision"))
        note_id = note.id
    response = client.post("/api/notifications/" + note_id + "/actions",
                           json={"action": "mark_done", "version": completed["plan_version"]})
    assert response.status_code == 200
    with SessionLocal.begin() as db:
        current = db.get(Goal, goal["id"])
        evaluate_progress(db, current)
        assert len(list(db.scalars(select(Notification).where(Notification.goal_id == current.id)))) == 1
        assert db.get(Task, task["id"]).criteria["confirmed_against_evidence_ids"]


@pytest.mark.asyncio
async def test_undo_schedules_durable_recheck_without_repeating_notifications(product):
    client, space_id = product()
    goal = state(client)["goals"][0]
    readme = next(t for t in goal["tasks"] if t["criteria"]["kind"] == "readme")
    from app.worker import replay_observation
    with SessionLocal.begin() as db:
        current = db.get(Goal, goal["id"])
        run = new_run(db, space_id, "replay", current.id)
        store_observations(db, current, [replay_observation("readme_complete")], "replay")
        evaluate_progress(db, current, run)
        run.status = "succeeded"
        version = current.plan_version
        evidence_ids = list(db.scalars(select(Record.id).where(Record.goal_id == current.id, Record.kind == "evidence")))
        note_count = len(list(db.scalars(select(Notification).where(Notification.goal_id == current.id))))
    undone = client.post("/api/goals/" + goal["id"] + "/undo", json={"version": version})
    assert undone.status_code == 200
    assert next(t for t in undone.json()["tasks"] if t["id"] == readme["id"])["status"] == readme["status"]
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.goal_id == goal["id"], Job.kind == "recheck", Job.status == "pending"))
        assert job and job.due_at > now()
        job_id = job.id
    await run_test_job(job_id)
    with SessionLocal() as db:
        assert db.get(Task, readme["id"]).status == "done"
        assert list(db.scalars(select(Record.id).where(Record.goal_id == goal["id"], Record.kind == "evidence"))) == evidence_ids
        assert len(list(db.scalars(select(Notification).where(Notification.goal_id == goal["id"])))) == note_count


@pytest.mark.asyncio
async def test_plain_commit_observation_does_not_call_model_or_push(product, monkeypatch):
    client, space_id = product("owner")
    with SessionLocal.begin() as db:
        goal = Goal(id=uid(), space_id=space_id, title="Track project", intent="Observe changes", status="active",
                    source_bindings={"github": {"owner": "example", "repo": "repo"}})
        db.add(goal)
        db.flush()
        task = Task(id=uid(), goal_id=goal.id, title="Accept MVP", criteria={"kind": "user"}, depends_on=[])
        db.add(task)
        job = queue(db, space_id, "observe", {}, goal.id)
        job_id, goal_id = job.id, goal.id
    class GitHubMock:
        def __init__(self, credential): pass
        async def observe(self, **bindings):
            return [Observation("github", "example/repo:commit", "new-sha", {"kind": "commit", "sha": "new-sha", "exists": True})]
        async def aclose(self): pass
    async def forbidden(*args, **kwargs):
        raise AssertionError("An ordinary commit must not trigger inference")
    monkeypatch.setattr(config(), "nebius_api_key", "mock-key")
    monkeypatch.setattr("app.worker.GitHubAdapter", GitHubMock)
    monkeypatch.setattr("app.worker.model_followup", forbidden)
    await run_test_job(job_id)
    with SessionLocal() as db:
        assert db.scalar(select(Record).where(Record.goal_id == goal_id, Record.source == "github"))
        assert not list(db.scalars(select(Notification).where(Notification.goal_id == goal_id)))
        assert db.get(Task, task.id).status == "todo"


@pytest.mark.asyncio
async def test_forgetting_completed_model_memory_erases_derived_reply_and_run(product, monkeypatch):
    import json
    import httpx
    from app.adapters import TokenFactoryAdapter
    from app.agent import build_context
    client, space_id = product("owner")
    secret = "private-memory-erase-sentinel"
    memory = client.post("/api/memory", json={"content": secret}).json()
    def respond(request):
        assert secret in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"id": "call", "type": "function",
            "function": {"name": "propose_plan_patch", "arguments": json.dumps({"reply": "Your preference: " + secret})}}]}}],
            "model": "nvidia/mock-nemotron", "usage": {"total_tokens": 100}})
    monkeypatch.setattr(config(), "nebius_api_key", "mock-key")
    monkeypatch.setattr("app.agent.TokenFactoryAdapter", lambda key, model, base: TokenFactoryAdapter(key, model, base, transport=httpx.MockTransport(respond)))
    response = client.post("/api/messages", json={"text": "What are my preferences?"}).json()
    with SessionLocal() as db:
        job_id = db.scalar(select(Job.id).where(Job.space_id == space_id, Job.kind == "message"))
    await run_test_job(job_id)
    assert secret in str(state(client)["messages"])
    assert client.delete("/api/memory/" + memory["id"], params={"version": memory["version"]}).status_code == 200
    with SessionLocal() as db:
        assert secret not in str(build_context(db, db.get(Space, space_id)))
        assert secret not in str([r.body for r in db.scalars(select(Record).where(Record.space_id == space_id))])
        run = db.get(Run, response["run_id"])
        assert run.metrics["explanation_forgotten"] and run.metrics["daily_calls"] == 1
        assert not run.patch


@pytest.mark.asyncio
async def test_bounded_model_reads_then_proposes_followup_with_each_call_budgeted(product, monkeypatch):
    import json
    import httpx
    from app.adapters import TokenFactoryAdapter
    client, space_id = product("owner")
    client.post("/api/memory", json={"content": "Prefer concise replies"})
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        advertised = {tool["function"]["name"] for tool in payload["tools"]}
        assert {"get_goal", "list_tasks", "read_evidence", "read_memory", "propose_followup"} <= advertised
        index = len(requests)
        if index == 1:
            name, arguments = "read_memory", {}
        elif index == 2:
            result = payload["messages"][-1]
            assert result["role"] == "tool" and result["tool_call_id"] == "call-1"
            assert "Prefer concise replies" in result["content"]
            name, arguments = "get_goal", {}
        else:
            assert payload["messages"][-1]["tool_call_id"] == "call-2"
            name, arguments = "propose_followup", {"reply": "I will keep replies concise. Please choose a goal."}
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"id": "call-" + str(index), "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}]}}], "model": "nvidia/mock-nemotron", "usage": {"total_tokens": 100}})
    monkeypatch.setattr(config(), "nebius_api_key", "mock-key")
    monkeypatch.setattr("app.agent.TokenFactoryAdapter", lambda key, model, base: TokenFactoryAdapter(key, model, base, transport=httpx.MockTransport(respond)))
    response = client.post("/api/messages", json={"text": "Help me choose the next action."}).json()
    with SessionLocal() as db:
        job_id = db.scalar(select(Job.id).where(Job.space_id == space_id, Job.kind == "message"))
    await run_test_job(job_id)
    with SessionLocal() as db:
        assert db.get(Run, response["run_id"]).metrics["daily_calls"] == 3
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_model_cannot_read_forever_or_write_without_a_validated_proposal(product, monkeypatch):
    import json
    import httpx
    from app.adapters import TokenFactoryAdapter
    client, space_id = product("owner")
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"id": "call-" + str(len(requests)), "type": "function",
            "function": {"name": "read_memory", "arguments": "{}"}}]}}], "model": "nvidia/mock-nemotron"})
    monkeypatch.setattr(config(), "nebius_api_key", "mock-key")
    monkeypatch.setattr("app.agent.TokenFactoryAdapter", lambda key, model, base: TokenFactoryAdapter(key, model, base, transport=httpx.MockTransport(respond)))
    response = client.post("/api/messages", json={"text": "Plan my next action."}).json()
    with SessionLocal() as db:
        job_id = db.scalar(select(Job.id).where(Job.space_id == space_id, Job.kind == "message"))
    await run_test_job(job_id)
    with SessionLocal() as db:
        run = db.get(Run, response["run_id"])
        assert run.status == "failed" and "four-round" in run.error and run.metrics["daily_calls"] == 4
        assert not list(db.scalars(select(Goal).where(Goal.space_id == space_id)))
        assert not list(db.scalars(select(Record).where(Record.space_id == space_id, Record.source == "agent")))
    assert len(requests) == 4 and isinstance(requests[-1]["tool_choice"], dict)


@pytest.mark.asyncio
async def test_deployment_failure_can_notify_without_log_permission(product, monkeypatch):
    client, space_id = product("owner")
    with SessionLocal.begin() as db:
        space = db.get(Space, space_id)
        space.settings = {**space.settings, "notification_start": "00:00", "notification_end": "00:00"}
        goal = Goal(id=uid(), space_id=space_id, title="Production", intent="Deploy demo", status="active")
        db.add(goal)
        db.flush()
        task = Task(id=uid(), goal_id=goal.id, title="Deploy", criteria={"kind": "deployment"}, depends_on=[])
        db.add(task)
        db.flush()
        store_observations(db, goal, [Observation("vercel", "project:latest", "failed", {
            "kind": "deployment", "role": "latest", "state": "ERROR", "observed": True,
            "completion_evidence_complete": True, "build_log_status": "permission"}, partial=True),
            Observation("github", "repo:ci", "unknown", {"kind": "ci", "observed": False}, partial=True)])
        evaluate_progress(db, goal)
        note = db.scalar(select(Notification).where(Notification.goal_id == goal.id))
        assert task.status == "blocked" and note.category == "important_change"
        assert len(note.body["evidence_ids"]) == 1  # Unrelated partial CI is not a dependency.
        note_id = note.id
    sent = []
    class WeComMock:
        async def send_text(self, text):
            sent.append(text)
            return {"provider_id": "mock-accepted"}
        async def aclose(self): pass
    monkeypatch.setattr("app.worker.wecom_adapter", lambda: WeComMock())
    assert await send_one_notification()
    with SessionLocal() as db:
        assert db.get(Notification, note_id).send_state == "accepted"
    assert len(sent) == 1 and "blocked" in sent[0]
