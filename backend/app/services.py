from datetime import timedelta, datetime, timezone
from sqlalchemy import select, inspect
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .db import Space, Goal, Task, Record, Run, Job, Notification, now, uid
from .schemas import ButlerSettings, InitialPlan


def serialize(row):
    return {c.key: (getattr(row, c.key).isoformat() if isinstance(getattr(row, c.key), datetime) else getattr(row, c.key))
            for c in inspect(row).mapper.column_attrs}


def owned_goal(db, space_id, goal_id, lock=False):
    q = select(Goal).where(Goal.id == goal_id, Goal.space_id == space_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    goal = db.scalar(q)
    if not goal:
        raise HTTPException(404, "目标不存在 / Goal not found")
    return goal


def check_version(goal, version):
    if version is None or goal.plan_version != version:
        raise HTTPException(409, "计划已变化，请刷新 / Plan changed; reload")


def default_settings():
    return ButlerSettings().model_dump()


def add_record(db, space_id, kind, body, goal_id=None, source="user", source_id=None, version="1", task_id=None):
    row = Record(id=uid(), space_id=space_id, kind=kind, body=body, goal_id=goal_id,
                 source=source, source_id=source_id or uid(), version=str(version), task_id=task_id)
    db.add(row)
    db.flush()
    return row


def queue(db, space_id, kind, payload, goal_id=None, due_at=None, key=None):
    key = key or f"{kind}:{uid()}"
    exists = db.scalar(select(Job).where(Job.dedup_key == key))
    if exists:
        return exists
    if kind == "followup" and goal_id:
        # One next follow-up per goal: snoozing or replanning replaces its schedule.
        for pending in db.scalars(select(Job).where(Job.goal_id == goal_id, Job.kind == "followup", Job.status == "pending")):
            pending.status = "cancelled"
    row = Job(id=uid(), space_id=space_id, goal_id=goal_id, kind=kind, payload=payload,
              dedup_key=key, due_at=due_at or now())
    db.add(row)
    db.flush()
    return row


def new_run(db, space_id, trigger, goal_id=None):
    run = Run(id=uid(), space_id=space_id, goal_id=goal_id, trigger=trigger)
    db.add(run)
    db.flush()
    return run


def goal_detail(db, goal):
    value = serialize(goal)
    value["tasks"] = [serialize(t) for t in db.scalars(select(Task).where(Task.goal_id == goal.id).order_by(Task.priority, Task.id))]
    value["evidence"] = [serialize(r) for r in db.scalars(select(Record).where(
        Record.goal_id == goal.id, Record.kind == "evidence", Record.active.is_(True)).order_by(Record.observed_at.desc()).limit(100))]
    return value


def validate_dependencies(tasks):
    graph = {t.key: t.depends_on for t in tasks}
    if len(graph) != len(tasks):
        raise ValueError("Duplicate task keys")
    if any(key not in graph for values in graph.values() for key in values):
        raise ValueError("Unknown dependency")
    visiting, visited = set(), set()
    def walk(key):
        if key in visiting:
            raise ValueError("Circular task dependency")
        if key in visited:
            return
        visiting.add(key)
        for dep in graph[key]:
            walk(dep)
        visiting.remove(key)
        visited.add(key)
    for key in graph:
        walk(key)


def install_initial_plan(db, goal, plan: InitialPlan, base_version):
    check_version(goal, base_version)
    if db.scalar(select(Task.id).where(Task.goal_id == goal.id).limit(1)):
        raise ValueError("Goal already has a plan; no replacement allowed")
    validate_dependencies(plan.tasks)
    keys = {t.key: uid() for t in plan.tasks}
    for t in plan.tasks:
        if t.criteria.get("kind", "user") not in ("user", "readme", "ci", "deployment", "repo", "core_dir"):
            raise ValueError("Unsupported completion criteria")
        db.add(Task(id=keys[t.key], goal_id=goal.id, title=t.title, priority=t.priority,
                    depends_on=[keys[d] for d in t.depends_on], criteria=t.criteria, estimate_hours=t.estimate_hours))
    goal.next_action = plan.next_action
    goal.plan_version += 1
    update_risk(db, goal)


def update_risk(db, goal):
    db.flush()
    tasks = list(db.scalars(select(Task).where(Task.goal_id == goal.id)))
    completed = {t.id for t in tasks if t.status in ("done", "skipped")}
    ready = sorted((t for t in tasks if t.status not in ("done", "skipped", "blocked")
                    and set(t.depends_on).issubset(completed)), key=lambda x: (x.priority, x.id))
    goal.next_action = ready[0].title if ready else ("等待阻塞解决 / Resolve blockers" if len(completed) < len(tasks) else "确认目标整体完成 / Confirm overall completion")
    if goal.deadline and goal.deadline <= now():
        goal.risk = "已超过截止时间 / Deadline passed"
    elif any(t.status == "blocked" for t in tasks):
        goal.risk = "存在阻塞，需要行动 / Blocked; action required"
    elif not goal.deadline:
        goal.risk = "未设截止时间 / No deadline set"
    else:
        space = db.get(Space, goal.space_id)
        capacity = goal.capacity_hours_per_day or space.settings.get("capacity_hours_per_day")
        if not capacity:
            goal.risk = "缺少可投入时间，无法确认计划是否充足 / Capacity unknown"
        else:
            remaining = sum(t.estimate_hours for t in tasks if t.status not in ("done", "skipped"))
            days = max(0, (goal.deadline - now()).total_seconds() / 86400)
            goal.risk = ("预估可在期限内完成，仍需跟进 / Estimated feasible; keep monitoring"
                         if remaining <= days * capacity else "预估工作量超过剩余时间 / Estimated workload exceeds remaining capacity")


def seed_demo(db, space):
    goal = Goal(id=uid(), space_id=space.id, title="完成 Nebius 黑客松项目", intent="交代一次，持续跟进到提交。This is a labeled sample, not live progress.",
                deadline=now() + timedelta(days=8), status="active", source_bindings={"mode": "replay"}, source_status={"mode": "replay"})
    db.add(goal)
    db.flush()
    ids = {k: uid() for k in ("core", "readme", "deploy", "video", "submit")}
    rows = [
        ("core", "确认核心功能 / Confirm MVP", "done", 3, [], {"kind": "user", "confirmed_by_user": True}),
        ("readme", "完善 README / Complete README", "in_progress", 2, [], {"kind": "readme"}),
        ("deploy", "部署 Demo / Deploy demo", "todo", 2, ["core"], {"kind": "deployment"}),
        ("video", "录制演示视频 / Record demo video", "todo", 3, ["deploy"], {"kind": "user"}),
        ("submit", "提交作品 / Submit project", "todo", 3, ["readme", "video"], {"kind": "user"}),
    ]
    for key, title, status, priority, deps, criteria in rows:
        db.add(Task(id=ids[key], goal_id=goal.id, title=title, status=status, priority=priority,
                    depends_on=[ids[x] for x in deps], criteria=criteria, estimate_hours=2))
    add_record(db, space.id, "memory", {"content": "只在重要变化、需要决策或需要行动时联系我。"}, source="demo_sample")
    add_record(db, space.id, "evidence", {"kind": "user_confirmation", "task_id": ids["core"], "summary": "样例用户已确认MVP完成 / Sample user confirmation", "confidence": "high", "data_mode": "replay"}, goal.id, "demo_sample", task_id=ids["core"])
    add_record(db, space.id, "message", {"role": "assistant", "text": "我是 Qianyan。这是隔离样例空间；你可以试试证据不足与部署失败如何改变计划。This space uses labeled replay evidence."}, goal.id, "demo_sample")
    update_risk(db, goal)
    return goal
