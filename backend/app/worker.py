"""Persistent jobs, evidence observation and notification outbox.

Run independently: python -m app.worker. No browser session is required.
"""
import asyncio
import logging
import re
from datetime import timedelta
from sqlalchemy import select, or_, and_, delete, func
from .config import config
from .db import SessionLocal, Space, AccessSession, Goal, Task, Record, Run, Job, Notification, now, uid
from .services import (queue, new_run, add_record, owned_goal, install_initial_plan,
                       check_version, update_risk, default_settings)
from .planning import (store_observations, evaluate_progress, next_contact, notify,
                       fingerprint, snapshot)
from .agent import build_context, context_revision, generate, apply_patch, ResponseProposal
from .schemas import InitialPlan
from .adapters import GitHubAdapter, VercelAdapter, WeComAdapter, Observation, AdapterError

log = logging.getLogger("qianyan.worker")


def wecom_adapter():
    c = config()
    return WeComAdapter(c.wecom_corp_id, c.wecom_agent_id, c.wecom_app_secret, c.wecom_user_id,
        c.wecom_callback_token, c.wecom_encoding_aes_key)


def heartbeat():
    with SessionLocal.begin() as db:
        db.execute(__import__("sqlalchemy").text("SELECT pg_advisory_xact_lock(74821932)"))
        row = db.scalar(select(Job).where(Job.dedup_key == "worker:heartbeat").with_for_update())
        if not row:
            row = Job(id=uid(), kind="heartbeat", payload={}, dedup_key="worker:heartbeat", status="heartbeat")
            db.add(row)
        row.lease_until = now()


def claim_job():
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(or_(
            and_(Job.status == "pending", Job.due_at <= now()),
            and_(Job.status == "running", Job.lease_until < now())))
            .order_by(Job.due_at, Job.id).with_for_update(skip_locked=True).limit(1))
        if not job:
            return None
        if job.attempts >= 3:
            job.status, job.error = "failed", "Worker retries exhausted"
            run = db.get(Run, job.payload.get("run_id")) if job.payload.get("run_id") else None
            if run:
                run.status, run.error = "failed", job.error
            return None
        job.status, job.attempts = "running", job.attempts + 1
        job.lease_until = now() + timedelta(seconds=360)
        token = uid()
        job.payload = {**job.payload, "lease_token": token}
        return job.id, token


def load_claim(db, job_id, token):
    observed_job = db.get(Job, job_id)
    if not observed_job:
        raise AdapterError("cancelled", "Job no longer exists")
    # All product mutations take the same Space lock first, including forgetting.
    space = db.scalar(select(Space).where(Space.id == observed_job.space_id).with_for_update().execution_options(populate_existing=True))
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.status != "running" or job.payload.get("lease_token") != token or job.lease_until <= now():
        raise AdapterError("cancelled", "Job was cancelled or its lease changed")
    if not space or (space.expires_at and space.expires_at <= now()):
        raise AdapterError("cancelled", "Space expired")
    goal = owned_goal(db, space.id, job.goal_id, True) if job.goal_id else None
    run_id = job.payload.get("run_id")
    run = db.get(Run, run_id) if run_id else None
    if run and (run.space_id != space.id or run.goal_id != job.goal_id):
        raise AdapterError("cancelled", "Run context does not match")
    return job, space, goal, run


def live_goal(goal):
    if not goal or goal.status != "active":
        raise AdapterError("cancelled", "Goal is not active")


def start_run(db, job, space, goal, run):
    if not run:
        run = new_run(db, space.id, job.kind, goal.id if goal else None)
        job.payload = {**job.payload, "run_id": run.id}
    run.status = "running"
    run.base_version = goal.plan_version if goal else None
    return run


