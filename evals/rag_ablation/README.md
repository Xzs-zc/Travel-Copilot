# RAG Retrieval Ablation

该数据集用于比较纯向量检索与 Hybrid RRF 检索，不参与 Runtime 300 的全链路通过率。

## 数据规模

- 共 100 条问题。
- Medium 30 条，单章节证据，使用语义改写和场景化表达。
- Hard 70 条，每题至少包含两个 gold section，覆盖跨章节、条件组合、来源冲突和故障排查。
- 知识库为 `backend/app/support/knowledge/*.md` 当前版本。

## 指标

- `Recall@15`：Top-15 粗召回命中 gold section 数除以该问题的 gold section 总数。
- `MRR@5`：`1 / 第一条相关结果排名`，Top-5 未命中时为 0。
- `HitRate@5`：Top-5 至少命中一个 gold section 的问题占比。

所有问题使用相同 routes、Top-15 粗召回、每文档最多 3 个章节和 Chroma 索引。纯向量、Hybrid RRF 与 Hybrid RRF + Rerank 只切换检索和排序链路。

## 复现

```bash
python3 scripts/generate_rag_ablation_cases.py
python scripts/evaluate_rag_ablation.py
```

评测脚本会强制使用当前知识库重建索引，并将 JSON、Markdown、HTML 和逐题 Trace 写入：

```text
data/evaluations/rag_ablation/<run_id>/
```
