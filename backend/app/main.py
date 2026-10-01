import json
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta, datetime
from time import monotonic
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, func, text
from sqlalchemy.orm import Session
from .config import config
from .db import session, Space, AccessSession, Goal, Task, Record, Run, Job, Notification, now, uid
from .auth import authenticate, valid_origin, create_session, verify_password, cookie_key
from .schemas import (SettingsChange, GoalInput, VersionInput, GoalChange, TaskChange,
                      MessageInput, MemoryInput, MemoryChange, NotificationAction, SourceChange)
from .services import (serialize, owned_goal, check_version, default_settings, add_record,
                       queue, new_run, goal_detail, seed_demo, update_risk)
from .services import validate_dependencies, invalidate_memory, confirm_task


@asynccontextmanager
async def lifespan(app):
    cfg = config()
    if cfg.production:
        if len(cfg.session_secret) < 32 or not cfg.owner_password_hash.startswith("pbkdf2_sha256$"):
            raise RuntimeError("Production requires configured session secret and owner password hash")
        if not cfg.app_public_url.startswith("https://"):
            raise RuntimeError("Production requires an HTTPS public URL")
    yield


app = FastAPI(title="Qianyan", version="0.1.0", lifespan=lifespan)
attempts = defaultdict(deque)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


def rate(request, name, limit, seconds):
    key = (name, request.client.host if request.client else "unknown")
    bucket = attempts[key]
    timestamp = monotonic()
    while bucket and bucket[0] < timestamp - seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(429, "请求过多，请稍后 / Too many requests")
    bucket.append(timestamp)


def context(request, db):
    space = authenticate(request, db)[0]
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        space = db.scalar(select(Space).where(Space.id == space.id).with_for_update().execution_options(populate_existing=True))
    return space


@app.get("/health/live")
@app.get("/api/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
@app.get("/api/health/ready")
def ready(db: Session = Depends(session)):
    db.execute(text("SELECT 1"))
    heartbeat = db.scalar(select(Job).where(Job.dedup_key == "worker:heartbeat"))
    online = bool(heartbeat and heartbeat.lease_until and heartbeat.lease_until > now() - timedelta(seconds=60))
    return JSONResponse({"database": True, "worker_online": online}, status_code=200 if online else 503)


class LoginInput(BaseModel):
    password: str = Field(min_length=1, max_length=1000)


@app.post("/api/auth/login")
def login(body: LoginInput, request: Request, response: Response, db: Session = Depends(session)):
    valid_origin(request)
    rate(request, "login", 8, 60)
    if not config().owner_password_hash:
        raise HTTPException(503, "本人账户尚未配置；可先试用样例 / Owner account is not configured")
    if not verify_password(body.password, config().owner_password_hash):
        raise HTTPException(401, "密码不正确 / Incorrect password")
    space = db.scalar(select(Space).where(Space.owner_key == "owner"))
    if not space:
        space = Space(id=uid(), owner_key="owner", role="owner", settings=default_settings())
        db.add(space)
        db.flush()
    result = create_session(db, space, response)
    db.commit()
    return result


@app.get("/api/auth/session")
def auth_session(request: Request, db: Session = Depends(session)):
    space, access = authenticate(request, db)
    return {"role": space.role, "space_id": space.id, "csrf_token": access.csrf_token}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(session)):
    space, access = authenticate(request, db)
    db.delete(access)
    db.commit()
    response.delete_cookie(cookie_key(space.role), secure=config().production, httponly=True, samesite="strict")
    return {"ok": True}


@app.post("/api/demo/session")
def demo_session(request: Request, response: Response, db: Session = Depends(session)):
    valid_origin(request)
    rate(request, "demo", 15, 3600)
    space = Space(id=uid(), role="demo", settings=default_settings(), expires_at=now() + timedelta(days=1))
    db.add(space)
    db.flush()
    seed_demo(db, space)
    result = create_session(db, space, response)
    db.commit()
    return result


