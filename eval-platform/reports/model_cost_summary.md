# 模型调用消耗汇总

统计日期：2026-11-08  
币种：人民币 CNY

## 官方单价

| 模型 | 输入缓存命中 | 输入缓存未命中 | 输出 |
|---|---:|---:|---:|
| DeepSeek `deepseek-flash` | ¥0.02/M | ¥1.00/M | ¥4.00/M |
| MiMo `mimo-v2.6-flash` | ¥0.02/M | ¥1.00/M | ¥2.00/M |
| SiliconFlow `BAAI/bge-m3` | 免费 | 免费 | 免费 |
| SiliconFlow `BAAI/bge-reranker-v2-m3` | 免费 | 免费 | 免费 |

DeepSeek 价格来自官方文档；2026-11-08 为周日，全部按空闲时段计费。MiMo 和 SiliconFlow 价格来自各自官方按量计费页。

## RAG Quality 100 条

- 首次运行：`20261108-040509`（`hybrid`，未启用 rerank）
- 首次运行 DeepSeek：输入 92799 tokens（全部未命中），输出 93750 tokens
- 首次运行 MiMo Judge：输入 133477 tokens（全部未命中），输出 40734 tokens
- 首次运行小计：`¥0.682744`
- Run：`20261108-044827`
- DeepSeek：输入 92906 tokens（缓存命中 18816，未命中 74090），输出 87930 tokens
- DeepSeek 成本：`¥0.426186`
- MiMo Judge：输入 133178 tokens（全部未命中），输出 37678 tokens
- MiMo 成本：`¥0.208534`
- Embedding / Rerank：`¥0`
- 第二次运行小计：`¥0.634720`
- 两次 RAG Quality 合计：`¥1.317464`

## Judge v2 校准

- 150 次 Judge：输入 430135 tokens（缓存命中 3328，未命中 426807），输出 46332 tokens
- 小计：`¥0.519555`

## Online Support E2E

- 20 题 × 3 trials
- DeepSeek：输入 111244 tokens（全部未命中），输出 9261 tokens
- Embedding：免费
- MiMo Judge：未启用
- 小计：`¥0.148288`

## 本计划实际模型消耗

| 项目 | 消耗 |
|---|---:|
| RAG Quality 首次 `040509` | ¥0.682744 |
| RAG Quality 正式 `044827` | ¥0.634720 |
| Judge v2 校准 | ¥0.519555 |
| Online Support E2E | ¥0.148288 |
| **合计** | **¥1.985307** |

历史 117 次 Judge v1 校准不在本计划合计内；如把该历史批次也算入，累计为 `¥2.219014`。
