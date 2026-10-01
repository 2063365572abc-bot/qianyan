"""Runtime boundary tests; use transient ORM objects, never a database account."""

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import agent, main, planning, worker
from app.adapters import Observation, WeComAdapter
from app.db import Goal, Record, Task, Job, Notification
from app.schemas import SourceChange, VercelBinding


class MemorySession:
    def __init__(self, tasks=(), records=()):
        self.tasks = list(tasks)
        self.records = list(records)
        self.added = []

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is Task:
            return self.tasks
        if entity is Record:
            return self.records
        if entity is Job:
            return []
        raise AssertionError(f"Unexpected entity: {entity}")

    def scalar(self, statement):
        # This method is only used in the exact-record recovery test, which has
        # one record with the matching source/version. No SQL filters are faked.
        entity = statement.column_descriptions[0]["entity"]
        if entity is Record:
            return self.records[0] if self.records else None
        raise AssertionError(f"Unexpected scalar entity: {entity}")

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


def goal():
    return Goal(id="goal", space_id="owner-space", title="Finish project", intent="Finish project",
                status="active", plan_version=2, deadline=None, next_action="Task one", risk="unknown",
                source_bindings={}, source_status={}, capacity_hours_per_day=None)


def task(task_id="task-1", *, criteria=None, dependencies=()):
    return Task(id=task_id, goal_id="goal", title="Task " + task_id, status="todo", priority=3,
                criteria=criteria or {"kind": "user"}, depends_on=list(dependencies), estimate_hours=1,
                followup_at=None)


def evidence(facts, *, partial=False, version="v1", source="github"):
    return Record(id="evidence-1", space_id="owner-space", goal_id="goal", kind="evidence",
                  body={"facts": facts, "partial": partial}, source=source,
                  source_id="owner/repo:readme", version=version, active=True,
                  observed_at=datetime.now(timezone.utc), source_updated_at=None)


def patch(**kwargs):
    return agent.PlanPatch(goal_id="goal", base_plan_version=2, reason="Proposed internal plan change", **kwargs)


def test_model_cannot_mark_subjective_acceptance_done_without_user_confirmation():
    item = task(criteria={"kind": "user"})
    current_goal = goal()
    db = MemorySession([item])
    proposal = patch(changes=[{"task_id": item.id, "status": "done"}])
    with pytest.raises(ValueError, match="verified evidence"):
        agent.apply_patch(db, current_goal, proposal)
    assert item.status == "todo" and current_goal.plan_version == 2
    assert not db.added


def test_readme_prose_without_usage_example_does_not_complete():
    content = "## Introduction\nA personal butler.\n## Installation\n`npm install`\n## Usage\nUsage information will be added later."
    result = planning.readme_standard(content)
    assert not result["passed"] and "usage example" in result["missing"]
    assert planning.readme_standard(content.replace("Usage information will be added later.", "Run `npm run dev`."))["passed"]


def test_confirmed_deployment_failure_remains_known_when_build_logs_unavailable():
    item = task(criteria={"kind": "deployment"})
    record = evidence({"kind": "deployment", "role": "latest", "state": "ERROR",
                       "completion_evidence_complete": True, "build_log_status": "permission"}, partial=True, source="vercel")
    passed, reason, cited = planning.criterion_result(item, [record])
    assert passed is False and cited is record
    record.body = {**record.body, "stale": True}
    assert planning.criterion_result(item, [record])[0] is None


def test_snapshot_reads_cannot_select_another_space_goal_or_record():
    context = {"memories": [{"id": "current-memory", "content": "Owned fact"}],
               "goal": {"id": "current-goal", "tasks": [{"id": "owned-task"}], "evidence": [{"id": "owned-evidence"}]}}
    assert agent.read_snapshot(context, "list_tasks", {}) == [{"id": "owned-task"}]
    assert agent.read_snapshot(context, "read_evidence", {"ids": ["owned-evidence"]}) == [{"id": "owned-evidence"}]
    for name, arguments in (("read_memory", {"ids": ["foreign-memory"]}), ("get_goal", {"space_id": "other"}),
                            ("read_evidence", {"url": "https://attacker.example"}), ("read_memory", {"ids": "current-memory"}),
                            ("shell", {})):
        with pytest.raises(ValueError):
            agent.read_snapshot(context, name, arguments)