@app.get("/api/state")
def state(request: Request, db: Session = Depends(session)):
    space = context(request, db)
    goals = list(db.scalars(select(Goal).where(Goal.space_id == space.id).order_by(Goal.created_at.desc())))
    def records(kind):
        rows = list(db.scalars(select(Record).where(Record.space_id == space.id, Record.kind == kind, Record.active.is_(True)).order_by(Record.created_at.desc()).limit(100)))
        return [serialize(r) for r in reversed(rows)]
    heartbeat = db.scalar(select(Job).where(Job.dedup_key == "worker:heartbeat"))
    cfg = config()
    return {
        "space": {"id": space.id, "role": space.role, "settings": space.settings, "settings_version": space.settings_version},
        "goals": [goal_detail(db, g) for g in goals], "memories": records("memory"), "messages": records("message"),
        "notifications": [serialize(n) for n in db.scalars(select(Notification).where(Notification.space_id == space.id).order_by(Notification.created_at.desc()).limit(100))],
        "runs": [serialize(r) for r in db.scalars(select(Run).where(Run.space_id == space.id).order_by(Run.created_at.desc()).limit(30))],
        "health": {"worker_last_seen": heartbeat.lease_until.isoformat() if heartbeat and heartbeat.lease_until else None,
                   "worker_online": bool(heartbeat and heartbeat.lease_until and heartbeat.lease_until > now() - timedelta(seconds=60))},
        "integrations": {"ai": bool(cfg.model_api_key), "provider": cfg.ai_provider, "nebius": bool(cfg.nebius_api_key), "github": bool(cfg.github_token), "vercel": bool(cfg.vercel_token),
                         "wecom": bool(cfg.wecom_corp_id and cfg.wecom_app_secret and cfg.wecom_user_id),
                         "model_id": cfg.model_id, "mode": "live" if cfg.model_api_key else "unconfigured",
                         "notification_channel": cfg.notification_channel},
    }


@app.get("/api/butler")
def settings_read(request: Request, db: Session = Depends(session)):
    space = context(request, db)
    return {"settings": space.settings, "version": space.settings_version}