async def initial_plan(job_id, token):
    with SessionLocal.begin() as db:
        job, space, goal, run = load_claim(db, job_id, token)
        if not goal or goal.status != "draft":
            raise AdapterError("cancelled", "Initial plan is no longer a draft")
        run = start_run(db, job, space, goal, run)
        if db.scalar(select(Task.id).where(Task.goal_id == goal.id).limit(1)):
            raise AdapterError("cancelled", "Manual plan already exists")
        ctx, rev, base, run_id = build_context(db, space, goal), context_revision(db, space, goal), goal.plan_version, run.id
    plan = await generate(run_id, ctx, InitialPlan,
        "Decompose the delegated goal into 1–10 realistic tasks, explicit completion criteria and dependencies. "
        "Use kind=user for subjective/offline acceptance. Return a draft for user confirmation, not a confirmed plan.")
    with SessionLocal.begin() as db:
        job, space, goal, run = load_claim(db, job_id, token)
        if context_revision(db, space, goal) != rev:
            raise AdapterError("conflict", "Personal context or plan changed; regenerate using current state")
        install_initial_plan(db, goal, plan, base)
        run.patch = {"title": "初始计划待确认 / Review initial plan", "reason": plan.reason, "next_action": goal.next_action}
        run.status = "succeeded"
        add_record(db, space.id, "message", {"role": "assistant", "text": "已生成初始计划，请检查完成标准和截止时间后确认。 / Your draft plan is ready for review."}, goal.id, "agent")


def replay_observation(event):
    if event in ("incomplete_readme", "readme_complete"):
        content = "# Sample\n## Introduction\nA persistent personal butler sample.\n## Installation\n"
        content += "```sh\nnpm install\n```\n## Usage\n```sh\nnpm run dev\n```\nOpen http://localhost:5173 and delegate a goal." if event == "readme_complete" else "TODO\n## Usage\nOpen the demo and review a goal."
        facts = {"kind": "readme", "exists": True, "content": content, "head_sha": "replay-" + event, "observed": True}
        source, source_id = "github", "sample/readme"
    else:
        facts = {"kind": "deployment", "role": "latest", "state": "ERROR" if event == "deployment_failed" else "READY",
            "production_url": None if event == "deployment_failed" else "https://example.com/qianyan-sample",
            "observed": True, "health": "not_observed", "current_routing": "not_observed"}
        source, source_id = "vercel", "sample/production"
    return Observation(source, source_id, fingerprint(facts), facts, url="https://example.com", source_updated_at="2026-09-30T12:00:00+00:00")


async def observe(job_id, token, replay=False):
    with SessionLocal.begin() as db:
        job, space, goal, run = load_claim(db, job_id, token)
        live_goal(goal)
        if replay != (space.role == "demo"):
            raise AdapterError("cancelled", "Live sources and replay cannot cross spaces")
        run = start_run(db, job, space, goal, run)
        bindings, event, base = dict(goal.source_bindings), job.payload.get("event"), goal.plan_version
    observations, statuses = [], {}
    if replay:
        observations = [replay_observation(event)]
        statuses = {"mode": "replay"}
    else:
        c = config()
        for source, adapter_type, credential in (("github", GitHubAdapter, c.github_token), ("vercel", VercelAdapter, c.vercel_token)):
            if source not in bindings:
                continue
            adapter = None
            try:
                adapter = adapter_type(credential)
                items = await adapter.observe(**bindings[source])
                observations.extend(items)
                statuses[source] = {"status": "partial" if any(o.partial for o in items) else "ok", "last_observed_at": now().isoformat()}
            except AdapterError as exc:
                statuses[source] = {"status": "error", "error": exc.message, "code": exc.code, "attempted_at": now().isoformat()}
            finally:
                if adapter:
                    await adapter.aclose()
    with SessionLocal.begin() as db:
        job, space, goal, run = load_claim(db, job_id, token)
        live_goal(goal)
        if goal.source_bindings != bindings or goal.plan_version != base:
            raise AdapterError("conflict", "Goal or source binding changed during observation")
        # A failed poll retains history and prior plan; only freshly observed sources
        # are eligible for new task completion in this cycle.
        for source, status in statuses.items():
            if isinstance(status, dict) and status.get("status") == "error":
                for record in db.scalars(select(Record).where(Record.goal_id == goal.id, Record.kind == "evidence", Record.source == source, Record.active.is_(True))):
                    record.body = {**record.body, "partial": True, "stale": True}
        goal.source_status = {**goal.source_status, **statuses}
        changed = store_observations(db, goal, observations, "replay" if replay else "live")
        changes = evaluate_progress(db, goal, run) if changed else []
        run.patch = run.patch or {"title": "同步完成 / Observation complete", "changes": [], "next_action": goal.next_action, "risk": goal.risk}
        run.metrics = {**run.metrics, "observation_changed": changed, "data_mode": "replay" if replay else "live", "source_status": statuses}
        run.status = "succeeded" if observations else "failed"
        run.error = None if observations else "未取得新事实；保留原计划 / No fresh facts; previous plan retained"
        ctx, rev, run_id = build_context(db, space, goal), context_revision(db, space, goal), run.id
        if not replay and goal.source_bindings:
            due = now() + timedelta(seconds=config().observation_interval)
            queue(db, space.id, "observe", {}, goal.id, due, f"observe:{goal.id}:{job.id}")
    if changed and config().nebius_api_key:
        await model_followup(job_id, token, ctx, rev, run_id,
            "Review the newly observed facts and current checked plan. Propose justified priority/dependency/next-action/follow-up changes only if useful. Explain uncertainties.")


