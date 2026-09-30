from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, String, Text, Integer, Boolean, DateTime, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import config


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


document = JSON().with_variant(JSONB, "postgresql")


class Space(Base):
    __tablename__ = "spaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    role: Mapped[str] = mapped_column(String(16))
    owner_key: Mapped[str | None] = mapped_column(String(20), unique=True)
    settings: Mapped[dict] = mapped_column(document, default=dict)
    settings_version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AccessSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    intent: Mapped[str] = mapped_column(Text)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    next_action: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(Text, default="尚未评估 / Not assessed")
    source_bindings: Mapped[dict] = mapped_column(document, default=dict)
    source_status: Mapped[dict] = mapped_column(document, default=dict)
    capacity_hours_per_day: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="todo")
    priority: Mapped[int] = mapped_column(Integer, default=3)
    depends_on: Mapped[list] = mapped_column(document, default=list)
    criteria: Mapped[dict] = mapped_column(document, default=lambda: {"kind": "user"})
    estimate_hours: Mapped[float] = mapped_column(default=1.0)
    followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (UniqueConstraint("space_id", "goal_id", "kind", "source", "source_id", "version", name="uq_record_source_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"), index=True)
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(String(24), index=True)
    body: Mapped[dict] = mapped_column(document, default=dict)
    source: Mapped[str] = mapped_column(String(40), default="user")
    source_id: Mapped[str | None] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(100), default="1")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    origin_record_id: Mapped[str | None] = mapped_column(String(36))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"), index=True)
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"))
    trigger: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    base_version: Mapped[int | None] = mapped_column(Integer)
    patch: Mapped[dict] = mapped_column(document, default=dict)
    metrics: Mapped[dict] = mapped_column(document, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    space_id: Mapped[str | None] = mapped_column(ForeignKey("spaces.id"))
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"))
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(document, default=dict)
    dedup_key: Mapped[str] = mapped_column(String(300), unique=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("space_id", "reason_key", name="uq_notification_reason"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"), index=True)
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"))
    category: Mapped[str] = mapped_column(String(30))
    reason_key: Mapped[str] = mapped_column(String(300))
    body: Mapped[dict] = mapped_column(document, default=dict)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    send_state: Mapped[str] = mapped_column(String(20), default="pending")
    provider_id: Mapped[str | None] = mapped_column(String(100))
    action_state: Mapped[str] = mapped_column(String(20), default="open")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


engine = create_engine(config().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def session():
    with SessionLocal() as db:
        yield db
