---
doc: checklist
status: approved
---

# Qianyan Build Checklist

Build mode: fast — 用户明确批准整套方案并委托完成实现、测试与部署；沿用总方案中的顺序，无需再逐步审批实现细节。真实账户接入和用户反馈保持明确记录。

## Slices

- [ ] **1. 定制管家、交代目标并持久保存初始计划**
  Becomes usable: 可打开的网页、个人设置、隔离Demo、目标计划和记忆；服务重启数据保留。
  Why now: 第一条可体验行为同时证明核心记忆与目标模型，避免只有脚手架。
  PRD ref: `prd.md > F1 — Customize Your Butler`, F2, F3, F9
  Spec ref: `spec.md > Frontend`, `Database`, `Backend and Access Control`, `Nebius Token Factory and NVIDIA Nemotron`
  Build: 实现设置/目标/记忆 API 与网页，PostgreSQL迁移、初始计划/确认、真实Token Factory适配与标记Demo。
  Verify (mechanical): 构建、API隔离/持久化/记忆测试、浏览器完成初始旅程；真实模型需有效账户。
  Learner check: 打开网页，设定称呼、交代目标，确认计划，刷新查看。
  Commit: `Build personalized persistent goal workspace`

- [ ] **2. 网页关闭后继续跟进，按证据自主维护计划**
  Becomes usable: 独立worker持续处理任务，展示证据、下一步和计划差异，暂停/重启/撤销生效。
  Why now: 证明持续存在和主动推进的核心。
  PRD ref: F4, F5
  Spec ref: `spec.md > Agent Architecture`, `Worker and Durable Scheduling`
  Build: 持久jobs/lease、PlanPatch校验、完成标准、版本并发、风险和内部跟进。
  Verify (mechanical): 依赖/Done证据/并发/重复/暂停/重启测试及真实后台观察。
  Learner check: 关网页再打开，看下一步；修改或暂停目标观察效果。
  Commit: `Add durable proactive planning and evidence validation`

- [ ] **3. 接入真实GitHub/Vercel事实和企业微信双向通知**
  Becomes usable: 真事实变化自动更新计划，网页关闭后手机收到通知并可回复。
  Why now: 形成完整比赛闭环，验证外部接入风险。
  PRD ref: F6, F7, F8
  Spec ref: `spec.md > GitHub Adapter API Contract`, `Vercel Adapter API Contract`, `Notification Service and WeChat Adapter`
  Build: 只读适配、来源状态与版本、通知政策与outbox、回调验证和手机详情操作。
  Verify (mechanical): provider契约、旧SHA/Preview/失败而旧线上存活、微信加解密与去重测试；有效账户真联调。
  Learner check: 在真实样例项目更新README/部署，手机查看通知并回复。
  Commit: `Connect evidence sources and proactive WeCom contact`

- [ ] **4. 云端部署、公开体验和完整交付验证**
  Becomes usable: 可公开试用的隔离Demo、真实个人实例、英文文档、备份恢复与视频材料。
  Why now: 核心完成后稳定与展示，完成最终交付。
  PRD ref: F9, `States and Boundaries`
  Spec ref: `spec.md > Where It Runs and How Someone Tries It`, `Deployment Operations`, `Demo Architecture`, `Verification Approach`
  Build: Docker Compose/Caddy、迁移/备份、生产配置核验、英文README/许可、跨日记录、Demo录制。
  Verify (mechanical): PostgreSQL集成/浏览器端到端、云URL/手机/重启/备份恢复、公开repo与视频访问审计。
  Learner check: 打开公开链接走完整流程，反馈最终体验。
  Commit: `Prepare verified cloud deployment and public demo`

## Hands-on Checkpoints

- [ ] 第一条网页旅程可体验，已提供入口并收集实际反馈。
- [ ] 完整目标闭环可体验，实际反馈已处理。

## Final Review

