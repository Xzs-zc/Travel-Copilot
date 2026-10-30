# Runtime 300 Evaluation

本目录包含五个冻结场景集，共 300 个唯一场景：

- `support_50.jsonl`：客服 Agent 路由、上下文、安全与多轮交互。
- `rag_100.jsonl`：六个知识域、章节命中、多证据和同义查询。
- `reliability_50.jsonl`：PostgreSQL、Redis、Worker、SSE、缓存、限流、熔断与租户隔离。
- `attraction_50.jsonl`：景区事实、官网状态、缺数据降级与在线接口。
- `travel_50.jsonl`：交通方式决策、降级、重规划和在线 E2E。

生成清单：

```bash
uv run python scripts/generate_runtime300_assets.py
```

执行完整评测：

```bash
docker compose exec -T api .venv/bin/python scripts/evaluate_runtime300.py \
  --base-url http://127.0.0.1:8765
```

执行模式：

- `online-e2e`：真实模型、真实外部工具或真实 HTTP/SSE 链路。
- `online-retrieval`：真实 SiliconFlow Embedding 与 Chroma 检索。
- `production-runtime`：连接 PostgreSQL 与 Redis 的生产组件。
- `controlled-runtime`：冻结输入下的真实业务规则和 Schema 校验。
- `controlled-routing`：规则、模型与路由判定。

报告的通过门槛：

- 客服、RAG、景区、旅行规划成功率达到 90%。
- Hybrid RAG Recall@5 达到 90%。
- 生产可靠性达到 95%。
- 在线 E2E 达到 90%。
- 安全违规为 0。

报告输出到 `data/evaluations/runtime300/<run_id>/`，包含 JSON、Markdown、HTML 和逐场景 JSONL。
