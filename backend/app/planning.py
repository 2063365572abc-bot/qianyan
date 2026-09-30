import hashlib
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from markdown_it import MarkdownIt
from sqlalchemy import select
from fastapi import HTTPException
from .db import Space, Task, Record, Notification, Run, now, uid
from .services import serialize, update_risk


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def readme_standard(content):
    sections, current = {}, None
    tokens = MarkdownIt().parse(content)
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            title = tokens[i + 1].content.strip().lower()
            current = next((kind for kind, names in {
                "introduction": ("introduction", "overview", "简介", "概述", "项目介绍"),
                "installation": ("installation", "install", "setup", "安装", "安装指南"),
                "usage": ("usage", "quickstart", "quick start", "使用", "使用方法"),
            }.items() if any(name == title or title.startswith(name + " ") for name in names)), None)
            if current:
                sections.setdefault(current, [])
        elif current and token.type in ("inline", "fence", "code_block"):
            if i and tokens[i - 1].type == "heading_open":
                continue
            sections[current].append(token.content.strip())
    missing = []
    for name in ("introduction", "installation", "usage"):
        text = "\n".join(sections.get(name, []))
        substantive = re.sub(r"(?im)^(?:todo|tbd|coming soon|待补充|待完善|placeholder)[.!。\s]*$", "", text).strip()
        if not substantive:
            missing.append(name)
        elif name == "installation" and not re.search(r"(?im)\b(?:npm|pnpm|yarn|pip|uv|docker|git|python|make|cargo|go|bun)\s+\S+", substantive):
            missing.append("installation commands")
        elif name == "usage" and not (len(substantive) > 15 and re.search(r"[\w/][\w/-]*\s+\S+|https?://", substantive)):
            missing.append("usage example")
    return {"passed": not missing, "missing": missing, "standard": "README structure; commands not execution-verified"}


def criterion_result(task, records):
    kind = task.criteria.get("kind", "user")
    if kind == "user" or task.criteria.get("confirmed_by_user"):
        return None, "需要用户判断 / User judgment", None
    candidates = [r for r in records if r.body.get("facts", {}).get("kind") == kind and r.active]
    if kind == "deployment":
        candidates = [r for r in candidates if r.body["facts"].get("role") == "latest"]
    if not candidates:
        return None, "暂无可靠证据 / No current evidence", None
    record = max(candidates, key=lambda r: r.observed_at)
    facts = record.body["facts"]
    if record.body.get("partial") or facts.get("observed") is False:
        return None, "证据不完整 / Incomplete observation", record
    if kind == "readme":
        if facts.get("exists") is not True:
            return None, "README无法观测 / README unavailable", record
        result = readme_standard(facts.get("content", ""))
        return result["passed"], "README结构完整 / README structure complete" if result["passed"] else "README缺少 / Missing: " + ", ".join(result["missing"]), record
    if kind == "ci":
        heads = [r.body.get("facts", {}).get("sha") for r in records
                 if r.active and r.body.get("facts", {}).get("kind") == "commit"]
        if not heads or facts.get("head_sha") != heads[-1]:
            return None, "CI与当前提交未对齐 / CI does not match current commit", record
        passed = facts.get("passed") is True and facts.get("status") == "completed" and facts.get("conclusion") == "success"
        return passed, "指定CI结果 / Selected CI result: " + str(facts.get("conclusion") or facts.get("status") or "unknown"), record
    if kind == "deployment":
        state = facts.get("state", "unknown").upper()
        if state in ("ERROR", "FAILED", "CANCELED", "CANCELLED"):
            return False, "最新生产部署失败；旧版本状态需分别查看 / Latest production deployment failed", record
        if state != "READY":
            return None, "生产部署尚未就绪 / Production not ready", record
        if not facts.get("production_url"):
            return None, "构建成功但Production URL未确认 / Production URL unconfirmed", record
        return True, "生产构建成功且URL已确认；不等于功能验收 / Production build and URL confirmed", record
    return facts.get("exists"), "系统事实 / System fact", record


