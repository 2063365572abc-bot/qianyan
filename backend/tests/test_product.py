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
