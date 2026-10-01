# Platform acceptance — 2026-10-01

## Current scope

The user supplied an Alibaba model credential and requested complete platform interactions first, with WeChat and other external-resource work deferred. Runtime now explicitly selects Alibaba Cloud Model Studio (`qwen-plus`, Beijing compatible API), with `AI_TOOL_MODE=proposal` and `NOTIFICATION_CHANNEL=in_app`. No automatic provider fallback occurs. This does not prove Nebius/NVIDIA hackathon compliance, a live Vercel account, WeCom delivery, or public HTTPS deployment.

## Real model evidence

- Account `/models` returned HTTP 200 and included `qwen-plus`; no credential or full catalog was printed.
- Actual forced function calling returned a valid boolean check (176 tokens reported by the provider).
- Through the product's independent worker, a three-task project presentation plan was generated in one model request after tightening completion-rule schemas. The browser confirmed the draft and showed the dependency-linked tasks.
- A real reply used a two-hour constraint to suggest the first task without fabricating a deadline or completion. Returning to a different project restored its explicitly stored two-hour preference in the model answer.
- Model requests were recorded with actual model ID/usage and reserved under the existing daily budget. A provider prose response was corrected via bounded retry. Proposal mode subsequently forces the structured response function from the already retrieved context.
- A subsequent actual owner-space reply succeeded in one request with an eight-task plan and live GitHub facts. In a fresh demo, a single chat instruction captured a goal with the exact proposed 2026-10-31T23:59:59+08:00 deadline; initial planning succeeded in one request, then the browser confirmed the plan. No user acceptance was inferred from that confirmation.
- Earlier real integration failures were retained honestly: transport-envelope token overcount and an invalid initial proposal. The schema compactor was also corrected to preserve the real title property, and proposal mode advertises only its required response function to avoid duplicated schema overhead. These issues were corrected; failed jobs were not labelled successful. A versioned, deduplicated retry-plan control was added.
- A longer owner conversation exposed a full-request estimate of 8,133 tokens despite the earlier fixed context cap. Context now fits the actual system instructions, function schemas and round history within the request budget, retaining authoritative task/evidence state and the latest human message. The same real owner question subsequently received a valid plain-language reply, preserving pending user acceptance. A regression verifies the full-request overhead and unchanged validation snapshot.

## Browser interactions exercised

- Enter isolated demo; create a new goal; review three generated tasks; confirm plan.
- Natural explicit `请记住...` creates a sourced memory; model uses it on return to the original goal.
- Change butler name/style/daily capacity via settings and save.
- `以后只在下午提醒我` persists the contact window as 12:00–18:00 Asia/Shanghai; the assumption is stated in the reply.
- Replay incomplete README: remains needs_review with missing requirements; keep-open action works in the inbox.
- Replay complete README: deterministic evidence makes the structure task done.
- Replay failed production build: deployment is blocked, a priority-one build-log action appears, later tasks retain dependencies.
- Replay deployment recovery: corresponding blocker clears and the next action moves to video.
- Edit a test memory: new content visible, obsolete derived explanation lineage removed. Edit/forget concurrency and exported data are additionally covered by PostgreSQL and HTTP tests.
- The new-goal deadline candidate was shown as an absolute date/timezone; confirmation enabled continued follow-up without marking subjective tasks done.
- Explicit personal-space tab is available even when goals exist, for global memories/new-goal conversation. Conversations show their source goal.
- Edit the actual goal intent/current scope with a versioned save, preserving the unset deadline and existing task evidence.
- A 390-pixel mobile view of the newly delegated goal exposed grid-column overflow; after correcting the grid children, document scroll width was 375px within a 390px viewport. The viewport was restored afterward.
- Login to the actual owner workspace with the generated ignored-file credential; current plan reflects the user's Alibaba/platform scope. No password was printed.

Replay cases are product-behavior evidence, not live Vercel acceptance. Only GitHub has separate real source evidence: [GitHub acceptance](live-github-evidence.md).

## Mechanical verification

- Backend: 138 PostgreSQL-backed/unit/contract tests passed; one upstream Starlette test-client deprecation warning.
- Frontend: TypeScript + Vite production build passed.
- Real local HTTP + API + independent worker: five journey groups passed after provider integration, including four replay transitions, settings/memory edit/forget, task states, export and owner/demo cookie isolation.
- Prior published revision `55dcbe2`: [cloud CI](https://github.com/2063365572abc-bot/qianyan/actions/runs/36808370660) passed 129 backend tests, frontend build, and an actual Compose stack with Caddy HTTP journeys. The newest provider/UI changes are verified locally and will be published separately.
- Native PostgreSQL backup/restore remains independently verified: [backup notes](backup-notes.md).

## User access and remaining acceptance

Open `http://localhost:5173`. Private-workspace password is in the ignored `.local/access.json`; never publish that file. The public-demo entrance creates an isolated replay workspace with real AI budget limits. The database and independent worker must remain running for continued background work on this machine.

The user has not yet supplied hands-on acceptance feedback. There is no claim of real next-day use, public hosting, external phone delivery or all arbitrary-language personalization commands. Clear remember commands and settings are supported; ambiguous requests ask for clarification or direct users to the settings controls.
