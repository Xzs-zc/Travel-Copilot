# RAG Quality · Hybrid Rerank 实测报告

- 正式 Run：`20261108-044827`
- 平台导入 Run：`imp-d25917d3153a5772`
- 数据集：`evals/rag_ablation/rag_100.jsonl`
- Cases：100
- Retrieval mode：`hybrid_rerank`
- Candidate pool：15
- Final top-k：5
- Answer model：`deepseek-flash`
- Judge model：`mimo-v2.6-flash`
- Embedding model：`BAAI/bge-m3`
- Rerank model：`BAAI/bge-reranker-v2-m3`
- 正式运行次数：1 次

## 实测结果

| 指标 | 结果 |
|---|---:|
| Recall@15 | **97.3%** |
| Recall@5 | **86.2%** |
| MRR@5 | **95.2%** |
| nDCG@5 | **85.5%** |
| Faithfulness | 97.1 |
| Citation Accuracy | 97.9 |
| Answer Relevance | 95.7 |
| Judge Coverage | 100% |
| Quality Pass | 59 / 100 |
| Errors | 0 |
| P50 | 15177 ms |
| P95 | 29759 ms |

## 与上一版 Hybrid 对比

上一版质量评测未启用重排：

- Run：`20261108-040509`
- Recall@5：85.0%
- 平台重算 MRR@5：91.2%
- 平台重算 nDCG@5：82.7%
- Faithfulness：96.4
- Citation Accuracy：97.0
- Answer Relevance：94.2

切换到 `hybrid_rerank` 后：

- Recall@5：85.0% → 86.2%
- MRR@5：91.2% → 95.2%
- nDCG@5：82.7% → 85.5%
- Faithfulness：96.4 → 97.1
- Citation Accuracy：97.0 → 97.9
- Answer Relevance：94.2 → 95.7

Recall@15 为 97.3%，说明主要损失发生在重排后的最终 Top 5，而不是候选池没有召回金标。

## 口径

- 本报告只记录实测结果，不新增发布门禁。
- 原有逐 Case quality rule 的结果为 59/100，本报告不把它作为发布门禁。
- 所有结果来自同一份正式报告，未重跑择优。
- 100 条结果的 `retrieval_modes` 全部为 `hybrid+rerank`。
