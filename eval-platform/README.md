# Traveling Eval Studio

面向 Traveling 旅行规划 Agent 的独立业务评测与回归平台。系统把数据集、可切换系统配置、真实 Trace、导入评测、Judge 校准、统计检验和发布门禁放在同一套 PostgreSQL 数据模型中。

## 5 分钟启动

```bash
cd eval-platform
./start.sh
```

访问：

- 平台：`http://127.0.0.1:8770`
- API：`http://127.0.0.1:8770/docs`
- 主站：`http://127.0.0.1:8765`

首次启动会迁移 PostgreSQL、加载 v1/v2 数据集并初始化三套可切换系统配置。

## 页面

- Datasets：数据集、版本、Case、版本 Diff、导入证据。
- Runs：选择系统配置、trials 并创建 Run。
- Run Detail：指标、分类健康、失败聚类、归因复核、Flaky Case。
- Trace Viewer：真实开始时间、父子 Span、输入输出、Token、成本与模型。
- Compare：通过率差、配对 bootstrap 区间、McNemar 检验、场景级回归。
- Labeling：隐藏 Judge 分数的独立评分与 JSONL 导出。
- Trends：同一数据集与系统配置的历史质量趋势，支持表格替代视图。

## 数据集

`datasets/traveling_business_v2.jsonl` 共 **215** 条：

| 类别 | 数量 |
|---|---:|
| transport_policy | 50 |
| agent_contract | 45 |
| support_routing | 30 |
| knowledge_retrieval | 30 |
| safety_policy | 30 |
| trajectory | 18 |
| attraction_evidence | 12 |

来源分布：`adapted=124`、`adversarial=46`、`derived_from_failure=27`、`hand=18`。

确定性切分：`dev=173`、`holdout=42`，holdout 比例 `19.5%`。校验：

```bash
docker compose exec eval-platform .venv/bin/python -m platform_backend.cli \
  validate-dataset datasets/traveling_business_v2.jsonl
```

## 系统配置

- `baseline-no-rag`：关闭知识检索，其余规则不变。
- `baseline-basic-validators`：只做基础结构校验。
- `candidate-current`：当前严格校验、安全边界和混合检索。

基线差异来自真实执行代码，不是 `runner.py` 中的结果分支。

## Trace 与统计

- Span 耗时来自 `time.perf_counter()`，父子关系由 `contextvars` 维护。
- 导入报告缺少逐事件时保时时标记 `unavailable`，不伪造时间线。
- `trials` 支持 1–10；`pass@k`、`pass^k`、bootstrap CI 和 McNemar 在 `core/stats.py`。
- 确定性任务只标记“方差 0”，不展示伪置信区间。

## 真实门禁

当前 v2 dev 门禁报告：[`reports/gate-report.md`](reports/gate-report.md)

- Candidate Run：`f4fbe94ac3a842b8`
- Baseline Run：`a3251b5640c84816`
- 通过率：`97.1%`
- 关键失败：`0`
- 相对基线回归：`0`
- 门禁结果：`PASS`

执行：

```bash
docker compose exec eval-platform .venv/bin/python scripts/run_once.py
```

## 导入的真实评测

平台已导入 7 个 Run：

- RAG ablation：vector、hybrid、hybrid_rerank，各 100 条。
- RAG Quality：hybrid_rerank，100 条。
- Runtime300：300 条。
- Support Blind v5：245 条。
- Online Support E2E：20 题 × 3 trials。

导入使用 SHA-256 幂等；重复执行全部显示 `skipped=true`。

```bash
docker compose exec eval-platform .venv/bin/python -m platform_backend.cli import --all
```

## RAG 质量

- 数据集：`evals/rag_ablation/rag_100.jsonl`
- 检索：候选池 15，`hybrid_rerank`，最终 Top 5。
- Recall@15 `97.3%`、Recall@5 `86.2%`、MRR@5 `95.2%`、nDCG@5 `85.5%`。
- Faithfulness `97.1`、Citation Accuracy `97.9`、Answer Relevance `95.7`。
- Judge Coverage `100%`，Errors `0`。
- 详情见 [`reports/rag_quality_reconciliation.md`](reports/rag_quality_reconciliation.md)。

## 在线 E2E

- 数据集：Support Blind v5 的 20 条 E2E。
- 正式执行：20 × 3 = 60 次，Errors 0。
- 通过率、pass@3、pass^3 均为 `100%`。
- P50 / P95：`2560 / 5172 ms`，延迟口径为 `consumer_arrival`。
- 实际模型消耗：`¥0.148288`。
- 详情见 [`reports/online_run_summary.md`](reports/online_run_summary.md)。

## Judge 与标注

- 4 个 YAML rubric：忠实度、引用准确率、答案相关性、路由正确性。
- Judge 输出严格 JSON，失败重试一次，仍失败记 `judge_error`，不按 0 分处理。
- 新版标注文件：`labels/rag_gold_v2.jsonl`，共 150 条（50 Case × 3 维）。
- 标注来源是 `codex_review`，不是人工金标。
- Judge 模型：`mimo-v2.6-flash`。
- 校准：50/50 有效样本/维度，异常 0。
- 加权 κ：忠实度 `0.086379`、引用准确率 `0.039501`、答案相关性 `0.061033`。
- Cohen κ：`0.086379`、`-0.029201`、`-0.050725`。
- 一致性偏低，当前 Judge 只作为参考，不进入门禁。
- 完整混淆矩阵与偏差方向见 [`docs/judge_calibration.md`](docs/judge_calibration.md)。

## 测试

```bash
docker compose --profile test run --rm eval-platform-test

cd frontend
npm ci
npm test
npm run build
```

当前结果：

- 后端：21 passed
- 前端：5 passed
- TypeScript + Vite build：通过

## 文档

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/metrics.md`](docs/metrics.md)
- [`docs/failure_taxonomy.md`](docs/failure_taxonomy.md)
- [`docs/limitations.md`](docs/limitations.md)
- [`docs/resume.md`](docs/resume.md)
- [`docs/PLAN_DEVIATIONS.md`](docs/PLAN_DEVIATIONS.md)