async def model_followup(job_id, token, ctx, rev, run_id, purpose):
    try:
        def validate(response):
            with SessionLocal.begin() as db:
                job, space, goal, run = load_claim(db, job_id, token)
                if context_revision(db, space, goal) != rev:
                    raise AdapterError("conflict", "Personal context changed; stale proposal discarded")
                if response.patch:
                    live_goal(goal)
                    apply_patch(db, goal, response.patch, dry_run=True)
                if response.capture_goal:
                    if goal or response.patch:
                        raise ValueError("New goal draft requires no selected goal or simultaneous patch")
                    if db.scalar(select(func.count()).select_from(Goal).where(Goal.space_id == space.id, Goal.status != "done")) >= 3:
                        raise ValueError("Up to three active goals")
                    if response.capture_goal.deadline:
                        record = db.get(Record, job.payload.get("record_id"))
                        user_text = record.body.get("text", "") if record else ""
                        local = response.capture_goal.deadline.astimezone(__import__("zoneinfo").ZoneInfo(space.settings["timezone"]))
                        month, day = local.month, local.day
                        date_pattern = rf"(?:{local.year}[-/]0?{month}[-/]0?{day}|(?:{local.year}年)?0?{month}月0?{day}日)"
                        if not re.search(date_pattern, user_text):
                            raise ValueError("No explicit matching calendar date; use null deadline and ask the user")
        response = await generate(run_id, ctx, ResponseProposal, purpose, validator=validate)
        with SessionLocal.begin() as db:
            job, space, goal, run = load_claim(db, job_id, token)
            if context_revision(db, space, goal) != rev:
                raise AdapterError("conflict", "Context changed while planning; stale proposal discarded")
            if response.patch:
                live_goal(goal)
                # Preserve the pre-observation snapshot so undo covers the whole update.
                original_before = run.metrics.get("before")
                apply_patch(db, goal, response.patch, run)
                if original_before:
                    run.metrics = {**run.metrics, "before": original_before}
            if response.capture_goal:
                delegated = response.capture_goal
                goal = Goal(id=uid(), space_id=space.id, title=delegated.title,
                    intent=delegated.intent, deadline=delegated.deadline, status="draft")
                db.add(goal)
                db.flush()
                run.goal_id, job.goal_id = goal.id, goal.id
                record = db.get(Record, job.payload.get("record_id"))
                if record:
                    # Preserve the actual human instruction rather than inferred intent.
                    goal.intent, record.goal_id = record.body["text"], goal.id
                initial = new_run(db, space.id, "initial_plan", goal.id)
                queue(db, space.id, "initial_plan", {"run_id": initial.id}, goal.id)
                run.patch = {"title": "目标草稿已保存 / Goal draft saved", "goal_id": goal.id,
                    "reason": "初始计划生成后仍需你确认 / Review and confirm the initial plan"}
            add_record(db, space.id, "message", {"role": "assistant", "text": response.reply,
                "type": "proposal_explanation", "run_id": run.id}, goal.id if goal else None, "agent")
            run.status = "succeeded"
    except (AdapterError, ValueError) as exc:
        with SessionLocal.begin() as db:
            job, space, goal, run = load_claim(db, job_id, token)
            run.metrics = {**run.metrics, "planning_error": getattr(exc, "message", "Proposal failed validation; previous plan preserved")}
            if run.trigger == "message":
                run.status, run.error = "failed", run.metrics["planning_error"]