def snapshot(db, goal):
    return {"tasks": [serialize(t) for t in db.scalars(select(Task).where(Task.goal_id == goal.id))],
            "next_action": goal.next_action, "risk": goal.risk}


def next_contact(settings, moment=None):
    moment = moment or now()
    zone = ZoneInfo(settings.get("timezone", "Asia/Shanghai"))
    local = moment.astimezone(zone)
    start_h, start_m = map(int, settings.get("notification_start", "08:00").split(":"))
    end_h, end_m = map(int, settings.get("notification_end", "22:00").split(":"))
    minute = local.hour * 60 + local.minute
    start, end = start_h * 60 + start_m, end_h * 60 + end_m
    # Equal boundaries mean a whole-day contact window.
    allowed = start == end or (start <= minute < end if start < end else minute >= start or minute < end)
    if allowed:
        return moment
    contact = local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    if contact <= local:
        contact += timedelta(days=1)
    return contact.astimezone(moment.tzinfo)


def notify(db, space, goal, category, reason_key, body, due_at=None):
    exists = db.scalar(select(Notification).where(Notification.space_id == space.id, Notification.reason_key == reason_key))
    if exists:
        return exists
    note = Notification(id=uid(), space_id=space.id, goal_id=goal.id, category=category, reason_key=reason_key,
                        body={"goal_title": goal.title, **body}, due_at=next_contact(space.settings, due_at),
                        send_state="in_app" if space.role == "demo" or not space.settings.get("proactive", True) else "pending")
    db.add(note)
    return note


def store_observations(db, goal, observations, data_mode="live"):
    changed = False
    for obs in observations:
        kind = obs.facts.get("kind", "unknown")
        existing = db.scalar(select(Record).where(Record.space_id == goal.space_id, Record.goal_id == goal.id, Record.kind == "evidence",
                                                  Record.source == obs.source, Record.source_id == obs.source_id, Record.version == obs.version))
        if existing and existing.active and existing.body.get("facts") == obs.facts and existing.body.get("partial") == obs.partial and not existing.body.get("stale"):
            existing.observed_at = now()
            continue
        previous = list(db.scalars(select(Record).where(Record.goal_id == goal.id, Record.kind == "evidence", Record.source == obs.source, Record.active.is_(True))))
        for r in previous:
            facts = r.body.get("facts", {})
            if facts.get("kind") == kind and facts.get("role") == obs.facts.get("role"):
                r.active = False
        if existing:
            existing.active, existing.observed_at = True, now()
            existing.body = {**existing.body, "facts": obs.facts, "partial": obs.partial, "stale": False,
                             "confidence": "high" if not obs.partial else "limited"}
            changed = True
            continue
        timestamp = datetime.fromisoformat(obs.source_updated_at.replace("Z", "+00:00")) if obs.source_updated_at else None
        if timestamp and not timestamp.tzinfo:
            raise ValueError("Source timestamps must include timezone")
        db.add(Record(id=uid(), space_id=goal.space_id, goal_id=goal.id, kind="evidence", source=obs.source,
                      source_id=obs.source_id, version=obs.version, source_updated_at=timestamp,
                      body={"facts": obs.facts, "summary": kind + ": " + str(obs.facts.get("state") or obs.facts.get("conclusion") or "observed"),
                            "url": obs.url, "partial": obs.partial, "confidence": "high" if not obs.partial else "limited", "data_mode": data_mode}))
        changed = True
    db.flush()
    return changed