- [ ] 所有P0逐项验收，有效外部账户与云部署证据齐全。
- [ ] 用户最后体验反馈已处理，确认可交付。

## Code Tour and App Map

- [ ] 实际代码的简明路线/使用说明完成。
- [ ] 可选学习/回顾按用户“做成项目”的偏好处理，不虚构学习记录。
- [ ] devpost/app-map.html 从真实代码生成并展示。

## Revisions

- 本机Node为24，正式运行采用兼容的Node24并锁依赖；保持React/Vite栈。
- Docker命令可用但初始daemon未启动，先尝试启动现有Docker Desktop，生产仍按PostgreSQL+容器架构。
- Docker Desktop底层启动失败。本地开发改用官方EDB PostgreSQL16.15 portable，运行在ASCII路径的用户目录；生产仍为Docker Compose，不替换数据库。
- 实现使用`backend/app/agent.py`和`worker.py`两个明确模块，避免为单Agent另建多层包；权限、证据和PlanPatch校验仍按spec执行。

## Verified Development Checkpoint — 2026-10-01

已实现的本地行为：个人设置、Owner/Demo隔离、目标草稿/人工任务/确认、记忆编辑和遗忘、独立Worker、Evidence回放、自动计划更新、暂停/恢复/完成、通知待办与只读连接器。无模型密钥时明确显示AI不可用，允许手动计划和确定性证据处理。

证据：

- `cd backend; uv run pytest -q`：98项通过，包括真实PostgreSQL空间隔离、CSRF/版本、遗忘并发与派生回复清除、任务完整编辑/依赖/草稿删除、用户优先级保护、用户确认与新证据冲突、撤销后的持久复核、普通commit不触发推理、四轮只读工具与逐次预算、部署日志权限不足时的阻塞通知、HTTPS探测的固定IP/TLS/私网拒绝边界。模型和外部平台使用MockTransport或显式fake，不计真实接入验收。
- `cd frontend; npm run build`：通过。
- `cd frontend; npm run verify:integration`：API与Worker重启后，真实HTTP/API/Worker五组闭环通过，含四种回放、设置/记忆、目标状态、导出、双cookie隔离；ready确认数据库与Worker在线。
- 真实`pg_dump`→独立恢复库：九张表的行数、结构、约束与Alembic版本一致；原库只读，未覆盖数据库。详见`infra/backup-notes.md`。
- 本地网页入口曾做桌面/手机布局检查；本轮浏览器连接不可用，完整可视交互验收仍待继续。HTTP验收不冒充浏览器验收。

尚未通过的门槛：真实Nemotron账户推理及自然对话联调、真实GitHub/Vercel账户观察、企业微信手机双向收发、生产容器运行、公网部署、真实跨日记录、公开仓库/视频及用户体验反馈。因此四个slice与最终验收仍未勾选，项目目标保持active。

代码地图已保存`devpost/app-map.html`，是参考路线；用户尚未实际参与代码导览，未标学习活动完成。

后续本地修正已通过128项测试：相对日期按消息时刻/个人时区形成待确认候选；模糊与非法时间澄清；撤销恢复原有跟进job；免打扰积压合并和四小时间隔；自选稍后时间及版本保护；相近重要变化一分钟合并；自定义管家名/风格影响通知。前端构建通过。真实GitHub接入、账户联调、生产部署与公开Demo/视频材料继续验收，保持既有P0范围。

真实GitHub验收已通过：公开仓库已发布，`730e895`对应的云端CI后端/前端成功；Owner API与独立Worker记录5条live GitHub证据，并按Repo/README/目录/同SHA指定CI标准完成4项客观任务。详见`infra/live-github-evidence.md`。随后修复了手动同步可能衍生多个周期观察链的问题，新增一项真实PostgreSQL回归通过；该修正尚待下一次云端CI验证。真实Nemotron、Vercel、企业微信、云资源、跨日与用户反馈等门槛仍未通过。