def test_notification_uses_personal_name_and_style_without_changing_facts():
    current = goal()
    current.deadline = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
    note = Notification(body={"title": "Production failed", "next_action": "Inspect build log", "risk": "Blocked"})
    texts = {style: worker.notification_text(note, current, {"name": "我的管家", "style": style, "timezone": "Asia/Shanghai"})
             for style in ("concise", "warm", "detailed")}
    for output in texts.values():
        assert output.startswith("我的管家：") and "Production failed" in output and "Inspect build log" in output and "Blocked" in output
        assert len(output.encode("utf-8")) <= 2048
    assert "I’m following up" in texts["warm"] and "2026-10-30T18:00:00+08:00" in texts["detailed"]
    assert len(set(texts.values())) == 3


def test_model_cannot_use_commit_success_as_subjective_mvp_acceptance():
    item = task(criteria={"kind": "user"})
    record = evidence({"kind": "commit", "sha": "abc123", "exists": True})
    proposal = patch(evidence_ids=[record.id], changes=[{"task_id": item.id, "status": "done"}])
    with pytest.raises(ValueError, match="verified evidence"):
        agent.apply_patch(MemorySession([item], [record]), goal(), proposal)
    assert item.status == "todo"


def test_done_requires_complete_current_evidence_and_citation():
    content = worker.replay_observation("readme_complete").facts["content"]
    for partial, cite in ((True, True), (False, False)):
        item = task(criteria={"kind": "readme"})
        record = evidence({"kind": "readme", "exists": True, "content": content}, partial=partial)
        proposal = patch(evidence_ids=[record.id] if cite else [], changes=[{"task_id": item.id, "status": "done"}])
        with pytest.raises(ValueError, match="verified evidence"):
            agent.apply_patch(MemorySession([item], [record]), goal(), proposal)
        assert item.status == "todo"


def test_valid_readme_done_uses_deterministic_standard():
    current_goal = goal()
    item = task(criteria={"kind": "readme"})
    record = evidence(worker.replay_observation("readme_complete").facts)
    proposal = patch(evidence_ids=[record.id], changes=[{"task_id": item.id, "status": "done"}])
    assert agent.apply_patch(MemorySession([item], [record]), current_goal, proposal)
    assert item.status == "done" and current_goal.plan_version == 3


def test_dependency_cycle_is_rejected_before_any_status_mutation():
    first = task("task-1")
    second = task("task-2", dependencies=["task-1"])
    proposal = patch(changes=[{"task_id": first.id, "status": "in_progress", "depends_on": [second.id]}])
    with pytest.raises(ValueError, match="Circular"):
        agent.apply_patch(MemorySession([first, second]), goal(), proposal)
    assert first.status == "todo" and first.depends_on == []


def test_cross_goal_evidence_and_stale_plan_are_rejected():
    item = task()
    with pytest.raises(ValueError, match="Evidence"):
        agent.apply_patch(MemorySession([item]), goal(), patch(evidence_ids=["foreign-evidence"]))
    current_goal = goal()
    current_goal.plan_version = 3
    with pytest.raises(HTTPException) as caught:
        agent.apply_patch(MemorySession([item]), current_goal, patch())
    assert caught.value.status_code == 409


@pytest.mark.parametrize("status", ["skipped", "blocked", "needs_review"])
def test_model_cannot_drop_scope_or_invent_blocker_without_evidence(status):
    item = task()
    with pytest.raises(ValueError):
        agent.apply_patch(MemorySession([item]), goal(), patch(changes=[{"task_id": item.id, "status": status}]))
    assert item.status == "todo"


