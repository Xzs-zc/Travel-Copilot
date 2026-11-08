# Online Support E2E 实测报告

- Run：`20261108-052650`
- 平台导入 Run：`imp-470be89f8f28e3b3`
- 数据：`support_blind_v5` 的 20 条 E2E
- 正式执行：20 题 × 3 trials = 60 次
- 正式运行次数：1 次
- 执行入口：`scripts/run_online_support_e2e.py`（复用盲测临时 PostgreSQL schema 与测试用户）
- 平台记录：真实报告导入 PostgreSQL；报告中的每次 trial 均可追踪到平台 Run

## 结果

| 指标 | 结果 |
|---|---:|
| 有效执行 | 60 / 60 |
| Errors | 0 |
| Error Rate | 0.0% |
| 通过率 | 100.0% |
| 通过率 95% CI（Wilson） | 83.9%–100.0% |
| pass@3 | 100.0% |
| pass^3 | 100.0% |
| Flaky Cases | 0 |
| P50 | 2560 ms |
| P95 | 5172 ms |
| Token 总量 | 120505 |

延迟口径为 `consumer_arrival`，不包含主项目内部逐步骤耗时。20/20 的 Wilson 95% CI 下限为 83.9%，样本很小，不作为“系统长期稳定”的结论。

在线门禁结果：`PASS`。`error_rate=0.0 <= 0.05`、`pass_hat_k=1.0 >= 0.85`、`P95=5172ms <= 15000ms`；成本规则因 `cost_usd_per_case_max=null` 为 `skip`。

## 实际模型消耗

DeepSeek Flash：

- 输入：111244 tokens，全部未命中缓存
- 输出：9261 tokens
- 空闲时段单价：输入未命中 ¥1/M，输出 ¥4/M
- 成本：111244 × ¥1/M + 9261 × ¥4/M = **¥0.148288**

SiliconFlow：

- BAAI/bge-m3 Embedding：官方价格页显示免费
- 本在线 Support E2E 未启用 Reranker

MiMo Judge：

- 本轮未启用 Judge，成本 ¥0

**本次正式在线评测总模型消耗：¥0.148288。**

用户已取消 `EVAL_MAX_COST_USD` 与 `EVAL_MAX_TOTAL_TOKENS` 限制，因此本次没有预算中止，也没有触发 `budget_stopped`；上表是实际消耗而非预算值。

平台原生 `support_e2e` 执行、error 分母剔除、pass@k/pass^k、Flaky、Trace 子 span 与在线门禁已实现并有桩测试；为避免违反“正式运行只跑一次”，没有再次发起付费模型调用。

## 价格来源

- DeepSeek：<https://api-docs.deepseek.com/zh-cn/quick_start/pricing>
- MiMo：<https://mimo.mi.com/static/docs/price/pay-as-you-go.md>
- SiliconFlow：<https://www.siliconflow.cn/pricing>

价格快照同时记录在 `configs/pricing.yaml`。
