export type Settings = {
  name: string;
  style: "concise" | "warm" | "detailed";
  preferences: string[];
  timezone: string;
  notification_start: string;
  notification_end: string;
  proactive: boolean;
  capacity_hours_per_day: number | null;
};
export type Task = {
  id: string;
  title: string;
  status: string;
  priority: number;
  depends_on: string[];
  criteria: unknown;
  estimate_hours: number | null;
  followup_at: string | null;
};
export type Evidence = {
  id: string;
  body: unknown;
  source: string;
  source_id: string;
  version: string;
  observed_at: string;
  source_updated_at: string | null;
};
export type Goal = {
  id: string;
  title: string;
  intent: string;
  deadline: string | null;
  status: string;
  plan_version: number;
  tasks: Task[];
  evidence: Evidence[];
  next_action: unknown;
  risk: unknown;
  source_bindings: Record<string, unknown>;
  source_status?: Record<string, unknown>;
};
export type Memory = {
  id: string;
  body: unknown;
  version: string;
  source: string;
  created_at: string;
};
export type Notification = {
  id: string;
  goal_id: string | null;
  category: string;
  body: unknown;
  send_state: string;
  due_at: string;
  action_state: string;
  version?: number;
};
export type Run = {
  id: string;
  goal_id: string;
  status: string;
  trigger: string;
  patch: unknown;
  error: unknown;
  created_at: string;
};
export type State = {
  space: {
    id: string;
    role: string;
    settings: Settings;
    settings_version: number;
  };
  goals: Goal[];
  memories: Memory[];
  messages: { id: string; goal_id: string | null; body: unknown; source: string; created_at: string }[];
  notifications: Notification[];
  runs: Run[];
  health: Record<string, unknown>;
  integrations: Record<string, unknown>;
};