def explicit_command(db, space, goal, record):
    text = record.body.get("text", "").strip()
    if text.startswith(("记住：", "记住:", "/remember ")):
        content = text.split(":", 1)[-1] if text.startswith("记住:") else text.removeprefix("记住：").removeprefix("/remember ")
        if not content.strip() or len(content) > 3000:
            raise AdapterError("input", "记忆需为1–3000字 / Memory must contain 1–3000 characters")
        memory = add_record(db, space.id, "memory", {"content": content.strip()}, goal.id if goal else None, "user")
        memory.origin_record_id = record.id
        return "已保存这条明确记忆，可在记忆页修改或遗忘。 / Saved; you can edit or forget it in Memory."
    if goal and text in ("暂停跟进", "/pause"):
        goal.status, goal.plan_version = "paused", goal.plan_version + 1
        for job in db.scalars(select(Job).where(Job.goal_id == goal.id, Job.status == "pending", Job.kind.in_(["followup", "observe"]))):
            job.status = "cancelled"
        for note in db.scalars(select(Notification).where(Notification.goal_id == goal.id, Notification.send_state == "pending")):
            note.send_state = "cancelled"
        return "已暂停该目标的自动观察和提醒。 / This goal's observation and follow-up are paused."
    if goal and text in ("稍后提醒", "/snooze"):
        live_goal(goal)
        queue(db, space.id, "followup", {}, goal.id, now() + timedelta(hours=2), "snooze:" + record.id)
        for note in db.scalars(select(Notification).where(Notification.goal_id == goal.id, Notification.action_state == "open")):
            note.action_state = "snooze"
            if note.send_state == "pending":
                note.send_state = "cancelled"
        return "已安排两小时后跟进，并遵守你的通知时间窗口。 / Follow-up scheduled in two hours, within your contact window."
    if goal and text.startswith(("确认完成：", "确认完成:", "/done ")):
        title = text.removeprefix("确认完成：").removeprefix("确认完成:").removeprefix("/done ").strip()
        tasks = list(db.scalars(select(Task).where(Task.goal_id == goal.id)))
        matches = [t for t in tasks if t.id == title or t.title == title]
        if len(matches) != 1:
            return "请使用任务的完整名称或在任务页确认，避免标错任务。 / Use the exact task title or confirm in Tasks."
        task = matches[0]
        task.status, task.criteria = "done", {**task.criteria, "confirmed_by_user": True}
        goal.plan_version += 1
        add_record(db, space.id, "evidence", {"kind": "user_confirmation", "summary": "用户明确确认完成 / Explicit user confirmation", "confidence": "high"}, goal.id, "user", task_id=task.id)
        update_risk(db, goal)
        return "已按你的明确确认更新任务完成状态。 / Task marked done from your explicit confirmation."
    return None


async def message(job_id, token):
    with SessionLocal.begin() as db:
        job, space, goal, run = load_claim(db, job_id, token)
        record = db.get(Record, job.payload.get("record_id"))
        if not record or record.space_id != space.id or not record.active:
            raise AdapterError("cancelled", "Message was removed or does not belong to this space")
        run = start_run(db, job, space, goal, run)
        reply = explicit_command(db, space, goal, record)
        if reply:
            add_record(db, space.id, "message", {"role": "assistant", "text": reply}, job.goal_id, "agent")
            run.status, run.patch = "succeeded", {"title": "已处理你的指令 / Command handled", "reason": reply}
            if record.source == "wecom" and goal:
                notify(db, space, goal, "decision", "reply:" + record.id, {"text": reply, "next_action": goal.next_action})
            return
        if record.source == "wecom" and not goal:
            goals = list(db.scalars(select(Goal).where(Goal.space_id == space.id, Goal.status == "active")))
            if goals:
                reply = "请先选择目标 / Please choose a goal: " + "; ".join(g.title for g in goals) + ". Open the goal detail link to reply."
                add_record(db, space.id, "message", {"role": "assistant", "text": reply}, source="agent")
                notify(db, space, goals[0], "decision", "choose-goal:" + record.id, {"text": reply})
                run.status = "succeeded"
                return
        ctx, rev, run_id = build_context(db, space, goal), context_revision(db, space, goal), run.id
    await model_followup(job_id, token, ctx, rev, run_id,
        "Respond to the latest user message using remembered preferences and current goal. Ask for subjective/offline progress if missing. "
        "If no selected goal, do not submit a patch. For an explicitly delegated new goal you can return capture_goal for an unconfirmed draft, "
        "or ask a focused question if ambiguous. Never invent a hard deadline or user confirmation. "
        "Explicit remember/done/pause/snooze commands and task controls are processed by the backend. For ambiguous completion, ask for explicit confirmation.")
    with SessionLocal.begin() as db:
        job, space, goal, run = load_claim(db, job_id, token)
        record = db.get(Record, job.payload["record_id"])
        if record.source == "wecom" and goal:
            text = "你的回复已处理，请查看当前计划。 / Your reply was processed; review the current plan." if run.status == "succeeded" else run.error
            notify(db, space, goal, "decision", "reply:" + record.id, {"text": text, "next_action": goal.next_action})


