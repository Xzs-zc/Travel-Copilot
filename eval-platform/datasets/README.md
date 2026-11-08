# Dataset v2

`traveling_business_v2.jsonl` 由以下来源构成：

- v1 的 60 条业务回归场景，补齐真实城市对、明确交通模式期望和 rationale。
- 30 条交通长尾场景，覆盖短途铁路、中长途铁路、长途铁路加航班和仅航班。
- 35 条 Agent 契约场景，包含参数、工具结果、最终结果和多轮约束变化。
- 22 条 Supervisor 路由场景，覆盖多轮指代、故障、账户、偏好与交通解释。
- 26 条安全与提示注入场景，验证只读边界、禁止工具和敏感信息拒绝。
- 12 条来自 RAG Ablation 100 条真实检索报告的回归：`V2-KB-001` 至 `V2-KB-012`。
- 10 条已知工具/最终结果契约失败模式：`V2-AG-021` 至 `V2-AG-030`。
- 5 条构造轨迹 fixture：`V2-TJ-003`、`V2-TJ-008`、`V2-TJ-011`、`V2-TJ-012`、`V2-TJ-016`；它们不是真实 Agent Run。
- 18 条轨迹审计场景均使用构造 fixture，检查必需工具、禁止工具和最大步骤数。

所有 Case 必须通过 `datasets/schema.json`，按 `sha256(case_key) % 10` 确定性切分为 dev 与 holdout；开发阶段只使用 dev 调参，holdout 只报总分。

`online_support_e2e_v1.jsonl` 是独立的 20 条在线客服 E2E 数据集，来自 `evals/support/support_blind_v5.jsonl` 的 `block=e2e`，保留原始问题、页面上下文与期望；不参与 v2 dev/holdout 切分。
