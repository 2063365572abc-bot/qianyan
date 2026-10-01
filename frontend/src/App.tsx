import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Bell,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Coffee,
  ExternalLink,
  Fingerprint,
  Globe2,
  Leaf,
  LoaderCircle,
  LogOut,
  MessageCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Target,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { api, ApiError, setCsrf, setMode } from "./api";
import type {
  Evidence,
  Goal,
  Memory,
  Notification,
  Settings,
  State,
  Task,
} from "./types";

type Page = "today" | "memory" | "inbox" | "settings" | "demo";
const defaultSettings: Settings = {
  name: "Qianyan",
  style: "concise",
  preferences: [],
  timezone: "Asia/Shanghai",
  notification_start: "08:00",
  notification_end: "22:00",
  proactive: true,
  capacity_hours_per_day: null,
};
function words(body: unknown): string {
  if (body == null) return "";
  if (typeof body === "string") return body;
  if (typeof body === "number" || typeof body === "boolean")
    return String(body);
  if (Array.isArray(body)) return body.map(words).filter(Boolean).join("\n");
  const object = body as Record<string, unknown>;
  for (const key of [
    "text",
    "content",
    "message",
    "summary",
    "title",
    "description",
    "next_action",
  ])
    if (typeof object[key] === "string") return object[key] as string;
  return Object.entries(object)
    .filter(([key]) => !["id", "goal_id", "task_id"].includes(key))
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${words(value)}`)
    .join("\n");
}
function confirmationConflict(body: unknown): boolean {
  return typeof body === "object" && body !== null &&
    "user_confirmation_conflict" in body && body.user_confirmation_conflict === true;
}
export default function App() {
  const [lang, setLang] = useState<"zh" | "en">(() =>
    localStorage.getItem("qianyan-language") === "en" ? "en" : "zh",
  );
  const t = useCallback(
    (zh: string, en: string) => (lang === "zh" ? zh : en),
    [lang],
  );
  const [session, setSession] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [state, setState] = useState<State | null>(null);
  const [page, setPage] = useState<Page>(() =>
    new URLSearchParams(location.search).has("notification")
      ? "inbox"
      : "today",
  );
  const [goalId, setGoalId] = useState<string | null>(() =>
    new URLSearchParams(location.search).get("goal"),
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [newGoal, setNewGoal] = useState(false);
  const [taskGoal, setTaskGoal] = useState<Goal | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [sourceGoal, setSourceGoal] = useState<Goal | null>(null);
  const [editingGoal, setEditingGoal] = useState<Goal | null>(null);
  const [editingTask, setEditingTask] = useState<{ goal: Goal; task: Task } | null>(null);
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const [memoryEdit, setMemoryEdit] = useState<Memory | null>(null);
  const [memoryAdding, setMemoryAdding] = useState(false);
  const [text, setText] = useState("");
  const [password, setPassword] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const trStatus = (status: string) =>
    ({
      draft: t("待确认", "Draft"),
      active: t("跟进中", "Active"),
      paused: t("已暂停", "Paused"),
      done: t("已完成", "Done"),
      todo: t("待开始", "To do"),
      in_progress: t("进行中", "In progress"),
      blocked: t("有阻塞", "Blocked"),
      needs_review: t("待判断", "Needs review"),
      skipped: t("不再做", "Skipped"),
      pending: t("排队中", "Queued"),
      running: t("正在思考", "Working"),
      succeeded: t("已更新", "Updated"),
      failed: t("运行失败", "Failed"),
      accepted: t("平台已接受", "Provider accepted"),
      queued: t("待发送", "Queued"),
      disabled: t("未启用", "Disabled"),
      demo_only: t("仅网页样例", "Demo inbox only"),
      sent: t("平台已接受", "Provider accepted"),
      handled: t("已处理", "Handled"),
      snoozed: t("稍后跟进", "Snoozed"),
      snooze: t("稍后跟进", "Snoozed"),
      keep_open: t("保持进行中", "Kept open"),
      mark_done: t("用户确认完成", "User confirmed done"),
      open: t("待处理", "Open"),
      cancelled: t("已取消", "Cancelled"),
      unavailable: t("尚不可用", "Unavailable"),
      unknown: t("投递未知", "Delivery unknown"),
    })[status] || status;
  const criteriaText = (value: unknown) => {
    if (!value || typeof value !== "object") return words(value);
    const criteria = value as Record<string, unknown>;
    const rules: Record<string, string> = {
      user: t(
        "由你明确确认完成。",
        "Completion requires your explicit confirmation.",
      ),
      readme: t(
        "README 的简介、Installation、Usage 非空，安装部分有命令，使用部分有示例。结构符合不代表命令已运行验证。",
        "README has substantive Introduction, Installation and Usage sections, with installation commands and a usage example. Structural completion does not prove commands were tested.",
      ),
      ci: t(
        "指定 CI 工作流必须在当前提交版本上完成并成功。旧版本成功不代表新版本通过。",
        "The selected CI workflow must complete successfully for the current commit. A previous passing run does not prove a new commit passed.",
      ),
      deployment: t(
        "最新生产部署构建成功，且 Production URL 已确认。构建成功不代表功能验收通过。",
        "The latest production build succeeds and its production URL is confirmed. A successful build does not prove functional acceptance.",
      ),
      repo: t(
        "能够从已绑定的 GitHub 来源确认仓库存在。",
        "The bound GitHub source confirms the repository exists.",
      ),
      core_dir: t(
        "指定核心代码目录存在。这只能证明目录存在，不能证明核心功能已完成。",
        "The specified core code directory exists. Directory existence does not prove the MVP is complete.",
      ),
    };
    return [
      rules[String(criteria.kind)] || words(value),
      words(criteria.description),
      criteria.confirmed_by_user
        ? t("已有用户明确确认记录。", "Explicit user confirmation is recorded.")
        : null,
    ]
      .filter(Boolean)
      .join("\n");
  };
  const date = (value: string | null | undefined) =>
    value
      ? new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en-US", {
          dateStyle: "medium",
          timeStyle: "short",
          timeZone: state?.space.settings.timezone || "Asia/Shanghai",
        }).format(new Date(value))
      : t("未设置", "Not set");
  const load = useCallback(async () => {
    try {
      const next = await api<State>("/state");
      setState(next);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setSession(null);
        setState(null);
      } else setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    api<{ role: string; csrf_token: string }>("/auth/session")
      .then((data) => {
        setCsrf(data.csrf_token);
        setSession(data.role);
        return load();
      })
      .catch((e) => {
        if (!(e instanceof ApiError && e.status === 401)) setError(e.message);
      })
      .finally(() => setReady(true));
  }, [load]);
  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    localStorage.setItem("qianyan-language", lang);
  }, [lang]);
  useEffect(() => {
    if (state) setSettings(state.space.settings);
  }, [state?.space.settings_version]);
  const pending = !!state?.runs.some((run) =>
    ["pending", "running"].includes(run.status),
  );
  useEffect(() => {
    if (!session) return;
    const poll = setInterval(
      () => {
        if (document.visibilityState === "visible") void load();
      },
      pending ? 2000 : 10000,
    );
    const visible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      clearInterval(poll);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [session, pending, load]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [state?.messages.length]);
  const mutate = async (
    path: string,
    method = "POST",
    data?: unknown,
    success?: string,
  ) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(path, method, data);
      await load();
      if (success) setNotice(success);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (e instanceof ApiError && e.status === 409) await load();
      return false;
    } finally {
      setBusy(false);
    }
  };
  const auth = async (demo: boolean) => {
    setBusy(true);
    setError("");
    setMode(demo ? "demo" : "owner");
    try {
      const result = await api<{ role?: string; csrf_token: string }>(
        demo ? "/demo/session" : "/auth/login",
        "POST",
        demo ? {} : { password },
      );
      setCsrf(result.csrf_token);
      setSession(result.role || (demo ? "demo" : "owner"));
      setPassword("");
      history.replaceState({}, "", demo ? "/demo" : "/");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const logout = async () => {
    if (await mutate("/auth/logout")) {
      setSession(null);
      setState(null);
      setCsrf("");
      setMode("owner");
      history.replaceState({}, "", "/");
    }
  };
  const goal = state?.goals.find((x) => x.id === goalId) || state?.goals[0];
  const demo = state?.space.role === "demo" || session === "demo";
  const label = state?.space.settings.name || "Qianyan";
  const focusTasks = (state?.goals || [])
    .filter((item) => item.status === "active")
    .flatMap((item) =>
      item.tasks
        .filter(
          (task) =>
            !["done", "skipped"].includes(task.status) &&
            task.depends_on.every((id) =>
              item.tasks.some(
                (dependency) =>
                  dependency.id === id &&
                  ["done", "skipped"].includes(dependency.status),
              ),
            ),
        )
        .map((task) => ({ goal: item, task })),
    )
    .sort((a, b) => a.task.priority - b.task.priority)
    .slice(0, 3);
  const nav: { page: Page; icon: typeof Leaf; name: string }[] = [
    { page: "today", icon: Coffee, name: t("今日与目标", "Today & goals") },
    { page: "memory", icon: Fingerprint, name: t("长期记忆", "Memory") },
    { page: "inbox", icon: Bell, name: t("跟进收件箱", "Inbox") },
    { page: "settings", icon: Settings2, name: t("我的管家", "My butler") },
    ...(demo
      ? [
          {
            page: "demo" as Page,
            icon: Play,
            name: t("演示控制台", "Demo controls"),
          },
        ]
      : []),
  ];
  const exportData = async () => {
    try {
      const data = await api("/export");
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "qianyan-export.json";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const postMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    if (
      await mutate("/messages", "POST", {
        text: text.trim(),
        ...(goal ? { goal_id: goal.id } : {}),
      })
    )
      setText("");
  };
  const notificationAction = (item: Notification, action: string) =>
    mutate(
      `/notifications/${item.id}/actions`,
      "POST",
      {
        action,
        ...(item.goal_id
          ? {
              version: state?.goals.find((g) => g.id === item.goal_id)
                ?.plan_version,
            }
          : {}),
        ...(action === "snooze"
          ? { followup_at: new Date(Date.now() + 7200000).toISOString() }
          : {}),
      },
      t("已记录你的选择", "Your choice was recorded"),
    );
  const badge = (status: string) => (
    <span className={`badge status-${status}`}>{trStatus(status)}</span>
  );
  const alert = (
    <>
      {error && (
        <div className="banner danger" role="alert">
          <CircleHelp size={18} />
          <span>{error}</span>
          <button
            className="icon-btn"
            aria-label={t("关闭提示", "Dismiss")}
            onClick={() => setError("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {notice && (
        <div className="banner success" role="status">
          <Check size={18} />
          <span>{notice}</span>
          <button
            className="icon-btn"
            aria-label={t("关闭提示", "Dismiss")}
            onClick={() => setNotice("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </>
  );

  if (!ready)
    return (
      <div className="loading-screen">
        <Leaf size={36} />
        <p>{t("正在打开你的工作台…", "Opening your workspace…")}</p>
      </div>
    );
  if (!session)
    return (
      <div className="login-shell">
        <div className="login-art">
          <div className="brand light">
            <Leaf /> Qianyan <span>千言</span>
          </div>
          <div className="login-copy">
            <span className="eyebrow">YOUR PERSONAL BUTLER</span>
            <h1>
              {t(
                "交代一次。\n下一步，有我。",
                "Say it once.\nKeep moving forward.",
              )}
            </h1>
            <p>
              {t(
                "记住你的目标，留意真实进展，\n在需要的时候，主动陪你向前。",
                "A persistent companion for your goals,\nreal progress, and the next right step.",
              )}
            </p>
            <div className="promise">
              <ShieldCheck size={20} />
              {t(
                "记忆由你掌控 · 外部操作保持受限",
                "Your memory, your control · Bounded actions",
              )}
            </div>
          </div>
          <div className="orb orb-one" />
          <div className="orb orb-two" />
        </div>
        <main className="login-form">
          <button
            className="language"
            onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          >
            <Globe2 size={16} />
            {lang === "zh" ? "English" : "中文"}
          </button>
          <div className="login-inner">
            <div className="logo-mark">
              <Leaf size={28} />
            </div>
            <h2>{t("欢迎回来", "Welcome back")}</h2>
            <p className="muted">
              {t(
                "进入你的私人工作台，或体验隔离的公开样例。",
                "Open your personal workspace, or explore an isolated demo.",
              )}
            </p>
            {alert}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void auth(false);
              }}
            >
              <label>
                {t("工作台密码", "Workspace password")}
                <input
                  autoComplete="current-password"
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={t(
                    "输入本人访问密码",
                    "Enter your access password",
                  )}
                />
              </label>
              <button className="primary wide" disabled={busy}>
                {busy ? <LoaderCircle className="spin" size={17} /> : null}
                {t("进入私人工作台", "Open my workspace")}
                <ArrowRight size={18} />
              </button>
            </form>
            <div className="divider">
              {t("第一次来看看？", "Here to explore?")}
            </div>
            <button
              className="secondary wide"
              disabled={busy}
              onClick={() => void auth(true)}
            >
              <Play size={17} />
              {t("体验公开 Demo", "Try the public demo")}
            </button>
            <p className="fine-print">
              {t(
                "Demo 使用带标签的证据回放，不访问真实个人记忆，也不会发送微信消息。AI 是否可用会如实显示。",
                "The demo uses labeled evidence replay. It cannot access personal memory or send WeChat messages. AI availability is shown honestly.",
              )}
            </p>
          </div>
        </main>
      </div>
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("today");
          }}
        >
          <span className="logo-mark small">
            <Leaf size={20} />
          </span>
          Qianyan <span>千言</span>
        </a>
        <div className="workspace-chip">
          <span className={`status-dot ${demo ? "amber" : ""}`} />
          {demo
            ? t("隔离样例空间", "Isolated demo")
            : t("我的私人空间", "Personal workspace")}
        </div>
        <nav aria-label={t("工作台导航", "Workspace navigation")}>
          {nav.map((item) => (
            <button
              key={item.page}
              className={`nav-item ${page === item.page ? "selected" : ""}`}
              onClick={() => setPage(item.page)}
            >
              <item.icon size={19} />
              {item.name}
              {item.page === "inbox" &&
                !!state?.notifications.filter((n) => n.action_state === "open")
                  .length && (
                  <span className="count">
                    {
                      state.notifications.filter(
                        (n) => n.action_state === "open",
                      ).length
                    }
                  </span>
                )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="butler-note">
            <Sparkles size={19} />
            <p>
              {t(
                "我会留意变化，\n你专注于眼前。",
                "I’ll keep watch.\nYou focus on what matters.",
              )}
            </p>
          </div>
          <button className="nav-item" onClick={() => void exportData()}>
            <ArrowDownToLine size={18} />
            {t("导出我的数据", "Export my data")}
          </button>
          <button
            className="nav-item"
            onClick={() => void logout()}
            disabled={busy}
          >
            <LogOut size={18} />
            {t("退出空间", "Sign out")}
          </button>
          <div className="profile">
            <span className="avatar">{label.slice(0, 1)}</span>
            <div>
              <strong>{label}</strong>
              <small>{t("你的个人管家", "Your personal butler")}</small>
            </div>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            {t("工作台", "Workspace")}
            <ChevronRight size={14} />
            <strong>{nav.find((x) => x.page === page)?.name}</strong>
          </div>
          <div className="top-actions">
            <span className="cloud-label">
              <span
                className={`status-dot ${state?.health?.worker_online === false ? "amber" : ""}`}
              />
              {state?.health.worker_online
                ? t("后台正在运行", "Background worker active")
                : t("后台暂不可用", "Background worker unavailable")}
            </span>
            <button
              className="language"
              onClick={() => setLang(lang === "zh" ? "en" : "zh")}
            >
              <Globe2 size={15} />
              {lang === "zh" ? "EN" : "中文"}
            </button>
            <button
              className="icon-btn"
              aria-label={t("刷新状态", "Refresh state")}
              onClick={() => void load()}
            >
              <RefreshCw size={17} />
            </button>
            <button
              className="icon-btn mobile-action"
              aria-label={t("导出我的数据", "Export my data")}
              onClick={() => void exportData()}
            >
              <ArrowDownToLine size={17} />
            </button>
            <button
              className="icon-btn mobile-action"
              aria-label={t("退出空间", "Sign out")}
              disabled={busy}
              onClick={() => void logout()}
            >
              <LogOut size={17} />
            </button>
          </div>
        </header>
        <main className="main-content">
          {alert}
          {demo && (
            <div className="demo-strip">
              <Play size={15} />
              <span>
                {t(
                  "公开 Demo · 证据为样例回放 · 微信发送已禁用",
                  "Public demo · Evidence replay · WeChat sending disabled",
                )}
              </span>
              <button onClick={() => setPage("demo")}>
                {t("控制回放", "Replay controls")}
                <ArrowRight size={14} />
              </button>
            </div>
          )}
          {state && !state.integrations.nebius && (
            <div className="banner subtle">
              <CircleHelp size={17} />
              <span>
                {t(
                  "AI 尚未配置。可以体验证据回放、编辑记忆和手动维护计划；目前无法自动生成 AI 计划或回复。",
                  "AI is not configured. Evidence replay, memory editing, and manual plans are available. AI planning and replies are currently unavailable.",
                )}
              </span>
            </div>
          )}
          {!state ? (
            <div className="empty">
              <LoaderCircle className="spin" />
              <h2>{t("正在加载工作台", "Loading workspace")}</h2>
              <button className="secondary" onClick={() => void load()}>
                {t("重试", "Retry")}
              </button>
            </div>
          ) : (
            <>
              {page === "today" && (
                <>
                  <section className="page-heading">
                    <div>
                      <p className="eyebrow">A LITTLE CLARITY, EVERY DAY</p>
                      <h1>
                        {t(
                          "把下一步，安排清楚。",
                          "A clear next step. Every day.",
                        )}
                      </h1>
                      <p>
                        {t(
                          `${label} 在这里，陪你把交代的事持续往前推进。`,
                          `${label} is here to help your goals move forward.`,
                        )}
                      </p>
                    </div>
                    <button
                      className="primary"
                      onClick={() => setNewGoal(true)}
                    >
                      <Plus size={17} />
                      {t("交代一个目标", "New goal")}
                    </button>
                  </section>
                  <div className="overview">
                    <div>
                      <span className="metric-icon">
                        <Target size={19} />
                      </span>
                      <span>
                        <strong>
                          {
                            state.goals.filter((g) => g.status === "active")
                              .length
                          }
                        </strong>
                        <small>{t("正在跟进的目标", "Active goals")}</small>
                      </span>
                    </div>
                    <div>
                      <span className="metric-icon amber-bg">
                        <CircleHelp size={19} />
                      </span>
                      <span>
                        <strong>
                          {
                            state.goals
                              .flatMap((g) => g.tasks)
                              .filter((task) =>
                                ["blocked", "needs_review"].includes(
                                  task.status,
                                ),
                              ).length
                          }
                        </strong>
                        <small>{t("需要留意的事项", "Need attention")}</small>
                      </span>
                    </div>
                    <div>
                      <span className="metric-icon">
                        <Check size={19} />
                      </span>
                      <span>
                        <strong>
                          {
                            state.goals
                              .flatMap((g) => g.tasks)
                              .filter((task) => task.status === "done").length
                          }
                        </strong>
                        <small>{t("已有完成记录", "Completed tasks")}</small>
                      </span>
                    </div>
                    <div className="metric-note">
                      <ShieldCheck size={18} />
                      <span>
                        {t(
                          "事实有来源，判断可追溯。",
                          "Facts have sources. Decisions have a trail.",
                        )}
                      </span>
                    </div>
                  </div>
                  {focusTasks.length > 0 && (
                    <section className="focus-section">
                      <div className="section-row">
                        <h2>
                          {t("现在最值得处理", "Worth your attention now")}
                        </h2>
                        <span className="muted">
                          {t(
                            "按当前计划优先级与依赖",
                            "From current priorities & dependencies",
                          )}
                        </span>
                      </div>
                      <div className="focus-strip">
                        {focusTasks.map(({ goal: focusGoal, task }) => (
                          <button
                            key={task.id}
                            className="focus-card"
                            onClick={() => setGoalId(focusGoal.id)}
                          >
                            <span className="focus-top">
                              <span>{focusGoal.title}</span>
                              {badge(task.status)}
                            </span>
                            <strong>{task.title}</strong>
                            <span className="focus-link">
                              {t("查看当前计划", "View the plan")}
                              <ArrowRight size={14} />
                            </span>
                          </button>
                        ))}
                      </div>
                    </section>
                  )}
                  <div className="home-grid">
                    <div className="plan-column">
                      <div className="section-row">
                        <h2>{t("目标与计划", "Goals & plans")}</h2>
                        <span className="muted">
                          {state.goals.length} {t("个目标", "goals")}
                        </span>
                      </div>
                      {state.goals.length > 0 && (
                        <div className="goal-tabs">
                          {state.goals.map((item) => (
                            <button
                              key={item.id}
                              className={goal?.id === item.id ? "active" : ""}
                              onClick={() => {
                                setGoalId(item.id);
                                const url = new URL(location.href);
                                url.searchParams.set("goal", item.id);
                                history.replaceState({}, "", url);
                              }}
                            >
                              {item.title}
                            </button>
                          ))}
                        </div>
                      )}
                      {!goal ? (
                        <div className="panel empty">
                          <span className="empty-icon">
                            <Target size={28} />
                          </span>
                          <h3>
                            {t(
                              "先交代一件想完成的事",
                              "Start with something you want to finish",
                            )}
                          </h3>
                          <p>
                            {t(
                              "给我目标和期限，我会提议第一份计划。\n没有接入外部来源也可以开始。",
                              "Give me a goal and a deadline. I’ll propose a plan.\nYou can start without external integrations.",
                            )}
                          </p>
                          <button
                            className="primary"
                            onClick={() => setNewGoal(true)}
                          >
                            <Plus size={16} />
                            {t("创建第一个目标", "Create your first goal")}
                          </button>
                        </div>
                      ) : (
                        <section className="panel goal-panel">
                          <div className="goal-header">
                            <div className="goal-icon">
                              <Target size={24} />
                            </div>
                            <div className="goal-title">
                              <h2>{goal.title}</h2>
                              <p>{goal.intent}</p>
                            </div>
                            {badge(goal.status)}
                          </div>
                          <div className="deadline">
                            <Clock3 size={15} />
                            {t("截止时间", "Deadline")} · {date(goal.deadline)}
                            <button
                              className="text-button"
                              onClick={() => setEditingGoal(goal)}
                            >
                              {t("编辑", "Edit")}
                            </button>
                          </div>
                          <div className="progress-row">
                            <div className="progress-track">
                              <div
                                style={{
                                  width: `${goal.tasks.length ? (goal.tasks.filter((task) => task.status === "done").length / goal.tasks.length) * 100 : 0}%`,
                                }}
                              />
                            </div>
                            <span>
                              {
                                goal.tasks.filter(
                                  (task) => task.status === "done",
                                ).length
                              }
                              /{goal.tasks.length}
                            </span>
                          </div>
                          {goal.status === "draft" && (
                            <div className="draft-callout">
                              <CircleHelp size={18} />
                              <span>
                                {t(
                                  "先检查任务与完成标准，确认后我才开始持续跟进。",
                                  "Review the tasks and criteria. Follow-up starts after you confirm.",
                                )}
                              </span>
                              <button
                                disabled={
                                  busy || goal.tasks.length === 0 || pending
                                }
                                className="primary compact"
                                onClick={() =>
                                  void mutate(
                                    `/goals/${goal.id}/confirm`,
                                    "POST",
                                    { plan_version: goal.plan_version },
                                  )
                                }
                              >
                                {t("确认计划", "Confirm plan")}
                              </button>
                            </div>
                          )}
                          {pending && (
                            <div className="working">
                              <LoaderCircle className="spin" size={16} />
                              {t(
                                "正在处理已排队的工作，结果会自动出现…",
                                "Processing queued work. Results will appear here…",
                              )}
                            </div>
                          )}
                          <div className="tasks">
                            {goal.tasks.map((task, index) => (
                              <article
                                className={`task task-${task.status}`}
                                key={task.id}
                              >
                                <span className="task-number">
                                  {task.status === "done" ? (
                                    <Check size={16} />
                                  ) : (
                                    String(index + 1).padStart(2, "0")
                                  )}
                                </span>
                                <div className="task-body">
                                  <div className="task-top">
                                    <h3>{task.title}</h3>
                                    {badge(task.status)}
                                  </div>
                                  <div className="task-meta">
                                    <span>
                                      {t("优先级", "Priority")} {task.priority}
                                    </span>
                                    {!!(task.criteria as Record<string, unknown>)?.priority_locked_by_user && (
                                      <span>{t("由你固定", "Set by you")}</span>
                                    )}
                                    {task.estimate_hours != null && (
                                      <span>{task.estimate_hours}h</span>
                                    )}
                                    {task.followup_at && (
                                      <span>
                                        {t("下次跟进", "Follow-up")}:{" "}
                                        {date(task.followup_at)}
                                      </span>
                                    )}
                                  </div>
                                  <details>
                                    <summary>
                                      {t(
                                        "完成标准与依赖",
                                        "Criteria & dependencies",
                                      )}
                                    </summary>
                                    <p className="preserve">
                                      {criteriaText(task.criteria) ||
                                        t(
                                          "尚未定义完成标准",
                                          "No criteria defined yet",
                                        )}
                                    </p>
                                    {task.depends_on?.length > 0 && (
                                      <p>
                                        {t("依赖", "Depends on")}:{" "}
                                        {task.depends_on
                                          .map(
                                            (id) =>
                                              goal.tasks.find(
                                                (x) => x.id === id,
                                              )?.title || id,
                                          )
                                          .join("、")}
                                      </p>
                                    )}
                                    <div className="task-edit">
                                      <label>
                                        {t("任务状态", "Task status")}
                                        <select
                                          aria-label={t(
                                            "修改任务状态",
                                            "Update task status",
                                          )}
                                          disabled={busy || goal.status === "done"}
                                          value={task.status}
                                          onChange={(e) =>
                                            void mutate(
                                              `/tasks/${task.id}`,
                                              "PATCH",
                                              {
                                                version: goal.plan_version,
                                                status: e.target.value,
                                              },
                                            )
                                          }
                                        >
                                          {[
                                            "todo",
                                            "in_progress",
                                            "blocked",
                                            "needs_review",
                                            "done",
                                            "skipped",
                                          ].map((value) => (
                                            <option key={value} value={value}>
                                              {trStatus(value)}
                                            </option>
                                          ))}
                                        </select>
                                      </label>
                                      <label>
                                        {t("优先级", "Priority")}
                                        <select
                                          disabled={busy || goal.status === "done"}
                                          value={task.priority}
                                          onChange={(e) =>
                                            void mutate(
                                              `/tasks/${task.id}`,
                                              "PATCH",
                                              {
                                                version: goal.plan_version,
                                                priority: Number(
                                                  e.target.value,
                                                ),
                                              },
                                            )
                                          }
                                        >
                                          {[1, 2, 3, 4, 5].map((value) => (
                                            <option value={value} key={value}>
                                              {value}
                                            </option>
                                          ))}
                                        </select>
                                      </label>
                                    </div>
                                    <button className="secondary compact" disabled={busy || goal.status === "done"}
                                      onClick={() => setEditingTask({ goal, task })}>
                                      <Settings2 size={14} />{t("编辑任务与依赖", "Edit task and dependencies")}
                                    </button>
                                    <p className="fine-print">
                                      {t(
                                        "手动设为完成会记录为用户确认，不会伪造平台成功。",
                                        "Manual completion is recorded as user confirmation, never as a platform success.",
                                      )}
                                    </p>
                                  </details>
                                </div>
                              </article>
                            ))}
                          </div>
                          {goal.tasks.length === 0 && !pending && (
                            <p className="muted empty-inline">
                              {t(
                                "还没有生成任务。请查看对话与运行状态。",
                                "No tasks yet. Check the conversation and run status.",
                              )}
                            </p>
                          )}
                          <div className="next-step">
                            <span className="eyebrow">
                              {t("下一步", "NEXT STEP")}
                            </span>
                            <p>
                              {words(goal.next_action) ||
                                t(
                                  "等待计划生成或新的信息。",
                                  "Awaiting a plan or new information.",
                                )}
                            </p>
                            {!!goal.risk && (
                              <small>
                                {t("计划风险", "Plan risk")}: {words(goal.risk)}
                              </small>
                            )}
                          </div>
                          <div className="goal-actions">
                            {["active", "paused"].includes(goal.status) && (
                              <button
                                className="secondary compact"
                                disabled={busy}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      t(
                                        "确认这个目标整体已完成？这会记录你的决定并停止持续跟进。",
                                        "Confirm this entire goal is complete? Your decision will be recorded and ongoing follow-up will stop.",
                                      ),
                                    )
                                  )
                                    void mutate(
                                      `/goals/${goal.id}/complete`,
                                      "POST",
                                      { version: goal.plan_version },
                                      t(
                                        "目标整体完成已记录",
                                        "Goal completion recorded",
                                      ),
                                    );
                                }}
                              >
                                <Check size={14} />
                                {t("确认目标完成", "Complete goal")}
                              </button>
                            )}
                            <button
                              className="secondary compact"
                              disabled={busy}
                              onClick={() => setTaskGoal(goal)}
                            >
                              <Plus size={14} />
                              {t("添加任务", "Add task")}
                            </button>
                            <button
                              className="secondary compact"
                              disabled={busy || (!demo && (goal.status!=='active' || Object.keys(goal.source_bindings||{}).length===0))}
                              title={!demo ? t('确认计划并连接数据源后可以同步真实进展。','Confirm the plan and connect sources to sync real progress.') : undefined}
                              onClick={() => demo ? setPage('demo') :
                                void mutate(`/goals/${goal.id}/sync`, "POST", {
                                  version: goal.plan_version,
                                })
                              }
                            >
                              <RefreshCw size={14} />
                              {demo?t('回放进展','Replay progress'):t("检查进展", "Check progress")}
                            </button>
                            {!["draft", "done"].includes(goal.status) && (
                              <button
                                className="secondary compact"
                                disabled={busy}
                                onClick={() =>
                                  void mutate(
                                    `/goals/${goal.id}/${goal.status === "paused" ? "resume" : "pause"}`,
                                    "POST",
                                    { version: goal.plan_version },
                                  )
                                }
                              >
                                {goal.status === "paused" ? (
                                  <Play size={14} />
                                ) : (
                                  <Pause size={14} />
                                )}{" "}
                                {goal.status === "paused"
                                  ? t("恢复", "Resume")
                                  : t("暂停", "Pause")}
                              </button>
                            )}
                            <button
                              className="text-button"
                              disabled={busy}
                              onClick={() =>
                                void mutate(`/goals/${goal.id}/undo`, "POST", {
                                  version: goal.plan_version,
                                })
                              }
                            >
                              <Undo2 size={14} />
                              {t("撤销更新", "Undo update")}
                            </button>
                          </div>
                          <div className="source-section">
                            <div className="section-row">
                              <h3>{t("进展证据", "Progress evidence")}</h3>
                              {!demo && (
                                <button
                                  className="text-button"
                                  onClick={() => setSourceGoal(goal)}
                                >
                                  {t("连接数据源", "Connect sources")}
                                </button>
                              )}
                            </div>
                            <p className="fine-print">
                              {goal.source_status && words(goal.source_status)}
                              <br />
                              {t(
                                "观察到变化 ≠ 宣布完成。证据与完成标准分别核对。",
                                "An observation is not a completion claim. Evidence is checked against criteria.",
                              )}
                            </p>
                            {goal.evidence.length === 0 ? (
                              <p className="muted">
                                {t(
                                  "暂时没有外部证据；你可以在对话中补充无法观察的进展。",
                                  "No external evidence yet. Share unobservable progress in the conversation.",
                                )}
                              </p>
                            ) : (
                              goal.evidence.slice(0, 8).map((item) => (
                                <button
                                  className="evidence-row"
                                  key={item.id}
                                  onClick={() => setEvidence(item)}
                                >
                                  <span
                                    className={`source-pill source-${item.source.toLowerCase()}`}
                                  >
                                    {item.source}
                                  </span>
                                  <span className="evidence-text">
                                    {words(item.body)}
                                  </span>
                                  <ChevronRight size={16} />
                                </button>
                              ))
                            )}
                          </div>
                        </section>
                      )}
                    </div>
                    <aside className="conversation-column">
                      <section className="panel conversation">
                        <div className="conversation-heading">
                          <span className="logo-mark small">
                            <Leaf size={18} />
                          </span>
                          <div>
                            <h2>{t("与管家说说", "Talk to your butler")}</h2>
                            <p>
                              {t(
                                "不必每天重新解释",
                                "Pick up where you left off",
                              )}
                            </p>
                          </div>
                        </div>
                        <div className="messages" aria-live="polite">
                          {state.messages.length === 0 && (
                            <div className="welcome-message">
                              <Sparkles size={21} />
                              <p>
                                {t(
                                  "今天有什么想交给我持续跟进？",
                                  "What would you like me to keep track of?",
                                )}
                              </p>
                              <small>
                                {t(
                                  "也可以告诉我线下进展，或纠正我的理解。",
                                  "You can share offline progress or correct my understanding.",
                                )}
                              </small>
                            </div>
                          )}
                          {state.messages.slice(-30).map((message) => (
                            <div
                              key={message.id}
                              className={`message ${["user", "owner", "wecom"].includes(message.source) ? "user" : "assistant"}`}
                            >
                              <span className="message-author">
                                {["user", "owner", "wecom"].includes(
                                  message.source,
                                )
                                  ? t("你", "You")
                                  : label}
                                {message.source === "wecom" ? " · WeCom" : ""}
                              </span>
                              <p className="preserve">{words(message.body)}</p>
                              <time>{date(message.created_at)}</time>
                            </div>
                          ))}
                          <div ref={bottom} />
                        </div>
                        <form className="composer" onSubmit={postMessage}>
                          <textarea
                            value={text}
                            onChange={(e) => setText(e.target.value)}
                            placeholder={
                              goal
                                ? t(
                                    "告诉我新的进展或想法…",
                                    "Share progress or a thought…",
                                  )
                                : t(
                                    "交代一个目标，或者告诉我你的偏好…",
                                    "Share a goal or a preference…",
                                  )
                            }
                            aria-label={t(
                              "给管家发送消息",
                              "Message your butler",
                            )}
                            rows={2}
                            required
                            maxLength={6000}
                          />
                          <div>
                            <span>
                              {goal
                                ? t("当前目标", "Context") + ": " + goal.title
                                : t("个人空间", "Personal space")}
                            </span>
                            <button
                              className="send-button"
                              aria-label={t("发送", "Send")}
                              disabled={busy || !text.trim()}
                            >
                              <Send size={17} />
                            </button>
                          </div>
                        </form>
                      </section>
                      <section className="panel recent">
                        <div className="section-row">
                          <h2>{t("最近的计划更新", "Recent plan updates")}</h2>
                          <Sparkles size={17} />
                        </div>
                        {state.runs.length === 0 ? (
                          <p className="muted">
                            {t(
                              "发生有意义的变化后，更新结果会出现在这里。",
                              "Meaningful changes will appear here.",
                            )}
                          </p>
                        ) : (
                          state.runs.slice(0, 3).map((run) => (
                            <article className="run-card" key={run.id}>
                              <div className="section-row">
                                {badge(run.status)}
                                <time>{date(run.created_at)}</time>
                              </div>
                              <ResultBody
                                body={run.error || run.patch}
                                t={t}
                                status={trStatus}
                                evidence={
                                  state.goals.find((g) => g.id === run.goal_id)
                                    ?.evidence || []
                                }
                              />
                            </article>
                          ))
                        )}
                      </section>
                    </aside>
                  </div>
                </>
              )}
              {page === "memory" && (
                <>
                  <section className="page-heading">
                    <div>
                      <p className="eyebrow">REMEMBER WHAT MATTERS</p>
                      <h1>
                        {t("记得，也由你决定。", "Memory you can shape.")}
                      </h1>
                      <p>
                        {t(
                          "查看、纠正或遗忘。你的记忆始终由你掌控。",
                          "Review, correct, or forget. You stay in control of your memory.",
                        )}
                      </p>
                    </div>
                    <button
                      className="primary"
                      onClick={() => setMemoryAdding(true)}
                    >
                      <Plus size={16} />
                      {t("记住一件事", "Remember something")}
                    </button>
                  </section>
                  <div className="banner subtle">
                    <ShieldCheck size={17} />
                    {t(
                      "只记住你交代或有来源的事项。遗忘后的内容不应进入后续模型上下文。",
                      "Memory comes from your statements or sourced facts. Forgotten content should not enter future model context.",
                    )}
                  </div>
                  <div className="memory-grid">
                    {state.memories.map((item) => (
                      <article className="panel memory-card" key={item.id}>
                        <span className="memory-icon">
                          <Fingerprint size={20} />
                        </span>
                        <p className="preserve">{words(item.body)}</p>
                        <div className="memory-footer">
                          <span>
                            {item.source} · {date(item.created_at)}
                          </span>
                          <button
                            className="text-button"
                            onClick={() => setMemoryEdit(item)}
                          >
                            {t("编辑", "Edit")}
                          </button>
                          <button
                            className="icon-btn"
                            aria-label={t("遗忘此记忆", "Forget this memory")}
                            onClick={() => {
                              if (
                                window.confirm(
                                  t(
                                    "遗忘这条记忆？之后不会再作为上下文使用。",
                                    "Forget this memory? It will no longer be used as context.",
                                  ),
                                )
                              )
                                void mutate(
                                  `/memory/${item.id}?version=${item.version}`,
                                  "DELETE",
                                );
                            }}
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                  {!state.memories.length && (
                    <div className="panel empty">
                      <Fingerprint size={30} />
                      <h3>
                        {t(
                          "记忆从你交代的事开始",
                          "Memory starts with what you share",
                        )}
                      </h3>
                      <p>
                        {t(
                          "比如：“我更喜欢简洁的建议”。",
                          "For example: “I prefer concise suggestions.”",
                        )}
                      </p>
                    </div>
                  )}
                </>
              )}
              {page === "inbox" && (
                <>
                  <section className="page-heading">
                    <div>
                      <p className="eyebrow">ONLY WHEN IT MATTERS</p>
                      <h1>
                        {t(
                          "值得你留意的变化。",
                          "Changes worth your attention.",
                        )}
                      </h1>
                      <p>
                        {t(
                          "重要状态变化、需要决策、需要行动。其余安静记录。",
                          "Important changes, decisions, and actionable follow-ups. The rest stays quiet.",
                        )}
                      </p>
                    </div>
                  </section>
                  <div className="inbox-list">
                    {state.notifications.map((item) => (
                      <article
                        className="panel notification-card"
                        key={item.id}
                      >
                        <div className="notification-icon">
                          <Bell size={20} />
                        </div>
                        <div className="notification-main">
                          <div className="section-row">
                            <h3>
                              {state.goals.find((g) => g.id === item.goal_id)
                                ?.title || t("管家跟进", "Butler follow-up")}
                            </h3>
                            <span className="badge">
                              {trStatus(item.send_state)}
                            </span>
                          </div>
                          <ResultBody
                            body={item.body}
                            t={t}
                            status={trStatus}
                            evidence={
                              state.goals.find((g) => g.id === item.goal_id)
                                ?.evidence || []
                            }
                          />
                          <div className="notification-meta">
                            {(
                              {
                                important_change: t(
                                  "重要变化",
                                  "Important change",
                                ),
                                decision: t("需要判断", "Decision needed"),
                                reminder: t("行动提醒", "Action reminder"),
                              } as Record<string, string>
                            )[item.category] || item.category}{" "}
                            · {date(item.due_at)} ·{" "}
                            {trStatus(item.action_state)}
                          </div>
                          <div className="notification-actions">
                            <button
                              className="secondary compact"
                              onClick={() => {
                                setGoalId(item.goal_id);
                                setPage("today");
                              }}
                            >
                              {t("查看详情", "View details")}
                              <ArrowRight size={14} />
                            </button>
                            {item.action_state === "open" && (
                              <>
                                <button
                                  className="secondary compact"
                                  disabled={busy}
                                  onClick={() =>
                                    void notificationAction(item, "snooze")
                                  }
                                >
                                  <Clock3 size={14} />
                                  {t("两小时后提醒", "Remind in 2h")}
                                </button>
                                <button
                                  className="secondary compact"
                                  disabled={busy}
                                  onClick={() =>
                                    void notificationAction(item, "handled")
                                  }
                                >
                                  <Check size={14} />
                                  {t("我已处理", "I handled this")}
                                </button>
                                {[
                                  "decision",
                                  "needs_decision",
                                  "needs_review",
                                ].includes(item.category) && (
                                  <>
                                    <button
                                      className="text-button"
                                      disabled={busy}
                                      onClick={() =>
                                        void notificationAction(
                                          item,
                                          "mark_done",
                                        )
                                      }
                                    >
                                      {confirmationConflict(item.body)
                                        ? t("保留我的确认", "Keep my confirmation")
                                        : t("仍标记完成", "Mark done anyway")}
                                    </button>
                                    <button
                                      className="text-button"
                                      disabled={busy}
                                      onClick={() =>
                                        void notificationAction(
                                          item,
                                          "keep_open",
                                        )
                                      }
                                    >
                                      {confirmationConflict(item.body)
                                        ? t("重新检查任务", "Review task")
                                        : t("保持进行中", "Keep open")}
                                    </button>
                                  </>
                                )}
                              </>
                            )}
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                  {!state.notifications.length && (
                    <div className="panel empty">
                      <Bell size={30} />
                      <h3>
                        {t("目前无需打扰你", "Nothing to interrupt you with")}
                      </h3>
                      <p>
                        {t(
                          "普通同步和 Commit 不会堆满你的收件箱。",
                          "Routine syncs and commits won’t fill your inbox.",
                        )}
                      </p>
                    </div>
                  )}
                </>
              )}
              {page === "settings" && (
                <>
                  <section className="page-heading">
                    <div>
                      <p className="eyebrow">MAKE IT YOURS</p>
                      <h1>
                        {t("一个适合你的管家。", "A butler that fits you.")}
                      </h1>
                      <p>
                        {t(
                          "调整沟通方式和联系边界。事实与权限不会随风格改变。",
                          "Choose a communication style and contact boundaries. Facts and permissions remain fixed.",
                        )}
                      </p>
                    </div>
                  </section>
                  <div className="settings-grid">
                    <form
                      className="panel settings-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void mutate(
                          "/butler",
                          "PATCH",
                          {
                            settings: {
                              ...settings,
                              preferences: settings.preferences
                                .map((value) => value.trim())
                                .filter(Boolean),
                            },
                            version: state.space.settings_version,
                          },
                          t("管家设置已保存", "Butler settings saved"),
                        );
                      }}
                    >
                      <h2>{t("个性与偏好", "Personality & preferences")}</h2>
                      <label>
                        {t("管家称呼", "Butler name")}
                        <input
                          required
                          maxLength={40}
                          value={settings.name}
                          onChange={(e) =>
                            setSettings({ ...settings, name: e.target.value })
                          }
                        />
                      </label>
                      <label>
                        {t("沟通风格", "Communication style")}
                        <select
                          value={settings.style}
                          onChange={(e) =>
                            setSettings({
                              ...settings,
                              style: e.target.value as Settings["style"],
                            })
                          }
                        >
                          <option value="concise">
                            {t("简洁 · 直奔重点", "Concise · To the point")}
                          </option>
                          <option value="warm">
                            {t(
                              "温和 · 多一点陪伴",
                              "Warm · A little more care",
                            )}
                          </option>
                          <option value="detailed">
                            {t(
                              "细致 · 解释依据",
                              "Detailed · Explain the reasoning",
                            )}
                          </option>
                        </select>
                      </label>
                      <label>
                        {t(
                          "个人偏好（每行一条）",
                          "Preferences (one per line)",
                        )}
                        <textarea
                          rows={4}
                          value={settings.preferences.join("\n")}
                          onChange={(e) =>
                            setSettings({
                              ...settings,
                              preferences: e.target.value.split("\n"),
                            })
                          }
                          placeholder={t(
                            "例如：先告诉我最重要的一件事",
                            "For example: tell me the most important thing first",
                          )}
                        />
                      </label>
                      <label>
                        {t("时区（IANA 名称）", "Time zone (IANA name)")}
                        <input
                          required
                          value={settings.timezone}
                          onChange={(e) =>
                            setSettings({
                              ...settings,
                              timezone: e.target.value,
                            })
                          }
                        />
                      </label>
                      <h2 className="form-section">
                        {t("主动联系", "Proactive contact")}
                      </h2>
                      <label className="switch-row">
                        <span>
                          <strong>
                            {t("允许主动通知", "Allow proactive notifications")}
                          </strong>
                          <small>
                            {t(
                              "关闭后仍维护计划；暂停目标单独控制。",
                              "Plans continue to update when off. Goal pause is separate.",
                            )}
                          </small>
                        </span>
                        <input
                          type="checkbox"
                          checked={settings.proactive}
                          onChange={(e) =>
                            setSettings({
                              ...settings,
                              proactive: e.target.checked,
                            })
                          }
                        />
                      </label>
                      <div className="form-pair">
                        <label>
                          {t("联系开始时间", "Contact window starts")}
                          <input
                            type="time"
                            required
                            value={settings.notification_start}
                            onChange={(e) =>
                              setSettings({
                                ...settings,
                                notification_start: e.target.value,
                              })
                            }
                          />
                        </label>
                        <label>
                          {t("联系结束时间", "Contact window ends")}
                          <input
                            type="time"
                            required
                            value={settings.notification_end}
                            onChange={(e) =>
                              setSettings({
                                ...settings,
                                notification_end: e.target.value,
                              })
                            }
                          />
                        </label>
                      </div>
                      <label>
                        {t(
                          "每天可投入时间（小时，可留空）",
                          "Available hours per day (optional)",
                        )}
                        <input
                          type="number"
                          min="0.25"
                          max="24"
                          step="0.25"
                          value={settings.capacity_hours_per_day ?? ""}
                          onChange={(e) =>
                            setSettings({
                              ...settings,
                              capacity_hours_per_day: e.target.value
                                ? Number(e.target.value)
                                : null,
                            })
                          }
                        />
                        <span className="fine-print">
                          {t(
                            "未填写时，我会明确说明无法判断计划是否在容量内。",
                            "Without this, schedule capacity stays unknown.",
                          )}
                        </span>
                      </label>
                      <button className="primary" disabled={busy}>
                        {busy ? (
                          <LoaderCircle className="spin" size={16} />
                        ) : (
                          <Check size={16} />
                        )}{" "}
                        {t("保存设置", "Save settings")}
                      </button>
                    </form>
                    <aside>
                      <section className="panel permissions">
                        <ShieldCheck size={24} />
                        <h2>{t("自主权限", "Autonomy boundaries")}</h2>
                        <p>
                          {t(
                            "能安全维护的内部计划，由我处理。重要外部影响，保持受限。",
                            "I can maintain internal plans. Significant external actions stay restricted.",
                          )}
                        </p>
                        {[
                          t("读取已连通的数据源", "Read connected sources"),
                          t(
                            "更新内部任务与优先级",
                            "Update internal tasks and priorities",
                          ),
                          t("安排后续跟进", "Schedule follow-up"),
                          t("向本人发送通知", "Notify the owner"),
                        ].map((value) => (
                          <div className="permission allowed" key={value}>
                            <Check size={16} />
                            {value}
                          </div>
                        ))}
                        {[
                          t(
                            "修改代码或替你部署",
                            "Edit code or deploy for you",
                          ),
                          t(
                            "发第三方邮件、付款、提交表单",
                            "Send third-party emails, pay, submit forms",
                          ),
                        ].map((value) => (
                          <div className="permission restricted" key={value}>
                            <X size={16} />
                            {value}
                          </div>
                        ))}
                        {demo && (
                          <p className="fine-print">
                            {t(
                              "公开 Demo 额外禁止真实微信发送。",
                              "The public demo additionally blocks real WeChat delivery.",
                            )}
                          </p>
                        )}
                      </section>
                      <section className="panel integration-card">
                        <h2>
                          {t("服务与接入状态", "Services & integrations")}
                        </h2>
                        {Object.entries(state.integrations || {}).map(
                          ([key, value]) => (
                            <div className="integration" key={key}>
                              <strong>{key.replaceAll("_", " ")}</strong>
                              <span className="preserve muted">
                                {typeof value === "boolean"
                                  ? value
                                    ? t(
                                        "已配置，等待实际连接验证",
                                        "Configured; live connection requires verification",
                                      )
                                    : t("尚未配置", "Not configured")
                                  : words(value)}
                              </span>
                            </div>
                          ),
                        )}
                        <details>
                          <summary>{t("运行状态", "Runtime status")}</summary>
                          <p className="preserve fine-print">
                            {words(state.health)}
                          </p>
                        </details>
                        <p className="fine-print">
                          {t(
                            "连接密钥由服务器环境配置，网页不会收集或显示密钥。",
                            "Connection secrets are configured on the server, never collected or displayed here.",
                          )}
                        </p>
                      </section>
                    </aside>
                  </div>
                </>
              )}
              {page === "demo" && (
                <>
                  <section className="page-heading">
                    <div>
                      <p className="eyebrow">EXPERIENCE THE LOOP</p>
                      <h1>
                        {t(
                          "看看管家如何主动推进。",
                          "Watch your butler move a plan forward.",
                        )}
                      </h1>
                      <p>
                        {t(
                          "在你的独立样例中回放事实变化，使用相同的计划规则。",
                          "Replay changes in your isolated space using the same planning rules.",
                        )}
                      </p>
                    </div>
                  </section>
                  <div className="panel demo-intro">
                    <span className="goal-icon">
                      <Play size={24} />
                    </span>
                    <div>
                      <h2>
                        {t("这是一段带标签的回放", "This is a labeled replay")}
                      </h2>
                      <p>
                        {t(
                          "证据来自公开样例，不代表已连接你的 GitHub、Vercel 或微信。模型失败不会伪装成成功。",
                          "Evidence comes from fixtures, not your GitHub, Vercel, or WeChat. Model failure is never presented as success.",
                        )}
                      </p>
                    </div>
                  </div>
                  <div className="demo-events">
                    {[
                      {
                        event: "incomplete_readme",
                        title: t(
                          "README 出现，但内容不完整",
                          "README appears, but is incomplete",
                        ),
                        desc: t(
                          "只有文件存在不等于完成；Installation 缺项。",
                          "File existence is not completion. Installation is missing.",
                        ),
                      },
                      {
                        event: "readme_complete",
                        title: t(
                          "README 达到结构标准",
                          "README meets structural criteria",
                        ),
                        desc: t(
                          "记录来源与版本，核对后更新结构任务。",
                          "Record the source and revision, then check the criteria.",
                        ),
                      },
                      {
                        event: "deployment_failed",
                        title: t(
                          "新的生产部署失败",
                          "A new production deployment fails",
                        ),
                        desc: t(
                          "提升排查优先级，调整后续依赖；旧版本可能仍在线。",
                          "Prioritize diagnosis and adjust dependencies. The old version may remain online.",
                        ),
                      },
                      {
                        event: "deployment_recovered",
                        title: t(
                          "后续生产部署成功",
                          "A later production deployment succeeds",
                        ),
                        desc: t(
                          "解除对应构建阻塞，安排接下来的行动。",
                          "Resolve the build blocker and arrange the next action.",
                        ),
                      },
                    ].map((item, index) => (
                      <button
                        className="panel demo-event"
                        key={item.event}
                        disabled={busy || pending}
                        onClick={() =>
                          void mutate(
                            "/demo/events",
                            "POST",
                            { event: item.event },
                            t(
                              "回放事件已提交，请查看目标与计划变化",
                              "Replay event submitted. Check your goals and plan updates.",
                            ),
                          )
                        }
                      >
                        <span className="event-number">0{index + 1}</span>
                        <h3>{item.title}</h3>
                        <p>{item.desc}</p>
                        <span className="text-button">
                          {t("回放这个变化", "Replay this change")}
                          <ArrowRight size={16} />
                        </span>
                      </button>
                    ))}
                  </div>
                  <div className="demo-footer">
                    <button
                      className="primary"
                      onClick={() => setPage("today")}
                    >
                      {t("查看工作台", "View workspace")}
                      <ArrowRight size={16} />
                    </button>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm(
                            t(
                              "重置你的样例目标、记忆和消息？",
                              "Reset your demo goals, memory, and messages?",
                            ),
                          )
                        )
                          void mutate(
                            "/demo/reset",
                            "POST",
                            {},
                            t("独立样例已重置", "Your isolated demo was reset"),
                          );
                      }}
                    >
                      <RefreshCw size={16} />
                      {t("重置我的样例", "Reset my demo")}
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </main>
        <footer className="workspace-footer">
          <span>
            Qianyan ·{" "}
            {t("交代一次，持续向前。", "Say it once. Keep moving forward.")}
          </span>
          <span>
            {t(
              "记忆可控 · 证据可见 · 权限明确",
              "Editable memory · Visible evidence · Clear permissions",
            )}
          </span>
        </footer>
      </div>
      {newGoal && (
        <Modal
          error={error}
          title={t("交代一个目标", "Give your butler a goal")}
          onClose={() => setNewGoal(false)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              const deadline = String(form.get("deadline"));
              if (
                await mutate(
                  "/goals",
                  "POST",
                  {
                    title: form.get("title"),
                    intent: form.get("intent"),
                    deadline: deadline
                      ? new Date(deadline).toISOString()
                      : null,
                  },
                  t(
                    "目标已记录，正在准备初始计划",
                    "Goal recorded. Preparing an initial plan.",
                  ),
                )
              ) {
                setNewGoal(false);
                setGoalId(null);
                setPage("today");
              }
            }}
          >
            <label>
              {t("目标名称", "Goal title")}
              <input
                name="title"
                required
                maxLength={160}
                placeholder={t(
                  "例如：完整提交黑客松项目",
                  "Example: submit a complete hackathon project",
                )}
              />
            </label>
            <label>
              {t("你希望完成什么？", "What does success look like?")}
              <textarea
                name="intent"
                required
                rows={4}
                maxLength={4000}
                placeholder={t(
                  "描述想达到的结果，以及必须满足的条件…",
                  "Describe the outcome and the conditions that matter…",
                )}
              />
            </label>
            <label>
              {t("明确的截止日期（可不填）", "Exact deadline (optional)")}
              <input name="deadline" type="datetime-local" />
              <span className="fine-print">
                {t(
                  "输入按当前设备时区换算；提交后显示绝对时间。",
                  "Entered in your device time zone; displayed as an absolute time after saving.",
                )}
              </span>
            </label>
            <button className="primary wide" disabled={busy}>
              <Sparkles size={16} />
              {t("让管家提议计划", "Propose a plan")}
            </button>
          </form>
        </Modal>
      )}
      {taskGoal && (
        <Modal
          error={error}
          title={t("添加人工任务", "Add a manual task")}
          onClose={() => setTaskGoal(null)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              const estimate = String(form.get("estimate"));
              if (
                await mutate(`/goals/${taskGoal.id}/tasks`, "POST", {
                  version: taskGoal.plan_version,
                  title: form.get("title"),
                  priority: Number(form.get("priority")),
                  criteria: {
                    kind: "user",
                    description:
                      form.get("criteria") ||
                      t(
                        "由用户明确确认完成",
                        "Completed by explicit user confirmation",
                      ),
                  },
                  estimate_hours: estimate ? Number(estimate) : 1,
                  depends_on: form.getAll("depends_on"),
                })
              )
                setTaskGoal(null);
            }}
          >
            <p className="fine-print">
              {t(
                "模型未配置或不可用时，你仍可手动建立计划。任务完成由你确认。",
                "You can build a plan manually when the model is unavailable. You confirm completion.",
              )}
            </p>
            <label>
              {t("任务名称", "Task title")}
              <input name="title" required maxLength={160} />
            </label>
            <label>
              {t("完成标准", "Completion criteria")}
              <textarea name="criteria" rows={3} />
            </label>
            <div className="form-pair">
              <label>
                {t("优先级", "Priority")}
                <select name="priority" defaultValue="3">
                  {[1, 2, 3, 4, 5].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("预估小时（可选）", "Estimated hours (optional)")}
                <input
                  name="estimate"
                  type="number"
                  min="0.25"
                  max="100"
                  step="0.25"
                />
              </label>
            </div>
            {taskGoal.tasks.length > 0 && (
              <fieldset className="dependency-options">
                <legend>
                  {t("依赖任务（可选）", "Dependencies (optional)")}
                </legend>
                {taskGoal.tasks.map((task) => (
                  <label key={task.id}>
                    <input type="checkbox" name="depends_on" value={task.id} />
                    {task.title}
                  </label>
                ))}
              </fieldset>
            )}
            <button className="primary wide" disabled={busy}>
              <Plus size={16} />
              {t("添加任务", "Add task")}
            </button>
          </form>
        </Modal>
      )}
      {editingTask && (
        <Modal error={error} title={t("编辑任务", "Edit task")} onClose={() => setEditingTask(null)}>
          <form onSubmit={async (e) => {
            e.preventDefault();
            const form = new FormData(e.currentTarget);
            const description = String(form.get("description") || "").trim();
            if (await mutate(`/tasks/${editingTask.task.id}`, "PATCH", {
              version: editingTask.goal.plan_version,
              title: form.get("title"),
              criteria: { kind: form.get("kind"), ...(description ? { description } : {}) },
              estimate_hours: Number(form.get("estimate")),
              priority: Number(form.get("priority")),
              priority_mode: form.get("priority_mode"),
              depends_on: form.getAll("depends_on"),
            })) setEditingTask(null);
          }}>
            <label>{t("任务名称", "Task title")}
              <input name="title" defaultValue={editingTask.task.title} required maxLength={300} />
            </label>
            <label>{t("完成判断方式", "Completion rule")}
              <select name="kind" defaultValue={String((editingTask.task.criteria as Record<string, unknown>)?.kind || "user")}>
                {[
                  ["user", t("由我确认", "My confirmation")],
                  ["readme", t("README 结构符合标准", "README structure")],
                  ["ci", t("当前提交的指定 CI 通过", "Current-commit CI success")],
                  ["deployment", t("生产构建成功与 URL 已确认", "Production build and URL")],
                  ["repo", t("绑定仓库存在", "Bound repository exists")],
                  ["core_dir", t("指定核心目录存在", "Core directory exists")],
                ].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label>{t("完成要求补充说明", "Additional completion notes")}
              <textarea name="description" rows={3} maxLength={1500}
                defaultValue={String((editingTask.task.criteria as Record<string, unknown>)?.description || "")} />
            </label>
            <p className="fine-print">{t("补充说明不会改变自动核验规则。需要主观验收时请选择「由我确认」。修改已完成任务的标准后会重新等待判断。", "Notes do not change automatic checks. Choose My confirmation for subjective acceptance. Changing a completed task's rule requires review.")}</p>
            <div className="form-pair">
              <label>{t("优先级", "Priority")}
                <select name="priority" defaultValue={editingTask.task.priority}>
                  {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <label>{t("预估小时", "Estimated hours")}
                <input name="estimate" type="number" min="0.25" max="100" step="0.25" required defaultValue={editingTask.task.estimate_hours || 1} />
              </label>
            </div>
            <label>{t("优先级由谁维护", "Who maintains priority")}
              <select name="priority_mode" defaultValue={(editingTask.task.criteria as Record<string, unknown>)?.priority_locked_by_user ? "manual" : "auto"}>
                <option value="auto">{t("管家根据进度调整", "Butler adjusts with progress")}</option>
                <option value="manual">{t("固定我的选择", "Keep my choice")}</option>
              </select>
            </label>
            <fieldset className="dependency-options">
              <legend>{t("依赖任务", "Dependencies")}</legend>
              {editingTask.goal.tasks.filter(item => item.id !== editingTask.task.id).map(item => (
                <label key={item.id}><input type="checkbox" name="depends_on" value={item.id}
                  defaultChecked={editingTask.task.depends_on.includes(item.id)} />{item.title}</label>
              ))}
            </fieldset>
            <button className="primary wide" disabled={busy}>{t("保存任务", "Save task")}</button>
            {editingTask.goal.status === "draft" && (
              <button type="button" className="secondary wide" disabled={busy} onClick={async () => {
                if (await mutate(`/tasks/${editingTask.task.id}?version=${editingTask.goal.plan_version}`, "DELETE")) setEditingTask(null);
              }}><Trash2 size={15} />{t("删除草稿任务", "Delete draft task")}</button>
            )}
          </form>
        </Modal>
      )}
      {editingGoal && (
        <Modal
          error={error}
          title={t("修改目标", "Edit goal")}
          onClose={() => setEditingGoal(null)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              const deadline = String(form.get("deadline"));
              if (
                await mutate(`/goals/${editingGoal.id}`, "PATCH", {
                  version: editingGoal.plan_version,
                  title: form.get("title"),
                  deadline: deadline ? new Date(deadline).toISOString() : null,
                })
              )
                setEditingGoal(null);
            }}
          >
            <label>
              {t("目标名称", "Goal title")}
              <input
                name="title"
                defaultValue={editingGoal.title}
                required
                maxLength={160}
              />
            </label>
            <label>
              {t("硬截止时间", "Hard deadline")}
              <input
                type="datetime-local"
                name="deadline"
                defaultValue={
                  editingGoal.deadline
                    ? new Date(
                        new Date(editingGoal.deadline).getTime() -
                          new Date().getTimezoneOffset() * 60000,
                      )
                        .toISOString()
                        .slice(0, 16)
                    : ""
                }
              />
            </label>
            <p className="fine-print">
              {t(
                "管家不会自主修改硬截止时间。此处按设备时区输入。",
                "The butler will not change your hard deadline. Enter this in your device time zone.",
              )}
            </p>
            <button disabled={busy} className="primary wide">
              {t("保存修改", "Save changes")}
            </button>
          </form>
        </Modal>
      )}
      {evidence && (
        <Modal
          error={error}
          title={t("证据详情", "Evidence details")}
          onClose={() => setEvidence(null)}
        >
          <span className="source-pill">{evidence.source}</span>
          <p className="preserve evidence-body">{words(evidence.body)}</p>
          <dl className="evidence-details">
            <dt>{t("来源标识", "Source identifier")}</dt>
            <dd>{evidence.source_id}</dd>
            <dt>{t("来源版本", "Source revision")}</dt>
            <dd>{evidence.version}</dd>
            <dt>{t("观察时间", "Observed at")}</dt>
            <dd>{date(evidence.observed_at)}</dd>
            <dt>{t("来源更新时间", "Source updated at")}</dt>
            <dd>{date(evidence.source_updated_at)}</dd>
            {typeof evidence.body === "object" && evidence.body !== null && (
              <>
                <dt>{t("来源可信分类", "Source confidence")}</dt>
                <dd>
                  {words(
                    (evidence.body as Record<string, unknown>).confidence,
                  ) || t("未提供", "Not provided")}
                </dd>
                <dt>{t("数据模式", "Data mode")}</dt>
                <dd>
                  {(evidence.body as Record<string, unknown>).data_mode ===
                  "replay"
                    ? t("样例回放", "Fixture replay")
                    : demo
                      ? t("样例空间", "Demo space")
                      : t("来源事实记录", "Sourced fact record")}
                </dd>
                {!!(evidence.body as Record<string, unknown>).partial && (
                  <>
                    <dt>{t("观测完整性", "Completeness")}</dt>
                    <dd>
                      {t(
                        "部分观测，不能证明完整完成",
                        "Partial observation; not proof of completion",
                      )}
                    </dd>
                  </>
                )}
              </>
            )}
          </dl>
          {typeof evidence.body === "object" &&
            evidence.body !== null &&
            !!(evidence.body as Record<string, unknown>).facts && (
              <details>
                <summary>
                  {t("查看来源事实字段", "Inspect source fact fields")}
                </summary>
                <p className="preserve">
                  {words((evidence.body as Record<string, unknown>).facts)}
                </p>
              </details>
            )}
          {typeof evidence.body === "object" &&
            evidence.body !== null &&
            typeof (evidence.body as Record<string, unknown>).url ===
              "string" &&
            /^https:\/\//.test(
              String((evidence.body as Record<string, unknown>).url),
            ) && (
              <a
                className="secondary"
                href={String((evidence.body as Record<string, unknown>).url)}
                rel="noopener noreferrer"
                target="_blank"
              >
                <ExternalLink size={15} />
                {t("查看原始来源", "Open original source")}
              </a>
            )}
          <p className="fine-print">
            {t(
              "这是事实记录；是否完成仍取决于任务标准或你的明确确认。",
              "This is a fact record. Completion depends on criteria or your explicit confirmation.",
            )}
          </p>
        </Modal>
      )}
      {(memoryEdit || memoryAdding) && (
        <Modal
          error={error}
          title={
            memoryEdit
              ? t("编辑记忆", "Edit memory")
              : t("记住一件事", "Remember something")
          }
          onClose={() => {
            setMemoryEdit(null);
            setMemoryAdding(false);
          }}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const content = new FormData(e.currentTarget).get("content");
              if (
                await mutate(
                  memoryEdit ? `/memory/${memoryEdit.id}` : "/memory",
                  memoryEdit ? "PATCH" : "POST",
                  {
                    content,
                    ...(memoryEdit ? { version: memoryEdit.version } : {}),
                  },
                )
              ) {
                setMemoryEdit(null);
                setMemoryAdding(false);
              }
            }}
          >
            <label>
              {t("记忆内容", "Memory content")}
              <textarea
                name="content"
                required
                rows={5}
                maxLength={3000}
                defaultValue={memoryEdit ? words(memoryEdit.body) : ""}
              />
            </label>
            <button className="primary wide" disabled={busy}>
              {t("保存记忆", "Save memory")}
            </button>
          </form>
        </Modal>
      )}
      {sourceGoal && (
        <Modal
          error={error}
          title={t("绑定只读数据源", "Connect read-only sources")}
          onClose={() => setSourceGoal(null)}
        >
          <p className="fine-print">
            {t(
              "只提交项目标识。访问令牌请在服务器环境配置；不在此输入。留空的来源不提交。",
              "Enter project identifiers only. Configure access tokens on the server. Empty sources are omitted.",
            )}
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const data = new FormData(e.currentTarget);
              const owner = String(data.get("owner")).trim();
              const project = String(data.get("project")).trim();
              if (!owner && !project) {
                setError(t("请至少填写一个数据源", "Fill at least one source"));
                return;
              }
              if (
                await mutate(`/goals/${sourceGoal.id}/sources`, "POST", {
                  version: sourceGoal.plan_version,
                  ...(owner
                    ? {
                        github: {
                          owner,
                          repo: data.get("repo"),
                          branch: data.get("branch") || "main",
                          workflow: data.get("workflow") || undefined,
                          core_dir: data.get("core_dir") || undefined,
                        },
                      }
                    : {}),
                  ...(project
                    ? {
                        vercel: {
                          project_id: project,
                          team_id: data.get("team") || undefined,
                        },
                      }
                    : {}),
                })
              )
                setSourceGoal(null);
            }}
          >
            <h3>GitHub</h3>
            <div className="form-pair">
              <label>
                Owner
                <input
                  name="owner"
                  defaultValue={String(
                    (
                      sourceGoal.source_bindings?.github as Record<
                        string,
                        unknown
                      >
                    )?.owner || "",
                  )}
                />
              </label>
              <label>
                Repository
                <input
                  name="repo"
                  defaultValue={String(
                    (
                      sourceGoal.source_bindings?.github as Record<
                        string,
                        unknown
                      >
                    )?.repo || "",
                  )}
                />
              </label>
            </div>
            <div className="form-pair">
              <label>
                Branch
                <input name="branch" placeholder="main" />
              </label>
              <label>
                CI workflow
                <input name="workflow" placeholder="test.yml" />
              </label>
            </div>
            <label>
              {t("核心代码目录", "Core code directory")}
              <input name="core_dir" placeholder="src" />
            </label>
            <h3>Vercel</h3>
            <label>
              Project ID
              <input
                name="project"
                defaultValue={String(
                  (
                    sourceGoal.source_bindings?.vercel as Record<
                      string,
                      unknown
                    >
                  )?.project_id || "",
                )}
              />
            </label>
            <label>
              Team ID ({t("可选", "optional")})<input name="team" />
            </label>
            <button className="primary wide" disabled={busy}>
              {t("绑定数据源", "Connect sources")}
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}
function ResultBody({
  body,
  t,
  status,
  evidence,
}: {
  body: unknown;
  t: (zh: string, en: string) => string;
  status: (value: string) => string;
  evidence: Evidence[];
}) {
  if (!body || typeof body !== "object" || Array.isArray(body))
    return (
      <p className="result-text preserve">
        {words(body) || t("等待处理结果", "Awaiting a result")}
      </p>
    );
  const patch = body as Record<string, unknown>;
  const changes = Array.isArray(patch.changes)
    ? (patch.changes as Record<string, unknown>[])
    : [];
  const sources = [
    ...new Set(
      evidence
        .filter(
          (item) =>
            Array.isArray(patch.evidence_ids) &&
            patch.evidence_ids.includes(item.id),
        )
        .map((item) => item.source),
    ),
  ];
  return (
    <div className="result-body">
      {!!(patch.title || patch.summary || patch.text) && (
        <p className="result-text preserve">
          {words(patch.title || patch.summary || patch.text)}
        </p>
      )}
      {!!patch.text && !!(patch.title || patch.summary) && (
        <p className="result-text preserve">{words(patch.text)}</p>
      )}
      {sources.length > 0 && (
        <div className="result-sources">
          <span>{t("依据", "Based on")}</span>
          {sources.map((source) => (
            <span key={source} className="source-pill">
              {source}
            </span>
          ))}
        </div>
      )}
      {changes.length > 0 && (
        <ul className="plan-changes">
          {changes.map((change, index) => {
            const before = change.before as Record<string, unknown> | undefined;
            const after = change.after as Record<string, unknown> | undefined;
            return (
              <li key={String(change.task_id || index)}>
                {after?.status === "done" ? (
                  <Check size={12} />
                ) : after?.status === "blocked" ? (
                  <CircleHelp size={12} />
                ) : (
                  <ArrowRight size={12} />
                )}
                <span>
                  <strong>{words(change.title || after?.title)}</strong>
                  {!!after?.status && (
                    <small>
                      {before?.status
                        ? status(String(before.status)) + " → "
                        : ""}
                      {status(String(after.status))}
                    </small>
                  )}
                  {before?.priority !== after?.priority &&
                    after?.priority != null && (
                      <small>
                        {t("优先级", "Priority")}: {String(after.priority)}
                      </small>
                    )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {!!patch.next_action && (
        <p className="result-next">
          <strong>{t("下一步", "Next step")}: </strong>
          {words(patch.next_action)}
        </p>
      )}
      {!!patch.risk && (
        <p className="fine-print">
          {t("计划风险", "Plan risk")}: {words(patch.risk)}
        </p>
      )}
      {!changes.length &&
        !patch.title &&
        !patch.summary &&
        !patch.text &&
        !patch.next_action && (
          <p className="result-text preserve">{words(body)}</p>
        )}
    </div>
  );
}
function Modal({
  title,
  onClose,
  children,
  error,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  error?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      dialog?.close();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-header">
        <h2>{title}</h2>
        <button
          className="icon-btn"
          aria-label="Close / 关闭"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      {error && (
        <div className="banner danger" role="alert">
          {error}
        </div>
      )}
      {children}
    </dialog>
  );
}