async def followup(job_id, token):
    with SessionLocal.begin() as db:
        job, space, goal, run = load_claim(db, job_id, token)
        live_goal(goal)
        run = start_run(db, job, space, goal, run)
        update_risk(db, goal)
        tasks = list(db.scalars(select(Task).where(Task.goal_id == goal.id)))
        active = [t for t in tasks if t.status not in ("done", "skipped")]
        if active:
            task = next((t for t in sorted(active, key=lambda t: (t.priority, t.id)) if t.title == goal.next_action), None)
            notify(db, space, goal, "action", "followup:" + job.id, {"title": "下一步需要你行动 / Your next action",
                "text": goal.next_action, "task_id": task.id if task else None, "risk": goal.risk,
                "next_action": goal.next_action, "deadline": goal.deadline.isoformat() if goal.deadline else None})
        run.status, run.patch = "succeeded", {"title": "跟进已检查 / Follow-up checked", "next_action": goal.next_action, "risk": goal.risk}
        due = now() + timedelta(hours=4)
        queue(db, space.id, "followup", {}, goal.id, due, f"followup:{goal.id}:{job.id}")


def notification_text(note, goal):
    body = note.body
    lines = ["Qianyan：" + goal.title, str(body.get("title") or "目标进度有变化 / Goal update")]
    if body.get("text"):
        lines.append(str(body["text"])[:450])
    for change in body.get("changes", [])[:3]:
        after = change.get("after", {})
        lines.append(str(change.get("title", ""))[:80] + " → " + str(after.get("status", "updated")))
    lines.extend(["下一步 / Next: " + str(body.get("next_action") or goal.next_action)[:180],
                  "状态 / Risk: " + str(body.get("risk") or goal.risk)[:160],
                  config().app_public_url.rstrip("/") + "/?goal=" + goal.id])
    return "\n".join(lines).encode("utf-8")[:2048].decode("utf-8", errors="ignore")


async def send_one_notification():
    with SessionLocal.begin() as db:
        note = db.scalar(select(Notification).where(Notification.send_state == "pending", Notification.due_at <= now())
            .order_by(Notification.due_at).with_for_update(skip_locked=True).limit(1))
        if not note:
            return False
        space, goal = db.get(Space, note.space_id), db.get(Goal, note.goal_id)
        permitted_state = goal and (goal.status == "active" or (goal.status == "draft" and note.category == "decision"))
        if not space or space.role != "owner" or not permitted_state or note.action_state != "open" or not space.settings.get("proactive", True):
            note.send_state = "cancelled"
            return True
        contact = next_contact(space.settings)
        if contact > now() + timedelta(seconds=1):
            note.due_at = contact
            return True
        if note.category == "decision" and note.body.get("task_id"):
            task = db.get(Task, note.body["task_id"])
            if not task or task.status == "done":
                note.send_state = "cancelled"
                return True
        if note.category == "important_change":
            for change in note.body.get("changes", []):
                task = db.get(Task, change.get("task_id"))
                if not task or task.status != change.get("after", {}).get("status"):
                    note.send_state = "cancelled"
                    return True
            for evidence_id in note.body.get("evidence_ids", []):
                record = db.get(Record, evidence_id)
                if not record or not record.active or record.body.get("partial") or record.body.get("stale"):
                    note.send_state = "cancelled"
                    return True
        if note.category == "action" and note.body.get("next_action") != goal.next_action:
            note.send_state = "cancelled"
            return True
        if note.category == "action" and note.body.get("task_id"):
            task = db.get(Task, note.body["task_id"])
            if not task or task.goal_id != goal.id or task.status in ("done", "skipped", "blocked"):
                note.send_state = "cancelled"
                return True
        note.send_state, note.attempts = "sending", note.attempts + 1
        note.body = {**note.body, "send_started_at": now().isoformat()}
        note_id, text = note.id, notification_text(note, goal)
    adapter = None
    try:
        adapter = wecom_adapter()
        result = await adapter.send_text(text)
        with SessionLocal.begin() as db:
            note = db.get(Notification, note_id)
            if note:
                note.send_state, note.provider_id = "accepted", result["provider_id"]
                note.error = None
    except AdapterError as exc:
        with SessionLocal.begin() as db:
            note = db.get(Notification, note_id)
            if note:
                note.error = exc.message
                note.send_state = "delivery_unknown" if exc.code == "delivery_unknown" else "failed"
                if exc.retryable and note.attempts < 3:
                    note.send_state, note.due_at = "pending", now() + timedelta(seconds=60 * note.attempts)
    finally:
        if adapter:
            await adapter.aclose()
    return True


