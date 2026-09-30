# Meta Muse 功能与对标场景调研（2026-09-30）

> 这是竞品事实清单，不是项目范围或技术方案。当前用户选择可定制管家底座优先；Qianyan 本次方案见 scope.md、prd.md、spec.md 与 development-plan.md。旧 DecisionPatch 规划已归档作废。

## 产品定位

Meta 把 Muse 定义为能代表用户执行多步任务的个人 Agent：具备长期记忆、个人目标、后台持续运行、网页与应用工具访问、主动建议和高风险动作审批。可通过 Muse App、网页、WhatsApp 交谈；有独立安全 VM、浏览器、文件系统和终端。来源：[发布说明](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/)、[功能页](https://ai.meta.com/muse/)、[设计说明](https://introducing.muse.ai/)、[安全架构](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)。

## 功能全景（公开资料可核实部分）

| 功能群 | 公开描述的具体能力 | 生活场景 |
|---|---|---|
| 对话与个性化 | 主聊天、侧聊天；命名/头像/风格；跨对话记忆，可查看、修改和遗忘 | 长期认识你的习惯、禁忌、关系与偏好 |
| 目标与主动性 | 把长期目标拆为计划；Goals 进度；定时/事件触发后台工作；Ideas 主动建议；按重要性通知 | 健身、家庭事务、计划推进、天气或价格监测 |
| 网页行动 | 独立浏览器搜索、填表、预约、客服、交易；用户可接管 | 办手续、预订、售后、退货 |
| 应用连接 | 邮件、日历、Instagram、云文件等 Connectors；权限可按读写范围控制 | 邮件筛选/草拟/发送、冲突改期、家庭日历、文件整理 |
| 购物与支付 | 商品搜索/比较、购物车、结账；关键操作前审批，一次性卡/受保护支付 | 杂货清单、礼物、价格跟踪、采购 |
| 产物与自建工具 | 文档、PDF、网页、仪表盘等 Artifacts；可写代码/自建所需工具 | 行程表、预算看板、学习材料 |
| 可见性与控制 | 活动日志、当前任务、审批卡、连接权限、记忆文件 | 知道它正在做什么、能阻止什么 |
| 多端与表达 | iOS、Android、网页、WhatsApp；官方生产力页还列 Mac App | 在日常沟通入口交代任务；跨设备跟进 |

上述能力来自[官方功能 FAQ](https://ai.meta.com/muse/)、[设计说明](https://introducing.muse.ai/)、[生产力场景](https://ai.meta.com/muse/productivity/)及[发布说明](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/)。具体连接器可能因地区和账户而异，应以实测为准。

## 对标场景

1. **生活行政事务**：收件箱筛出需行动邮件、提取截止日期、创建日历事件、准备回复并请求发送审批。[官方生产力页](https://ai.meta.com/muse/productivity/)
2. **家庭与活动协调**：读取学校邮件/网站、汇总开学事项、采购清单、预约聚餐，并在临近报名截止时提醒。[官方设计说明](https://introducing.muse.ai/)
3. **健康与日常目标**：制定训练计划，结合健康应用调整休息与训练，管理营养、食谱与食材采购。[官方健身页](https://ai.meta.com/muse/fitness/)
4. **购物与旅行**：比价、购物车、预订、付款审批、行程安排，状态变化后继续跟进。[官方介绍](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/)
5. **客服与手续**：通过浏览器填表、预约、处理账单或退款，关键步骤让用户确认。[官方生产力页](https://ai.meta.com/muse/productivity/)

## 当前能力与预告须分开

2026-09-30 可据官方公开材料确认的是上述已描述能力；[Connect 2026 公告](https://about.fb.com/news/2026/09/the-biggest-news-from-connect-2026/)中的 AI 眼镜集成、部分新增商家/支付/效率连接器、Muse 自有邮箱等属于将推出或扩展中的能力，不应当作所有账户已可用。发布说明中的 Confidential VM 也是计划推出。

## 对本次选题的含义

Muse 的整体产品需要大量连接器、长期后台任务、权限和安全基础设施，Solo 比赛周期不适合全量复制。按最新访谈，Qianyan 先建立可定制管家底座，通过一个长期目标闭环验证，再扩展生活场景。当前自主权限仅内部计划维护与向本人通知，外部高影响动作不进入首版。不能沿用旧 coding agent 草稿。
