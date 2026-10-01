# Qianyan / 千言

## Overview

A persistent personal AI butler: tell it a goal once, then let it maintain the plan, remember context, follow up and explain changes with evidence. Qianyan updates its own tasks; it does not edit your repository, deploy code, pay, submit forms or email other people.

千言是持续在云端工作的个人管家：交代一次目标，维护计划、记忆与跟进。核心路径是个人设置 → 交代目标 → 确认计划 → 后台跟进 → 回复后调整 → 下次仍记得。GitHub/Vercel 是可选的只读进度来源。

## Current delivery stage

As requested on 2026-10-01, this stage completes the platform's interactions using **Alibaba Cloud Model Studio / qwen-plus**. Notifications remain in the platform inbox. WeCom, further live deployment-provider accounts and public hosting are deferred. Alibaba inference is not evidence of compliance with the Nebius/NVIDIA hackathon requirements; the explicit Nebius/Nemotron provider remains supported for that later stage.

The localhost API, independent worker and PostgreSQL 16.15 are running. Real Alibaba function calling, draft plan generation, personal-memory responses and browser interactions have been exercised. GitHub evidence has also been observed from the actual published repository. Current details and remaining user acceptance are in [delivery evidence](infra/platform-acceptance.md).

[Cloud CI at 55dcbe2](https://github.com/2063365572abc-bot/qianyan/actions/runs/36808370660) passed backend tests, frontend build and the actual Docker Compose stack, including HTTP journeys through Caddy. Docker Desktop remains unavailable on the development machine; native PostgreSQL provides local persistence. A real snapshot was restored into an isolated database: [backup evidence](infra/backup-notes.md).

Fixture evidence is clearly labelled **replay**. Vercel account access, WeCom phone delivery, public HTTPS hosting and real Nemotron access have not been verified. A development HTTP container run does not establish public production hosting.

当前先交付可正常交互的平台：阿里云真实模型、个人化设置、持久记忆、目标计划、后台任务、进展证据和平台内收件箱。微信及进一步的外部部署资源接入暂缓。真实 GitHub 与回放来源分别标记；用户体验反馈待用户实际操作后记录。

## Stack

- React, TypeScript, Vite frontend; Python 3.12 FastAPI API and independent worker.
- PostgreSQL 16 for settings, memory, goals, evidence, runs, persistent jobs and notification outbox.
- Explicit model-provider selection: Nebius Token Factory / NVIDIA Nemotron, or Alibaba Cloud Model Studio / Qwen; bounded requests and validated plan changes, with no silent fallback.
- Read-only GitHub/Vercel adapters and separate WeCom notification adapter.
- Caddy serves the built frontend and proxies API/callbacks on the same origin. No GPU required.

## Installation

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 24, Docker with Compose 2.24.4+ and a working Linux engine.

```sh
python scripts/bootstrap-local.py
```

This creates development credentials and saves your local sign-in password in ignored `.local/access.json` without printing it. Alternatively copy `.env.example` to `.env`, fill `POSTGRES_PASSWORD` with a random URL-safe password, set the matching localhost `DATABASE_URL`, and provide `SESSION_SECRET` and `OWNER_PASSWORD_HASH`. For manual hashing, use `app.auth.hash_password` through the backend Python environment and quote the resulting hash in `.env` so Compose preserves its dollar characters. Never place a plaintext owner password in `.env`. For Alibaba inference set `AI_PROVIDER=aliyun`, `ALIYUN_API_KEY`, `ALIYUN_MODEL_ID=qwen-plus` and the correct regional `ALIYUN_BASE_URL`; `AI_TOOL_MODE=proposal` forces a structured proposal from the supplied bounded context. For the hackathon provider set `AI_PROVIDER=nebius`, `NEBIUS_API_KEY` and an account-verified NVIDIA `NEBIUS_MODEL_ID`. `NOTIFICATION_CHANNEL=in_app` requires no WeChat account; select `wecom` only after configuring that integration. No key should be embedded in frontend variables or source.

```sh
docker compose --env-file .env -f infra/compose.yaml -f infra/compose.dev.yaml up -d postgres
cd backend
uv sync --frozen
uv run python -m app.migrate
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
cd backend
uv run python -m app.worker
```

And a third:

```sh
cd frontend
npm ci
npm run dev
```

The Vite development server proxies `/api` and `/hooks/wecom` to localhost:8000. For all-container local testing:

If Docker Desktop is unavailable on Windows, keep the same PostgreSQL stack: download the [official EDB PostgreSQL 16 binary archive](https://www.enterprisedb.com/download-postgresql-binaries), extract its `pgsql/` directory into `%LOCALAPPDATA%/Qianyan/postgres16/`, then run `./scripts/start-local-postgres.ps1`. PostgreSQL on Windows needs an ASCII-only runtime/data path here; initialization under the Chinese workspace name failed during bootstrap. The script binds only localhost, uses the `.env` password, preserves cluster data and installs no Windows service. Stop it with `./scripts/stop-local-postgres.ps1`. Both scripts accept `-RuntimeDirectory` for another explicit ASCII-only path. Do not run Docker PostgreSQL and portable PostgreSQL on the same port simultaneously.

```sh
docker compose --env-file .env -f infra/compose.yaml -f infra/compose.dev.yaml up --build -d
```

Open `http://localhost:8080`. PostgreSQL/API ports in this override are bound to `127.0.0.1`; the production file does not publish them. `:8080` intentionally uses local HTTP. A WeCom callback requires actual public HTTPS and cannot call localhost.

## Production deployment

Use one Linux CPU VM with fixed public outbound IP, initially about 2 vCPU / 4 GiB RAM. These are planning estimates, not purchased infrastructure. A real domain must point to the server; ports 80 and 443 must be reachable for Caddy HTTPS. Keep SSH restricted and do not publish PostgreSQL.

1. Install Docker Engine and Compose; copy this project onto the VM.
2. Create a protected `.env`. Set `APP_ENV=production`, `APP_DOMAIN=your-real-domain`, `APP_PUBLIC_URL=https://your-real-domain`, strong random database/session secrets, owner password hash, and Nebius credentials. Do not use example values. The Compose file routes database connections to the internal PostgreSQL service.
3. Verify `GET https://api.tokenfactory.nebius.com/v1/models` using your key and select the exact available NVIDIA Nemotron model ID. Do not infer account availability from the example default.
4. Run the configuration preflight. It never prints values:

```sh
python3 scripts/preflight.py --require-live-wecom
docker compose --env-file .env -f infra/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose.yaml up --build -d
```

If WeCom is not configured yet, omit `--require-live-wecom`; the inbox can work, but real proactive WeChat delivery remains unverified. Database readiness precedes migration; successful migration precedes API and worker. API liveness precedes Caddy startup. Use `/api/health/ready` to check database/worker readiness after startup, and inspect `docker compose logs api worker` without exposing secret environment values.

5. Register a WeCom self-built app, bind only the intended recipient, add the server's fixed outbound IP to its trusted IP list, then configure the callback `https://your-real-domain/hooks/wecom`. Validate actual phone send/reply and opening the authenticated detail page. Ordinary personal WeChat compatibility is not promised.
6. Configure only your selected GitHub repository and Vercel project with read permissions. Validate content/CI SHA matching, production vs preview, stale sources and failed deployment with an existing healthy production site.
7. Close the browser, wait for a due follow-up, restart API/worker and verify persistent job recovery. Test at least one real cross-day goal. Use anonymous `/demo` for judges; demo spaces must never send real WeCom messages or access owner data.
8. Back up and rehearse restoration using [backup notes](infra/backup-notes.md). Keep the publicly accessible demo alive for the contest judging period, subject to the final contest dates.

正式环境：域名与服务器需用户实际账户；模型额度、可信出口 IP、企业微信成员可见范围、手机外链都要真实核验。默认基础架构不包含高可用、自动异地备份或成本保障。

## Usage

Open `http://localhost:5173` after starting the API, worker and frontend. Choose the isolated demo to replay README and deployment events, or sign in using the generated local password in ignored `.local/access.json` to maintain your own goals.

With a real model provider configured, delegate a goal such as “Help me finish this project by the end of the month.” Review the absolute deadline, task criteria and dependencies before confirming the draft. Without a model key, add manual tasks; the UI explicitly reports that inference is unavailable. Connect only your selected repository/project to observe actual provider facts.

The worker continues while the webpage is closed. Real phone delivery requires the configured official WeCom application. Replay notifications remain inside the demo and never contact your real account.

## Verification commands

Backend database tests must target a separate database named `qianyan_test` (create it on the development PostgreSQL server first). Set `DATABASE_URL` to the same development connection with the database name changed to `qianyan_test` before running pytest. Never point test cleanup at an owner/production database. CI creates its own PostgreSQL service and test database.

```sh
cd backend
uv run pytest
cd ../frontend
npm run build
```

Do not treat these commands alone as production proof. Live model requests, provider facts, notification delivery, cloud availability and recovery need separate evidence.

## Privacy and permissions

Owner and anonymous demo sessions use separate spaces. Personal settings and memory live in PostgreSQL, rather than browser storage. Required task context is sent to the selected model provider for inference; Qianyan does not claim that all user data stays exclusively on its VM. Secrets are server-only. Memory correction/forgetting and JSON export are part of the product baseline.

## License

[MIT](LICENSE).
