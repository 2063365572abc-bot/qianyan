import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { readFile } from "node:fs/promises";

// Exercises the same authenticated contract used by the UI, in a disposable
// isolated demo. Requires the API, worker and Vite dev proxy to be running.
const base = process.env.QIANYAN_TEST_URL || "http://localhost:5173";
let cookie = "";
let csrf = "";
async function request(path, method = "GET", data) {
  const response = await fetch(base + "/api" + path, {
    method,
    headers: {
      Origin: base,
      "X-Qianyan-Mode": "demo",
      "X-CSRF-Token": csrf,
      ...(cookie ? { Cookie: cookie } : {}),
      ...(data !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: data !== undefined ? JSON.stringify(data) : undefined,
  });
  const setCookie = response.headers.getSetCookie?.();
  if (setCookie?.length)
    cookie = setCookie.map((value) => value.split(";")[0]).join("; ");
  const body = await response.json();
  assert.ok(
    response.ok,
    `${method} ${path}: ${response.status} ${JSON.stringify(body)}`,
  );
  return body;
}
const session = await request("/demo/session", "POST", {});
csrf = session.csrf_token;
const role = await request("/auth/session");
assert.equal(role.role, "demo");
let state = await request("/state");
assert.equal(state.space.role, "demo");
const spaceId = state.space.id;
async function runEvent(event) {
  const { run_id } = await request("/demo/events", "POST", { event });
  for (let n = 0; n < 100; n++) {
    await delay(500);
    const run = await request(`/runs/${run_id}`);
    if (["succeeded", "failed"].includes(run.status)) {
      assert.equal(run.status, "succeeded", `${event}: ${run.error}`);
      state = await request("/state");
      return state.goals[0];
    }
  }
  assert.fail(`Worker did not finish ${event}`);
}
let goal = await runEvent("incomplete_readme");
let readme = goal.tasks.find((task) => task.criteria.kind === "readme");
assert.notEqual(readme.status, "done");
const decision = state.notifications.find(
  (note) => note.category === "decision" && note.action_state === "open",
);
assert.ok(decision, "README decision notice should exist");
await request(`/notifications/${decision.id}/actions`, "POST", {
  action: "keep_open",
  version: goal.plan_version,
});
goal = await runEvent("readme_complete");
readme = goal.tasks.find((task) => task.criteria.kind === "readme");
assert.equal(readme.status, "done");
goal = await runEvent("deployment_failed");
assert.equal(
  goal.tasks.find((task) => task.criteria.kind === "deployment").status,
  "blocked",
);
const repair = goal.tasks.filter(
  (task) => task.title.includes("部署") || task.title.includes("deployment"),
);
assert.ok(repair.length >= 2, "Deployment failure should create a next action");
goal = await runEvent("deployment_recovered");
assert.equal(
  goal.tasks.find((task) => task.criteria.kind === "deployment").status,
  "done",
);
console.log("PASS: four replay transitions and README decision");

state = await request("/state");
await request("/butler", "PATCH", {
  settings: {
    ...state.space.settings,
    name: "Contract-test butler",
    style: "warm",
    preferences: ["Only actionable updates"],
    notification_start: "09:00",
    notification_end: "20:00",
  },
  version: state.space.settings_version,
});
state = await request("/state");
assert.equal(state.space.settings.name, "Contract-test butler");
const memory = await request("/memory", "POST", {
  content: "Disposable memory for UI contract verification",
});
assert.ok(memory.id);
await request(`/memory/${memory.id}`, "PATCH", {
  content: "Corrected disposable memory",
  version: memory.version,
});
state = await request("/state");
const corrected = state.memories.find(
  (item) => item.body.content === "Corrected disposable memory",
);
assert.ok(corrected);
await request(`/memory/${corrected.id}?version=${corrected.version}`, "DELETE");
state = await request("/state");
assert.ok(!state.memories.some((item) => item.id === corrected.id));
console.log("PASS: persisted settings and memory create/edit/forget");

const created = await request("/goals", "POST", {
  title: "Manual contract goal",
  intent: "Verify a manual plan without pretending the model ran",
  deadline: new Date(Date.now() + 86400000).toISOString(),
});
goal = await request(`/goals/${created.goal_id}`);
await request(`/goals/${goal.id}/tasks`, "POST", {
  version: goal.plan_version,
  title: "A manually maintained task",
  priority: 2,
  criteria: { kind: "user", description: "Explicit user judgment" },
  estimate_hours: 1,
  depends_on: [],
});
goal = await request(`/goals/${goal.id}`);
await request(`/goals/${goal.id}/confirm`, "POST", {
  plan_version: goal.plan_version,
});
goal = await request(`/goals/${goal.id}`);
assert.equal(goal.status, "active");
await request(`/goals/${goal.id}/pause`, "POST", {
  version: goal.plan_version,
});
goal = await request(`/goals/${goal.id}`);
assert.equal(goal.status, "paused");
await request(`/goals/${goal.id}/resume`, "POST", {
  version: goal.plan_version,
});
goal = await request(`/goals/${goal.id}`);
assert.equal(goal.status, "active");
await request(`/tasks/${goal.tasks[0].id}`, "PATCH", {
  version: goal.plan_version,
  status: "done",
});
goal = await request(`/goals/${goal.id}`);
assert.equal(goal.tasks[0].status, "done");
await request(`/goals/${goal.id}/complete`, "POST", {
  version: goal.plan_version,
});
goal = await request(`/goals/${goal.id}`);
assert.equal(goal.status, "done");
assert.ok(goal.evidence.some((item) => item.body.kind === "user_confirmation"));
const exported = await request("/export");
assert.ok(exported.goals.some((item) => item.id === goal.id));
console.log("PASS: manual task/confirm/pause/resume/complete and data export");

const previousCookie = cookie;
const previousCsrf = csrf;
const second = await request("/demo/session", "POST", {});
csrf = second.csrf_token;
const isolated = await request("/state");
assert.notEqual(isolated.space.id, spaceId);
assert.ok(!isolated.goals.some((item) => item.id === goal.id));
cookie = previousCookie;
csrf = previousCsrf;
await request("/demo/reset", "POST", {});
assert.equal((await request("/state")).goals.length, 1);
console.log("PASS: separate demo sessions and replay reset");
// The optional password is generated locally by this project's bootstrap. It
// stays in memory and is never printed or committed into this test file.
let access;
try {
  access = JSON.parse(
    await readFile(
      new URL("../../.local/access.json", import.meta.url),
      "utf8",
    ),
  );
} catch {}
if (access?.local_owner_password) {
  const login = await fetch(base + "/api/auth/login", {
    method: "POST",
    headers: { Origin: base, "Content-Type": "application/json" },
    body: JSON.stringify({ password: access.local_owner_password }),
  });
  assert.ok(login.ok, "Local owner login should succeed");
  const ownerSession = await login.json();
  const ownerCookie = login.headers
    .getSetCookie()
    .map((value) => value.split(";")[0])
    .join("; ");
  const sameBrowserCookie = cookie + "; " + ownerCookie;
  const ownerState = await (
    await fetch(base + "/api/auth/session", {
      headers: { Cookie: sameBrowserCookie },
    })
  ).json();
  assert.equal(ownerState.role, "owner");
  const demoState = await (
    await fetch(base + "/api/auth/session", {
      headers: { Cookie: sameBrowserCookie, "X-Qianyan-Mode": "demo" },
    })
  ).json();
  assert.equal(demoState.role, "demo");
  const out = await fetch(base + "/api/auth/logout", {
    method: "POST",
    headers: {
      Origin: base,
      Cookie: sameBrowserCookie,
      "X-CSRF-Token": ownerSession.csrf_token,
    },
  });
  assert.ok(out.ok);
  console.log("PASS: simultaneous owner/demo cookies select the intended role");
} else
  console.log(
    "SKIP: simultaneous owner/demo cookies (local owner access not provisioned)",
  );
console.log(
  "No claim of live NVIDIA inference or external notification delivery was made.",
);