def evaluate_progress(db, goal, run=None):
    before = snapshot(db, goal)
    tasks = list(db.scalars(select(Task).where(Task.goal_id == goal.id)))
    evidence = list(db.scalars(select(Record).where(Record.goal_id == goal.id, Record.kind == "evidence", Record.active.is_(True))))
    space = db.get(Space, goal.space_id)
    decisions = []
    for task in tasks:
        if task.status == "skipped":
            continue
        passed, reason, record = criterion_result(task, evidence)
        if passed is None:
            continue
        if passed:
            task.status = "done"
            for fix in tasks:
                if fix.criteria.get("blocker_for") == task.id and fix.status != "skipped" and not fix.criteria.get("confirmed_by_user"):
                    fix.status = "done"
        elif task.criteria.get("kind") == "deployment":
            task.status, task.priority = "blocked", 1
            fix = next((t for t in tasks if t.criteria.get("blocker_for") == task.id), None)
            if not fix and len(tasks) < 10:
                fix = Task(id=uid(), goal_id=goal.id, title="检查 Build Log，排查部署失败 / Inspect build log and unblock deployment", priority=1,
                           criteria={"kind": "user", "blocker_for": task.id}, depends_on=[], estimate_hours=1)
                db.add(fix)
                tasks.append(fix)
            elif fix and fix.status != "skipped" and not fix.criteria.get("confirmed_by_user"):
                fix.status, fix.priority = "todo", 1
        else:
            old = task.status
            task.status = "needs_review" if task.criteria.get("kind") == "readme" else "in_progress"
            if task.criteria.get("kind") == "readme" and old != "needs_review":
                decisions.append((task, reason, record))
    db.flush()
    update_risk(db, goal)
    after = snapshot(db, goal)
    changes = []
    before_tasks = {t["id"]: t for t in before["tasks"]}
    for t in after["tasks"]:
        old = before_tasks.get(t["id"])
        if old is None or any(old[k] != t[k] for k in ("status", "priority", "depends_on")):
            changes.append({"task_id": t["id"], "title": t["title"], "before": old, "after": t})
    if changes:
        goal.plan_version += 1
        patch = {"title": "Qianyan updated your plan", "changes": changes, "next_action": goal.next_action,
                 "risk": goal.risk, "evidence_ids": [r.id for r in evidence], "reason": "已核对当前事实和完成标准 / Current facts checked against completion standards"}
        if run:
            run.patch = patch
            run.metrics = {**run.metrics, "before": before, "after": after, "committed_version": goal.plan_version, "data_mode": "replay" if space.role == "demo" else "live"}
        failed = any(t.status == "blocked" for t in tasks)
        if failed or any(c["after"]["status"] == "done" for c in changes):
            notify(db, space, goal, "important_change", f"progress:{goal.id}:{fingerprint(changes)}", patch)
    for task, reason, record in decisions:
        notify(db, space, goal, "decision", f"review:{goal.id}:{task.id}:{fingerprint(reason)}",
               {"title": "需要你判断 / Your decision needed", "text": reason, "task_id": task.id,
                "next_action": "补齐完成条件，或明确确认 / Fill missing requirements or confirm explicitly", "evidence_ids": [record.id] if record else []})
    return changes


def undo_latest(db, goal):
    run = next((r for r in db.scalars(select(Run).where(Run.goal_id == goal.id, Run.status == "succeeded").order_by(Run.created_at.desc()))
                if r.metrics.get("committed_version") == goal.plan_version and r.metrics.get("before")), None)
    if not run:
        raise HTTPException(409, "当前版本没有可撤销的自动更新 / No automatic update can be undone at this version")
    old_tasks = {t["id"]: t for t in run.metrics["before"]["tasks"]}
    for task in db.scalars(select(Task).where(Task.goal_id == goal.id)):
        if task.id not in old_tasks:
            db.delete(task)
        else:
            before = old_tasks[task.id]
            for field in ("status", "priority", "depends_on", "criteria", "title"):
                setattr(task, field, before[field])
    goal.plan_version += 1
    run.metrics = {**run.metrics, "undone": True}
    update_risk(db, goal)
    # Evidence remains available; a future observation re-evaluates conditions.
    for note in db.scalars(select(Notification).where(Notification.goal_id == goal.id, Notification.send_state == "pending")):
        note.send_state = "cancelled"