def test_model_cannot_add_preconfirmed_tasks_or_external_operations():
    new = {"key": "new", "title": "Pretend already confirmed", "criteria": {"kind": "user", "confirmed_by_user": True}}
    with pytest.raises(ValueError, match="user judgment"):
        agent.apply_patch(MemorySession([task()]), goal(), patch(new_tasks=[new]))
    for external in ({"deadline": "2026-10-30T00:00:00Z"}, {"send_email": True}, {"deploy": "now"}):
        with pytest.raises(ValidationError):
            patch(**external)


@pytest.mark.parametrize("prior_status,criteria", [("done", {"kind": "user", "confirmed_by_user": True}), ("skipped", {"kind": "user"})])
def test_model_cannot_reopen_explicit_user_completion_or_skipped_scope(prior_status, criteria):
    item = task(criteria=criteria)
    item.status = prior_status
    proposal = patch(changes=[{"task_id": item.id, "status": "in_progress"}])
    with pytest.raises(ValueError):
        agent.apply_patch(MemorySession([item]), goal(), proposal)
    assert item.status == prior_status


@pytest.mark.parametrize("event", ["readme_complete", "incomplete_readme"])
def test_automatic_observation_respects_user_skipped_scope(event, monkeypatch):
    item = task(criteria={"kind": "readme"})
    item.status = "skipped"
    record = evidence(worker.replay_observation(event).facts)
    db = MemorySession([item], [record])
    db.get = lambda entity, identity: SimpleNamespace(role="owner")
    monkeypatch.setattr(planning, "notify", lambda *args, **kwargs: None)
    planning.evaluate_progress(db, goal())
    assert item.status == "skipped", "Source synchronization must not restore user-excluded scope"


def test_changing_source_binding_invalidates_old_project_evidence_and_keeps_user_decisions(monkeypatch):
    current_goal = goal()
    current_goal.source_bindings = {"github": {"owner": "old", "repo": "project"}, "vercel": {"project_id": "prod"}}
    github_record = evidence({"kind": "readme", "exists": True})
    vercel_record = evidence({"kind": "deployment", "state": "READY"}, source="vercel")
    completed = task("automatically-done", criteria={"kind": "readme"})
    completed.status = "done"
    user_confirmed = task("user-done", criteria={"kind": "readme", "confirmed_by_user": True})
    user_confirmed.status = "done"
    skipped = task("skipped", criteria={"kind": "ci"})
    skipped.status = "skipped"

    class BindingSession(MemorySession):
        def scalars(self, statement):
            if statement.column_descriptions[0]["entity"] is Record:
                sources = statement.compile().params.get("source_1")
                return [row for row in self.records if row.source in set(sources)] if sources is not None else [row for row in self.records if row.active]
            return super().scalars(statement)

        def commit(self):
            pass

    db = BindingSession([completed, user_confirmed, skipped], [github_record, vercel_record])
    monkeypatch.setattr(main, "context", lambda request, db: SimpleNamespace(id="owner-space", role="owner"))
    monkeypatch.setattr(main, "owned_goal", lambda *args: current_goal)
    monkeypatch.setattr(main, "add_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "queue", lambda *args, **kwargs: None)
    result = main.bind_sources(current_goal.id, SourceChange(version=2, vercel=VercelBinding(project_id="prod")), None, db)
    assert github_record.active is False and vercel_record.active is True
    assert completed.status == "needs_review"
    assert user_confirmed.status == "done" and skipped.status == "skipped"
    assert result["plan_version"] == 3


def test_followup_requires_bounded_future_timezone_aware_time():
    for instant in (datetime.now(), datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc) + timedelta(days=31)):
        with pytest.raises(ValueError, match="Follow-up"):
            agent.apply_patch(MemorySession([task()]), goal(), patch(followup_at=instant))