@app.patch("/api/butler")
def settings_write(body: SettingsChange, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    space = db.scalar(select(Space).where(Space.id == space.id).with_for_update().execution_options(populate_existing=True))
    if body.version != space.settings_version:
        raise HTTPException(409, "设置已变化，请刷新 / Settings changed")
    space.settings = body.settings.model_dump()
    space.settings_version += 1
    for goal in db.scalars(select(Goal).where(Goal.space_id == space.id)):
        update_risk(db, goal)
    db.commit()
    return {"settings": space.settings, "version": space.settings_version}


@app.post("/api/goals")
def create_goal(body: GoalInput, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    count = db.scalar(select(func.count()).select_from(Goal).where(Goal.space_id == space.id, Goal.status != "done"))
    if count >= 3:
        raise HTTPException(422, "最多三个活动目标 / Up to three active goals")
    goal = Goal(id=uid(), space_id=space.id, title=body.title, intent=body.intent, deadline=body.deadline)
    db.add(goal)
    db.flush()
    run = new_run(db, space.id, "initial_plan", goal.id)
    queue(db, space.id, "initial_plan", {"run_id": run.id}, goal.id)
    add_record(db, space.id, "message", {"role": "user", "text": body.intent}, goal.id)
    db.commit()
    return {"goal_id": goal.id, "run_id": run.id}


@app.get("/api/goals")
def goals(request: Request, db: Session = Depends(session)):
    space = context(request, db)
    return [goal_detail(db, g) for g in db.scalars(select(Goal).where(Goal.space_id == space.id))]


@app.get("/api/goals/{goal_id}")
def read_goal(goal_id: str, request: Request, db: Session = Depends(session)):
    return goal_detail(db, owned_goal(db, context(request, db).id, goal_id))


@app.post("/api/goals/{goal_id}/confirm")
def confirm_goal(goal_id: str, body: VersionInput, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    goal = owned_goal(db, space.id, goal_id, lock=True)
    check_version(goal, body.plan_version or body.version)
    if goal.status != "draft":
        raise HTTPException(409, "计划已经确认 / Already confirmed")
    if not db.scalar(select(Task.id).where(Task.goal_id == goal.id).limit(1)):
        raise HTTPException(422, "先添加或生成计划任务 / Add or generate plan tasks first")
    goal.status = "active"
    goal.plan_version += 1
    queue(db, space.id, "followup", {}, goal.id, now() + timedelta(hours=4), f"followup:{goal.id}:{goal.plan_version}")
    if goal.source_bindings:
        queue(db, space.id, "observe", {}, goal.id)
    add_record(db, space.id, "decision", {"action": "confirm_plan", "plan_version": goal.plan_version}, goal.id)
    db.commit()
    return goal_detail(db, goal)


@app.post("/api/goals/{goal_id}/plan")
def retry_initial_plan(goal_id: str, body: VersionInput, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    goal = owned_goal(db, space.id, goal_id, lock=True)
    check_version(goal, body.plan_version or body.version)
    if goal.status != "draft" or db.scalar(select(Task.id).where(Task.goal_id == goal.id).limit(1)):
        raise HTTPException(409, "已有计划，请直接编辑任务 / Edit the existing plan")
    existing = db.scalar(select(Job).where(Job.goal_id == goal.id, Job.kind == "initial_plan", Job.status.in_(["pending", "running"])))
    if existing:
        return {"goal_id": goal.id, "run_id": existing.payload.get("run_id")}
    run = new_run(db, space.id, "initial_plan", goal.id)
    queue(db, space.id, "initial_plan", {"run_id": run.id}, goal.id)
    db.commit()
    return {"goal_id": goal.id, "run_id": run.id}


@app.patch("/api/goals/{goal_id}")
def edit_goal(goal_id: str, body: GoalChange, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    goal = owned_goal(db, space.id, goal_id, True)
    check_version(goal, body.version)
    if "deadline" in body.model_fields_set and body.deadline and not body.deadline.tzinfo:
        raise HTTPException(422, "截止时间需要时区 / Deadline needs timezone")
    for field in body.model_fields_set - {"version"}:
        setattr(goal, field, getattr(body, field))
    goal.plan_version += 1
    add_record(db, space.id, "decision", {"action": "edit_goal", "changes": body.model_dump(mode="json", exclude={"version"}, exclude_unset=True)}, goal.id)
    update_risk(db, goal)
    db.commit()
    return goal_detail(db, goal)


class TaskInput(BaseModel):
    version: int
    title: str = Field(min_length=1, max_length=300)
    priority: int = Field(default=3, ge=1, le=5)
    criteria: dict = Field(default_factory=lambda: {"kind": "user"})
    estimate_hours: float = Field(default=1, gt=0, le=100)
    depends_on: list[str] = Field(default_factory=list, max_length=10)


@app.post("/api/goals/{goal_id}/tasks")
def add_task(goal_id: str, body: TaskInput, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    goal = owned_goal(db, space.id, goal_id, True)
    check_version(goal, body.version)
    ids = set(db.scalars(select(Task.id).where(Task.goal_id == goal.id)))
    if len(ids) >= 10 or not set(body.depends_on).issubset(ids):
        raise HTTPException(422, "任务数量或依赖不合法 / Task limit or dependencies invalid")
    if body.criteria.get("kind", "user") not in ("user", "readme", "ci", "deployment", "repo", "core_dir"):
        raise HTTPException(422, "不支持的完成条件 / Unsupported criteria")
    task = Task(id=uid(), goal_id=goal.id, title=body.title, priority=body.priority, criteria=body.criteria,
                estimate_hours=body.estimate_hours, depends_on=body.depends_on)
    db.add(task)
    goal.plan_version += 1
    update_risk(db, goal)
    db.commit()
    return serialize(task)


@app.patch("/api/tasks/{task_id}")
def edit_task(task_id: str, body: TaskChange, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在 / Task not found")
    goal = owned_goal(db, space.id, task.goal_id, True)
    check_version(goal, body.version)
    if goal.status == "done":
        raise HTTPException(409, "目标已结束 / Goal is completed")
    if body.depends_on is not None:
        tasks = list(db.scalars(select(Task).where(Task.goal_id == goal.id)))
        class Node:
            def __init__(self, key, dependencies):
                self.key, self.depends_on = key, dependencies
        try:
            validate_dependencies([Node(t.id, body.depends_on if t.id == task.id else t.depends_on) for t in tasks])
        except ValueError:
            raise HTTPException(422, "依赖必须属于同一目标且不能成环 / Dependencies must be local and acyclic") from None
        task.depends_on = body.depends_on
    if body.title is not None:
        task.title = body.title
    if body.estimate_hours is not None:
        task.estimate_hours = body.estimate_hours
    current_rule = {k: v for k, v in task.criteria.items() if k in ("kind", "description") and v != ""}
    proposed_rule = {k: v for k, v in (body.criteria or {}).items() if v != ""}
    if body.criteria is not None and current_rule != proposed_rule:
        metadata = {k: v for k, v in task.criteria.items() if k in ("priority_locked_by_user", "blocker_for")}
        task.criteria = {**proposed_rule, **metadata}
        if task.status == "done" and body.status != "done":
            task.status = "needs_review"
    if body.status:
        task.status = body.status
        if body.status == "done":
            confirm_task(db, space.id, goal, task, "用户确认完成 / User confirmed completion")
        elif task.criteria.get("confirmed_by_user"):
            task.criteria = {k: v for k, v in task.criteria.items() if k not in ("confirmed_by_user", "confirmed_against_evidence_ids")}
    if body.priority is not None:
        task.priority = body.priority
        task.criteria = {**task.criteria, "priority_locked_by_user": body.priority_mode != "auto"}
    elif body.priority_mode is not None:
        task.criteria = {**task.criteria, "priority_locked_by_user": body.priority_mode == "manual"}
    if body.status != "done" and (body.status is not None or body.criteria is not None and current_rule != proposed_rule):
        for evidence in db.scalars(select(Record).where(Record.task_id == task.id, Record.kind == "evidence", Record.source == "user")):
            evidence.active = False
    goal.plan_version += 1
    add_record(db, space.id, "decision", {"action": "edit_task", "task_id": task.id, "changes": body.model_dump(exclude={"version"}, exclude_none=True)}, goal.id)
    update_risk(db, goal)
    db.commit()
    return goal_detail(db, goal)


@app.delete("/api/tasks/{task_id}")
def delete_draft_task(task_id: str, version: int, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在 / Task not found")
    goal = owned_goal(db, space.id, task.goal_id, True)
    check_version(goal, version)
    if goal.status != "draft":
        raise HTTPException(422, "仅可删除草稿任务；已确认计划请选择不再做 / Delete draft tasks only; use Skipped for confirmed plans")
    for dependent in db.scalars(select(Task).where(Task.goal_id == goal.id)):
        if task.id in dependent.depends_on:
            raise HTTPException(422, "先移除其他任务对它的依赖 / Remove dependencies on this task first")
    for record in db.scalars(select(Record).where(Record.task_id == task.id)):
        record.task_id = None
    db.flush()
    db.delete(task)
    goal.plan_version += 1
    add_record(db, space.id, "decision", {"action": "delete_draft_task", "task_id": task.id}, goal.id)
    update_risk(db, goal)
    db.commit()
    return goal_detail(db, goal)


@app.post("/api/goals/{goal_id}/pause")
@app.post("/api/goals/{goal_id}/complete")
@app.post("/api/goals/{goal_id}/resume")
@app.post("/api/goals/{goal_id}/sync")
@app.post("/api/goals/{goal_id}/undo")
def goal_action(goal_id: str, body: VersionInput, request: Request, db: Session = Depends(session)):
    action = request.url.path.rsplit("/", 1)[1]
    space = context(request, db)
    goal = owned_goal(db, space.id, goal_id, True)
    check_version(goal, body.version or body.plan_version)
    if action in ("pause", "resume", "complete"):
        if goal.status == "draft":
            raise HTTPException(422, "请先确认初始计划 / Confirm initial plan first")
        if goal.status == "done":
            raise HTTPException(409, "目标已经结束 / Goal already completed")
        goal.status = {"pause": "paused", "resume": "active", "complete": "done"}[action]
        goal.plan_version += 1
        add_record(db, space.id, "decision", {"action": action, "user_confirmed": True}, goal.id)
        if action == "resume":
            queue(db, space.id, "followup", {}, goal.id, now() + timedelta(hours=4))
            if goal.source_bindings:
                queue(db, space.id, "observe", {}, goal.id)
        else:
            for job in db.scalars(select(Job).where(Job.goal_id == goal.id, Job.status == "pending", Job.kind.in_(["followup", "observe"]))):
                job.status = "cancelled"
            for note in db.scalars(select(Notification).where(Notification.goal_id == goal.id, Notification.send_state == "pending")):
                note.send_state = "cancelled"
        db.commit()
        return goal_detail(db, goal)
    if action == "sync":
        if space.role == "demo":
            raise HTTPException(403, "样例只使用标记回放 / Demo uses labeled replay")
        if goal.status != "active" or not goal.source_bindings:
            raise HTTPException(422, "先启用目标并绑定数据源 / Activate goal and connect a source")
        run = new_run(db, space.id, "observe", goal.id)
        queue(db, space.id, "observe", {"run_id": run.id}, goal.id)
        db.commit()
        return {"run_id": run.id}
    if action == "undo":
        from .planning import undo_latest
        undo_latest(db, goal)
        db.commit()
        return goal_detail(db, goal)
    raise HTTPException(404, "未知操作 / Unknown action")


@app.post("/api/goals/{goal_id}/sources")
def bind_sources(goal_id: str, body: SourceChange, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    if space.role != "owner":
        raise HTTPException(403, "样例空间不能连接个人数据源 / Demo cannot connect private sources")
    goal = owned_goal(db, space.id, goal_id, True)
    check_version(goal, body.version)
    bindings = {k: getattr(body, k).model_dump(exclude_none=True) for k in ("github", "vercel") if getattr(body, k)}
    changed_sources = {k for k in ("github", "vercel") if goal.source_bindings.get(k) != bindings.get(k)}
    for evidence in db.scalars(select(Record).where(Record.goal_id == goal.id, Record.kind == "evidence", Record.source.in_(changed_sources))):
        evidence.active = False
    affected = set()
    if "github" in changed_sources:
        affected.update(("readme", "ci", "repo", "core_dir"))
    if "vercel" in changed_sources:
        affected.add("deployment")
    for task in db.scalars(select(Task).where(Task.goal_id == goal.id)):
        if task.criteria.get("kind") in affected and not task.criteria.get("confirmed_by_user") and task.status != "skipped":
            task.status = "needs_review"
    goal.source_bindings = bindings
    goal.source_status = {}
    goal.plan_version += 1
    add_record(db, space.id, "decision", {"action": "change_sources", "changed_sources": sorted(changed_sources), "old_evidence_invalidated": True}, goal.id)
    update_risk(db, goal)
    if goal.status == "active" and goal.source_bindings:
        queue(db, space.id, "observe", {}, goal.id)
    db.commit()
    return goal_detail(db, goal)


@app.post("/api/messages")
def message(body: MessageInput, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    if body.goal_id:
        owned_goal(db, space.id, body.goal_id)
    record = add_record(db, space.id, "message", {"role": "user", "text": body.text}, body.goal_id)
    run = new_run(db, space.id, "message", body.goal_id)
    queue(db, space.id, "message", {"record_id": record.id, "run_id": run.id}, body.goal_id)
    db.commit()
    return {"message_id": record.id, "run_id": run.id}


@app.get("/api/runs/{run_id}")
def read_run(run_id: str, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    run = db.scalar(select(Run).where(Run.id == run_id, Run.space_id == space.id))
    if not run:
        raise HTTPException(404, "运行不存在 / Run not found")
    return serialize(run)


@app.get("/api/memory")
def memory_list(request: Request, db: Session = Depends(session)):
    space = context(request, db)
    return [serialize(x) for x in db.scalars(select(Record).where(Record.space_id == space.id, Record.kind == "memory", Record.active.is_(True)))]


@app.post("/api/memory")
def memory_add(body: MemoryInput, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    if body.goal_id:
        owned_goal(db, space.id, body.goal_id)
    record = add_record(db, space.id, "memory", {"content": body.content}, body.goal_id)
    db.commit()
    return serialize(record)


def owned_memory(db, space_id, record_id):
    record = db.scalar(select(Record).where(Record.id == record_id, Record.space_id == space_id, Record.kind == "memory", Record.active.is_(True)).with_for_update())
    if not record:
        raise HTTPException(404, "记忆不存在 / Memory not found")
    return record


@app.patch("/api/memory/{record_id}")
def memory_edit(record_id: str, body: MemoryChange, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    row = owned_memory(db, space.id, record_id)
    if str(body.version) != row.version:
        raise HTTPException(409, "记忆已变化 / Memory changed")
    invalidate_memory(db, space.id, row)
    replacement = add_record(db, space.id, "memory", {"content": body.content}, row.goal_id,
                             source="user", source_id=row.source_id, version=str(int(row.version) + 1))
    db.commit()
    return serialize(replacement)


@app.delete("/api/memory/{record_id}")
def memory_forget(record_id: str, request: Request, version: str, db: Session = Depends(session)):
    space = context(request, db)
    row = owned_memory(db, space.id, record_id)
    if version != row.version:
        raise HTTPException(409, "记忆已变化 / Memory changed")
    invalidate_memory(db, space.id, row)
    db.commit()
    return {"forgotten": True}


@app.get("/api/export")
def export(request: Request, db: Session = Depends(session)):
    space = context(request, db)
    data = {"exported_at": now().isoformat(), "settings": space.settings,
            "goals": [goal_detail(db, g) for g in db.scalars(select(Goal).where(Goal.space_id == space.id))],
            "memories": [serialize(r) for r in db.scalars(select(Record).where(Record.space_id == space.id, Record.kind == "memory", Record.active.is_(True)))]}
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="qianyan-export.json"'})


@app.get("/api/notifications")
def notes(request: Request, db: Session = Depends(session)):
    space = context(request, db)
    return [serialize(n) for n in db.scalars(select(Notification).where(Notification.space_id == space.id).order_by(Notification.created_at.desc()))]


@app.post("/api/notifications/{note_id}/actions")
def notification_action(note_id: str, body: NotificationAction, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    note = db.scalar(select(Notification).where(Notification.id == note_id, Notification.space_id == space.id).with_for_update())
    if not note:
        raise HTTPException(404, "通知不存在 / Notification not found")
    if note.action_state != "open":
        raise HTTPException(409, "该跟进已处理 / Follow-up already handled")
    goal = owned_goal(db, space.id, note.goal_id, True) if note.goal_id else None
    if body.action == "snooze":
        due = body.followup_at or now() + timedelta(hours=2)
        if due.tzinfo is None or due <= now():
            raise HTTPException(422, "请选择带时区的未来时间 / Choose a future time with timezone")
        if goal:
            check_version(goal, body.version)
            if goal.status != "active":
                raise HTTPException(409, "目标未在跟进中 / Goal is not active")
            goal.plan_version += 1
        queue(db, space.id, "followup", {"notification_id": note.id, "user_snooze": True}, note.goal_id, due)
    elif body.action == "mark_done":
        task_id = note.body.get("task_id")
        task = db.get(Task, task_id) if task_id else None
        if not goal or not task or task.goal_id != goal.id:
            raise HTTPException(422, "通知没有明确任务，请在目标中确认 / No unambiguous task")
        check_version(goal, body.version)
        confirm_task(db, space.id, goal, task, "用户选择仍标记完成 / User override")
        goal.plan_version += 1
        update_risk(db, goal)
    elif body.action == "handled":
        add_record(db, space.id, "decision", {"action": "handled", "notification_id": note.id, "does_not_prove_external_success": True}, note.goal_id)
        if goal and space.role == "owner" and goal.source_bindings:
            queue(db, space.id, "observe", {}, goal.id)
    elif body.action == "keep_open" and note.body.get("user_confirmation_conflict"):
        task = db.get(Task, note.body.get("task_id"))
        if not goal or not task or task.goal_id != goal.id:
            raise HTTPException(422, "通知没有明确任务 / No unambiguous task")
        check_version(goal, body.version)
        task.status = "needs_review"
        task.criteria = {k: v for k, v in task.criteria.items() if k not in ("confirmed_by_user", "confirmed_against_evidence_ids")}
        for previous in db.scalars(select(Record).where(Record.task_id == task.id, Record.kind == "evidence",
                                                       Record.source == "user", Record.active.is_(True))):
            previous.active = False
        goal.plan_version += 1
        add_record(db, space.id, "decision", {"action": "review_user_confirmation", "task_id": task.id}, goal.id)
        update_risk(db, goal)
    note.action_state = body.action
    if note.send_state == "pending":
        note.send_state = "cancelled"
    db.commit()
    return serialize(note)


@app.post("/api/demo/events")
def demo_event(body: dict, request: Request, db: Session = Depends(session)):
    space = context(request, db)
    if space.role != "demo":
        raise HTTPException(403, "回放只用于隔离样例 / Replay is demo-only")
    event = body.get("event")
    if event not in ("incomplete_readme", "readme_complete", "deployment_failed", "deployment_recovered"):
        raise HTTPException(422, "未知回放事件 / Unknown replay event")
    goal = db.scalar(select(Goal).where(Goal.space_id == space.id).order_by(Goal.created_at))
    run = new_run(db, space.id, "replay", goal.id)
    queue(db, space.id, "replay", {"run_id": run.id, "event": event}, goal.id)
    db.commit()
    return {"run_id": run.id}


@app.post("/api/demo/reset")
def demo_reset(request: Request, db: Session = Depends(session)):
    space = context(request, db)
    if space.role != "demo":
        raise HTTPException(403, "仅能重置隔离样例 / Demo reset only")
    # Preserve same-day AI reservations so reset cannot bypass public Demo limits.
    for run in db.scalars(select(Run).where(Run.space_id == space.id)):
        run.goal_id, run.patch, run.error = None, {}, None
        run.status = "cancelled"
        run.metrics = {k: v for k, v in run.metrics.items() if k in (
            "model_calls", "reserved_tokens", "budget_day", "daily_calls", "daily_reserved_tokens", "model_id", "usage")}
    db.flush()
    for table in (Notification, Job, Record):
        db.execute(delete(table).where(table.space_id == space.id))
    goal_ids = list(db.scalars(select(Goal.id).where(Goal.space_id == space.id)))
    db.execute(delete(Task).where(Task.goal_id.in_(goal_ids)))
    db.execute(delete(Goal).where(Goal.space_id == space.id))
    seed_demo(db, space)
    db.commit()
    return {"ok": True}


@app.get("/hooks/wecom")
async def wecom_verify(request: Request):
    from .worker import wecom_adapter
    from .adapters import AdapterError
    adapter = None
    try:
        adapter = wecom_adapter()
        return Response(adapter.verify_url(dict(request.query_params)), media_type="text/plain")
    except AdapterError as exc:
        raise HTTPException(400 if exc.code != "configuration" else 503, exc.message) from None
    finally:
        if adapter:
            await adapter.aclose()


@app.post("/hooks/wecom")
async def wecom_callback(request: Request, db: Session = Depends(session)):
    from .worker import wecom_adapter
    from .adapters import AdapterError
    adapter = None
    try:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 180000:
                raise HTTPException(413, "Callback exceeds maximum size")
        adapter = wecom_adapter()
        event = adapter.parse_callback(dict(request.query_params), bytes(raw))
    except AdapterError as exc:
        raise HTTPException(400 if exc.code != "configuration" else 503, exc.message) from None
    finally:
        if adapter:
            await adapter.aclose()
    if event["message_type"] != "text" or not event["text"].strip():
        return Response("success", media_type="text/plain")
    db.execute(text("SELECT pg_advisory_xact_lock(74821933)"))
    space = db.scalar(select(Space).where(Space.owner_key == "owner", Space.role == "owner").with_for_update())
    if not space:
        raise HTTPException(503, "Log in to initialize your personal space first")
    existing = db.scalar(select(Record.id).where(Record.space_id == space.id, Record.source == "wecom",
        Record.source_id == event["message_id"], Record.kind == "message"))
    if existing:
        return Response("success", media_type="text/plain")
    goals = list(db.scalars(select(Goal).where(Goal.space_id == space.id, Goal.status == "active")))
    goal = goals[0] if len(goals) == 1 else None
    text_value = event["text"].strip()
    # Multiple goals require an explicit title prefix, never a guessed mapping.
    for candidate in goals:
        prefix = candidate.title + "："
        if text_value.startswith(prefix):
            goal, text_value = candidate, text_value[len(prefix):].strip()
            break
    record = add_record(db, space.id, "message", {"role": "user", "text": text_value,
        "provider_created_at": event["created_at"]}, goal.id if goal else None, "wecom", event["message_id"])
    run = new_run(db, space.id, "message", record.goal_id)
    queue(db, space.id, "message", {"record_id": record.id, "run_id": run.id}, record.goal_id, key="wecom:" + event["message_id"])
    db.commit()
    return Response("success", media_type="text/plain")
