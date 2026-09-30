from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, Field, field_validator, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ButlerSettings(StrictModel):
    name: str = Field(default="Qianyan", min_length=1, max_length=40)
    style: Literal["concise", "warm", "detailed"] = "concise"
    preferences: list[str] = Field(default_factory=list, max_length=20)
    timezone: str = "Asia/Shanghai"
    notification_start: str = "08:00"
    notification_end: str = "22:00"
    proactive: bool = True
    capacity_hours_per_day: float | None = Field(default=None, gt=0, le=24)

    @field_validator("preferences")
    @classmethod
    def preference_length(cls, value):
        if any(len(x) > 300 for x in value):
            raise ValueError("偏好过长 / Preference too long")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("未知时区 / Unknown timezone")
        return value

    @field_validator("notification_start", "notification_end")
    @classmethod
    def valid_time(cls, value):
        datetime.strptime(value, "%H:%M")
        return value


class SettingsChange(StrictModel):
    settings: ButlerSettings
    version: int


class GoalInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    intent: str = Field(min_length=1, max_length=6000)
    deadline: datetime | None = None

    @field_validator("deadline")
    @classmethod
    def timezone_required(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("截止时间必须包含时区 / Deadline must include timezone")
        return value


class VersionInput(StrictModel):
    version: int | None = None
    plan_version: int | None = None


class GoalChange(StrictModel):
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=200)
    deadline: datetime | None = None
    capacity_hours_per_day: float | None = Field(default=None, gt=0, le=24)


class TaskChange(StrictModel):
    version: int
    status: Literal["todo", "in_progress", "blocked", "needs_review", "done", "skipped"] | None = None
    priority: int | None = Field(default=None, ge=1, le=5)


class MessageInput(StrictModel):
    text: str = Field(min_length=1, max_length=6000)
    goal_id: str | None = None


class MemoryInput(StrictModel):
    content: str = Field(min_length=1, max_length=3000)
    goal_id: str | None = None


class MemoryChange(StrictModel):
    content: str = Field(min_length=1, max_length=3000)
    version: str


class GitHubBinding(StrictModel):
    owner: str = Field(pattern=r"^[A-Za-z0-9-]{1,39}$")
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    branch: str = Field(default="main", min_length=1, max_length=150)
    workflow: str = Field(default="", max_length=100)
    core_dir: str = Field(default="src", max_length=150)

    @field_validator("core_dir", "branch", "workflow")
    @classmethod
    def no_traversal(cls, value):
        if ".." in value or "\\" in value or value.startswith("/") or "?" in value or "#" in value:
            raise ValueError("不合法路径 / Invalid source path")
        return value


class VercelBinding(StrictModel):
    project_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    team_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")


class SourceChange(StrictModel):
    version: int
    github: GitHubBinding | None = None
    vercel: VercelBinding | None = None


class NotificationAction(StrictModel):
    action: Literal["snooze", "handled", "mark_done", "keep_open"]
    followup_at: datetime | None = None
    version: int | None = None


class PlanTask(StrictModel):
    key: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=300)
    priority: int = Field(default=3, ge=1, le=5)
    depends_on: list[str] = Field(default_factory=list, max_length=10)
    criteria: dict = Field(default_factory=lambda: {"kind": "user"})
    estimate_hours: float = Field(default=1, gt=0, le=100)


class InitialPlan(StrictModel):
    tasks: list[PlanTask] = Field(min_length=1, max_length=10)
    next_action: str = Field(max_length=500)
    reason: str = Field(max_length=1500)