def maintenance():
    with SessionLocal.begin() as db:
        # A crashed send may have reached the provider; never silently resend it.
        for note in db.scalars(select(Notification).where(Notification.send_state == "sending")):
            started = note.body.get("send_started_at")
            if started and __import__("datetime").datetime.fromisoformat(started) < now() - timedelta(minutes=3):
                note.send_state, note.error = "delivery_unknown", "Worker restarted during send; verify delivery before retry"
        expired = list(db.scalars(select(Space.id).where(Space.role == "demo", Space.expires_at <= now())))
        if expired:
            goal_ids = list(db.scalars(select(Goal.id).where(Goal.space_id.in_(expired))))
            for table in (AccessSession, Notification, Job, Run, Record):
                db.execute(delete(table).where(table.space_id.in_(expired)))
            db.execute(delete(Task).where(Task.goal_id.in_(goal_ids)))
            db.execute(delete(Goal).where(Goal.space_id.in_(expired)))
            db.execute(delete(Space).where(Space.id.in_(expired)))


async def process_claim(job_id, token):
    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            kind = job.kind
            previous_run = db.get(Run, job.payload.get("run_id")) if job.payload.get("run_id") else None
            already_committed = bool(previous_run and previous_run.status == "succeeded")
        if already_committed:
            with SessionLocal.begin() as db:
                job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if job and job.payload.get("lease_token") == token and job.status == "running":
                    job.status, job.lease_until = "completed", None
            return
        async with asyncio.timeout(300):
            if kind == "initial_plan":
                await initial_plan(job_id, token)
            elif kind == "message":
                await message(job_id, token)
            elif kind in ("observe", "replay"):
                await observe(job_id, token, replay=kind == "replay")
            elif kind == "followup":
                await followup(job_id, token)
            else:
                raise AdapterError("input", "Unknown job type")
        with SessionLocal.begin() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job and job.payload.get("lease_token") == token and job.status == "running":
                job.status, job.lease_until = "completed", None
    except Exception as exc:
        # Provider/validation errors are intentionally safe to display; raw exceptions
        # can include SQL parameters or credentials and never reach API/logs.
        code = exc.code if isinstance(exc, AdapterError) else "internal"
        error_message = exc.message if isinstance(exc, AdapterError) else "后台处理失败；已保留已提交事实 / Worker failed; committed facts retained"
        with SessionLocal.begin() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not job or job.payload.get("lease_token") != token:
                return
            retry = code == "conflict" and job.attempts < 3
            job.status, job.error, job.lease_until = ("pending" if retry else "cancelled" if code == "cancelled" else "failed"), error_message, None
            if retry:
                job.due_at = now() + timedelta(seconds=3)
            run = db.get(Run, job.payload.get("run_id")) if job.payload.get("run_id") else None
            if run and not retry:
                run.status, run.error = ("cancelled" if code == "cancelled" else "failed"), error_message
        log.warning("Job %s ended with %s", job_id, code)


async def heartbeat_loop():
    while True:
        try:
            heartbeat()
        except Exception:
            log.warning("Heartbeat unavailable")
        await asyncio.sleep(5)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    beat = asyncio.create_task(heartbeat_loop())
    try:
        while True:
            maintenance()
            claimed = claim_job()
            if claimed:
                await process_claim(*claimed)
            for _ in range(5):
                if not await send_one_notification():
                    break
            if not claimed:
                await asyncio.sleep(config().worker_interval)
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