def test_replay_readme_matches_live_completion_standard():
    complete = worker.replay_observation("readme_complete")
    incomplete = worker.replay_observation("incomplete_readme")
    assert planning.readme_standard(complete.facts["content"])["passed"]
    failed = planning.readme_standard(incomplete.facts["content"])
    assert not failed["passed"] and "installation" in failed["missing"]
    assert complete.source_updated_at == "2026-09-30T12:00:00+00:00"
    assert complete.version != incomplete.version


def test_replay_deployment_never_claims_live_health():
    for event in ("deployment_failed", "deployment_ready"):
        record = worker.replay_observation(event)
        assert record.facts["health"] == "not_observed"
        assert record.facts["current_routing"] == "not_observed"
        assert record.source_updated_at == "2026-09-30T12:00:00+00:00"


@pytest.mark.asyncio
async def test_worker_wecom_factory_passes_correct_configuration_and_bound_user(monkeypatch):
    settings = SimpleNamespace(wecom_corp_id="ww-corp", wecom_agent_id="1000002", wecom_app_secret="secret",
                               wecom_user_id="owner", wecom_callback_token="callback-token",
                               wecom_encoding_aes_key="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8")
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={"access_token": "access", "expires_in": 7200})
        import json
        assert json.loads(request.content)["touser"] == "owner"
        return httpx.Response(200, json={"errcode": 0, "msgid": "message-id"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(worker, "config", lambda: settings)
    monkeypatch.setattr(worker, "WeComAdapter", lambda *args: WeComAdapter(*args, client=client))
    adapter = worker.wecom_adapter()
    assert adapter.agent_id == 1000002 and adapter.corp_id == "ww-corp"
    assert await adapter.send_text("An actionable change") == {"accepted": True, "provider_id": "message-id"}
    assert len(calls) == 2
    await client.aclose()


def test_successful_observation_clears_prior_stale_partial_marker():
    facts = {"kind": "readme", "exists": True, "content": worker.replay_observation("readme_complete").facts["content"]}
    previous = evidence(facts, partial=True)
    previous.body["stale"] = True
    db = MemorySession(records=[previous])
    observation = Observation("github", previous.source_id, previous.version, facts, partial=False)
    changed = planning.store_observations(db, goal(), [observation])
    assert changed, "Restored source freshness must trigger evidence evaluation"
    assert previous.body["partial"] is False
    assert previous.body.get("stale") is not True


@pytest.mark.asyncio
async def test_action_notification_for_completed_task_is_cancelled_before_wecom(monkeypatch):
    current_goal = goal()
    current_task = task()
    current_task.status = "done"
    current_goal.next_action = "Record demo video"
    note = SimpleNamespace(id="note", space_id="owner-space", goal_id="goal", category="action",
                           action_state="open", send_state="pending", attempts=0, error=None,
                           body={"task_id": current_task.id, "text": "Finish Task one", "next_action": "Task one"},
                           due_at=datetime.now(timezone.utc), provider_id=None)
    space = SimpleNamespace(id="owner-space", role="owner", settings={"proactive": True, "timezone": "UTC", "notification_start": "00:00", "notification_end": "00:00"})

    class NotificationSession(MemorySession):
        def scalar(self, statement):
            return note

        def get(self, entity, identity):
            return {worker.Notification: note, worker.Goal: current_goal,
                    worker.Space: space, worker.Task: current_task}.get(entity)

    db = NotificationSession([current_task])

    @contextmanager
    def transaction():
        yield db

    sent = []

    class Sender:
        async def send_text(self, text):
            sent.append(text)
            return {"accepted": True, "provider_id": "provider-id"}

        async def aclose(self):
            pass

    monkeypatch.setattr(worker, "SessionLocal", SimpleNamespace(begin=transaction))
    monkeypatch.setattr(worker, "wecom_adapter", Sender)
    monkeypatch.setattr(worker, "config", lambda: SimpleNamespace(app_public_url="https://qianyan.example"))
    assert await worker.send_one_notification()
    assert not sent, "Completed tasks must not receive old action reminders"
    assert note.send_state == "cancelled"
