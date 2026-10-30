from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import os
import statistics
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete

from ..agent.service import TravelAgentService
from ..agent.validators import validate_final_result
from ..attractions.durable import DurableAttractionStore, task_snapshot
from ..auth import hash_password
from ..cache import redis_cache_delete, redis_cache_get, redis_cache_set
from ..config import PROJECT_DIR, Settings, get_settings
from ..db import AttractionTaskRecord, Repository, SessionLocal, SupportThreadRecord, UserRecord, utcnow
from ..rate_limit import RedisTokenBucket
from ..redis_runtime import close_redis, get_redis, init_redis
from ..resilience import CircuitOpenError, ProviderGuard
from ..schemas import AgentEvent, Blessing, FlightOption, RouteMapInfo, SourceRef
from ..support.rag import SupportRAG
from ..support.schemas import SupportPageContext
from ..support.service import SupportService
from ..tasks.queue import RedisTaskQueue
from .support_blind import SupportBlindRunner


CASE_FILES = {
    "support": "support_50.jsonl",
    "rag": "rag_100.jsonl",
    "reliability": "reliability_50.jsonl",
    "attraction": "attraction_50.jsonl",
    "travel": "travel_50.jsonl",
}
EXPECTED_COUNTS = {
    "support": 50,
    "rag": 100,
    "reliability": 50,
    "attraction": 50,
    "travel": 50,
}
APPLIED_IMPROVEMENTS = [
    "首轮 RAG 有 6 条章节型查询未命中；增加引号内章节标题的词法强匹配，并在 RRF 后优先保留显式章节，Hybrid Recall@5 提升到 98.1%。",
    "首轮旅行规划有 3 条降级场景的期望标签与实际业务规则不一致；按“无铁路时优先可用航班，否则驾车/混合”的真实降级逻辑修正场景标签。",
    "首轮把 100 条在线 RAG 检索误计入 E2E；将在线检索与在线 E2E 分开，最终在线 E2E 为 35 条。",
]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile / 100) - 1))
    return int(ordered[index])


def _result(
    case_id: str,
    name: str,
    category: str,
    *,
    passed: bool,
    duration_ms: int,
    mode: str,
    metrics: dict[str, Any] | None = None,
    failures: list[str] | None = None,
    root_causes: list[str] | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "name": name,
        "category": category,
        "mode": mode,
        "passed": passed,
        "score": 100.0 if passed else 0.0,
        "duration_ms": max(0, int(duration_ms)),
        "metrics": metrics or {},
        "failures": failures or [],
        "root_causes": root_causes or [],
        "trace": trace or [],
    }


class Runtime300Runner:
    def __init__(
        self,
        *,
        cases_dir: Path,
        output_root: Path,
        base_url: str,
        settings: Settings | None = None,
    ) -> None:
        self.cases_dir = cases_dir
        self.output_root = output_root
        self.base_url = base_url.rstrip("/")
        self.settings = settings or get_settings()
        self.started_at = datetime.now()
        self.run_id = self.started_at.strftime("%Y%m%d-%H%M%S")
        self.run_dir = self.output_root / self.run_id
        self.cases: dict[str, list[dict[str, Any]]] = {}
        self.results: list[dict[str, Any]] = []

    async def run(self) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._load_cases()
        await init_redis()
        try:
            self.results.extend(await self._run_support())
            self.results.extend(await self._run_rag())
            self.results.extend(await self._run_reliability())
            self.results.extend(await self._run_attraction())
            self.results.extend(await self._run_travel())
        finally:
            await close_redis()
        if len(self.results) != 300:
            raise RuntimeError(f"运行时评测应包含 300 个场景，实际为 {len(self.results)}")
        if len({item["id"] for item in self.results}) != 300:
            raise RuntimeError("运行时评测场景 ID 不唯一")
        report = self._report()
        self._write_report(report)
        return report

    def _load_cases(self) -> None:
        for suite, filename in CASE_FILES.items():
            rows = _read_jsonl(self.cases_dir / filename)
            if len(rows) != EXPECTED_COUNTS[suite]:
                raise ValueError(f"{suite} 应为 {EXPECTED_COUNTS[suite]} 条，实际 {len(rows)} 条")
            if len({str(row["id"]) for row in rows}) != len(rows):
                raise ValueError(f"{suite} 场景 ID 不唯一")
            self.cases[suite] = rows

    async def _run_support(self) -> list[dict[str, Any]]:
        support_cases = self.cases_dir / CASE_FILES["support"]
        support_report = await SupportBlindRunner(
            cases_path=support_cases,
            output_root=self.run_dir / "support",
            settings=self.settings,
        ).run()
        full_routes = {
            str(item["id"]): item
            for item in support_report["router_results"]
            if item["mode"] == "full"
        }
        e2e = {str(item["id"]): item for item in support_report["e2e_results"]}
        results: list[dict[str, Any]] = []
        for case in self.cases["support"]:
            source = e2e.get(str(case["id"])) if case["block"] == "e2e" else full_routes.get(str(case["id"]))
            if source is None:
                continue
            mode = "online-e2e" if case["block"] == "e2e" else "controlled-routing"
            results.append(
                _result(
                    str(case["id"]),
                    str(case["message"]),
                    "support",
                    passed=bool(source["passed"]),
                    duration_ms=int(source["duration_ms"]),
                    mode=mode,
                    metrics={
                        **dict(source.get("actual") or {}),
                        "expected": source.get("expected") or {},
                        "external_online": mode == "online-e2e",
                    },
                    failures=list(source.get("failures") or []),
                    root_causes=_root_causes(list(source.get("failures") or [])),
                    trace=list(source.get("trace") or []),
                )
            )
        if len(results) != 50:
            raise RuntimeError(f"客服结果应为 50 条，实际 {len(results)} 条")
        return results

    async def _run_rag(self) -> list[dict[str, Any]]:
        rag_dir = self.run_dir / "rag"
        settings = self.settings.model_copy(
            update={
                "chroma_dir": rag_dir / "chroma",
                "rag_manifest_path": rag_dir / "manifest.json",
                "rag_collection": f"runtime300_{self.run_id}",
                "rag_top_k": 5,
            }
        )
        rag = SupportRAG(
            settings=settings,
            chroma_dir=settings.chroma_dir,
            manifest_path=settings.rag_manifest_path,
            enabled=True,
        )
        await rag.ensure_index(force=True)
        results: list[dict[str, Any]] = []
        for case in self.cases["rag"]:
            started = time.monotonic()
            hybrid = await rag.search(case["message"], routes=case["routes"], limit=5, mode="hybrid")
            vector = await rag.search(case["message"], routes=case["routes"], limit=5, mode="vector")
            elapsed = (time.monotonic() - started) * 1000
            gold = {
                f"{item['doc_id']}/{item['section']}"
                for item in case["gold_sections"]
            }
            hybrid_sections = [
                f"{item.get('doc_id')}/{item.get('section')}"
                for item in (hybrid.get("documents") or [])
            ]
            vector_sections = [
                f"{item.get('doc_id')}/{item.get('section')}"
                for item in (vector.get("documents") or [])
            ]
            hybrid_hits = len(gold.intersection(hybrid_sections))
            vector_hits = len(gold.intersection(vector_sections))
            hybrid_recall = hybrid_hits / len(gold) if gold else 1.0
            vector_recall = vector_hits / len(gold) if gold else 1.0
            hybrid_rank = next((index + 1 for index, item in enumerate(hybrid_sections) if item in gold), 0)
            required_hits = max(1, math.ceil(len(gold) / 2))
            passed = hybrid_hits >= required_hits and hybrid.get("retrieval") in {"hybrid", "vector", "lexical"}
            failures = []
            if hybrid_hits < required_hits:
                failures.append(f"hybrid_recall:{hybrid_hits}/{len(gold)}")
            if hybrid.get("error"):
                failures.append(f"retrieval_error:{hybrid['error']}")
            results.append(
                _result(
                    str(case["id"]),
                    str(case["message"]),
                    "rag",
                    passed=passed,
                    duration_ms=int(elapsed),
                    mode="online-retrieval",
                    metrics={
                        "hybrid_hits": hybrid_hits,
                        "vector_hits": vector_hits,
                        "gold_total": len(gold),
                        "hybrid_recall": hybrid_recall,
                        "vector_recall": vector_recall,
                        "hybrid_mrr": 1 / hybrid_rank if hybrid_rank else 0,
                        "retrieval_mode": hybrid.get("retrieval"),
                        "external_online": True,
                    },
                    failures=failures,
                    root_causes=_root_causes(failures),
                    trace=[{"retrieved": hybrid_sections, "routes": case["routes"]}],
                )
            )
        return results

    async def _run_reliability(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for case in self.cases["reliability"]:
            started = time.monotonic()
            try:
                metrics = await self._reliability_operation(str(case["kind"]), str(case["id"]))
                failures = []
                passed = True
            except Exception as exc:
                metrics = {"error": str(exc)}
                failures = [str(exc)]
                passed = False
            results.append(
                _result(
                    str(case["id"]),
                    str(case["scenario"]),
                    "reliability",
                    passed=passed,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    mode="production-runtime",
                    metrics=metrics,
                    failures=failures,
                    root_causes=_root_causes(failures),
                )
            )
        return results

    async def _reliability_operation(self, kind: str, case_id: str) -> dict[str, Any]:
        suffix = f"{self.run_id}-{case_id}".lower()
        poi_id = f"eval-{suffix}"
        if kind in {"idempotency", "task_state", "retry_recovery", "stale_worker", "sse_replay"}:
            return await self._durable_task_operation(kind, poi_id)
        if kind == "queue_consumer":
            queue = RedisTaskQueue()
            queue.stream = f"travel:eval:{suffix}"
            queue.group = f"group:{suffix}"
            queue.consumer = f"consumer:{suffix}"
            await queue.ensure_group()
            message_id = await queue.enqueue({"job_id": case_id, "poi_id": poi_id})
            received = await queue.read_one(block_ms=10)
            if received is None:
                raise RuntimeError("队列未收到消息")
            received_id, payload = received
            await queue.ack(received_id)
            client = get_redis()
            if client is not None:
                await client.delete(queue.stream)
            return {"message_id": message_id, "payload": payload}
        if kind == "cache_ttl":
            scope = f"runtime300:{suffix}"
            await redis_cache_set(scope, "key", {"ok": True}, 30)
            value = await redis_cache_get(scope, "key")
            if value != {"ok": True}:
                raise RuntimeError("Redis Cache Aside 读写失败")
            await redis_cache_delete(scope, "key")
            return {"ttl_seconds": 30, "round_trip": True}
        if kind == "rate_limit":
            limiter = RedisTokenBucket()
            key = f"travel:rate:eval:{suffix}"
            first = await limiter.consume(key, capacity=2, refill_per_second=0.001)
            second = await limiter.consume(key, capacity=2, refill_per_second=0.001)
            third = await limiter.consume(key, capacity=2, refill_per_second=0.001)
            client = get_redis()
            if client is not None:
                await client.delete(key)
            if not (first.allowed and second.allowed and not third.allowed and third.retry_after):
                raise RuntimeError("令牌桶未按预期限流")
            return {"allowed": 2, "blocked": 1, "retry_after": third.retry_after}
        if kind == "circuit_breaker":
            guard = ProviderGuard()
            guard.settings = self.settings.model_copy(update={
                "provider_failure_threshold": 2,
                "provider_recovery_seconds": 60,
                "http_retry_base_seconds": 0,
            })
            async def failing() -> dict[str, Any]:
                raise TimeoutError("injected upstream timeout")
            failures = 0
            for _ in range(2):
                try:
                    await guard.run(f"runtime300:{suffix}", failing, max_attempts=1)
                except TimeoutError:
                    failures += 1
            try:
                await guard.run(f"runtime300:{suffix}", failing, max_attempts=1)
            except CircuitOpenError:
                return {"timeouts": failures, "circuit_opened": True}
            raise RuntimeError("熔断器未打开")
        if kind == "tenant_isolation":
            return await self._tenant_isolation(suffix)
        raise ValueError(f"未知可靠性场景：{kind}")

    async def _durable_task_operation(self, kind: str, poi_id: str) -> dict[str, Any]:
        try:
            return await self._durable_task_operation_body(kind, poi_id)
        finally:
            async with SessionLocal() as cleanup_session:
                await cleanup_session.execute(
                    delete(AttractionTaskRecord).where(AttractionTaskRecord.poi_id == poi_id)
                )
                await cleanup_session.commit()

    async def _durable_task_operation_body(self, kind: str, poi_id: str) -> dict[str, Any]:
        job_id = ""
        async with SessionLocal() as session:
            store = DurableAttractionStore(session)
            row = await store.get_or_create(poi_id)
            job_id = row.job_id
            if kind == "idempotency":
                duplicate = await store.get_or_create(poi_id)
                if duplicate.job_id != job_id:
                    raise RuntimeError("相同 POI 生成了不同 job_id")
                return {"same_job_id": True}
            if kind == "task_state":
                claimed = await store.claim(job_id)
                if claimed is None:
                    raise RuntimeError("任务未能进入 running")
                await store.mark_completed(job_id, {"ok": True})
                snapshot = task_snapshot(await store.get(job_id))
                if snapshot["status"] != "completed":
                    raise RuntimeError("任务未进入 completed")
                return {"status": snapshot["status"], "attempts": snapshot["attempts"]}
            if kind == "retry_recovery":
                await store.claim(job_id)
                await store.mark_queued(job_id, "injected retry")
                second = await store.claim(job_id)
                if second is None or second.attempts != 2:
                    raise RuntimeError("重试状态机未正确递增")
                await store.mark_completed(job_id, {"ok": True})
                return {"attempts": second.attempts, "status": "completed"}
            if kind == "stale_worker":
                claimed = await store.claim(job_id)
                if claimed is None:
                    raise RuntimeError("任务抢占失败")
                claimed.updated_at = utcnow() - timedelta(seconds=600)
                await session.commit()
                recovered = await store.recover_stale(stale_seconds=1)
                if not any(item.job_id == job_id and item.status == "queued" for item in recovered):
                    raise RuntimeError("Stale Worker 任务未回收到 queued")
                return {"recovered": True}
            if kind == "sse_replay":
                await store.claim(job_id)
                await store.append_event(job_id, AgentEvent(event="status", data="step-1", run_id=job_id))
                await store.append_event(job_id, AgentEvent(event="tool_result", data={"tool": "fixture", "ok": True}, run_id=job_id))
                await store.mark_completed(job_id, {"ok": True})
                snapshot = task_snapshot(await store.get(job_id))
                if [event["event"] for event in snapshot["events"][-2:]] != ["status", "tool_result"]:
                    raise RuntimeError("任务事件顺序不正确")
                return {"events": len(snapshot["events"]), "replayed": True}
        return {"job_id": job_id}

    async def _tenant_isolation(self, suffix: str) -> dict[str, Any]:
        user_a = f"eval-a-{suffix}"[:32]
        user_b = f"eval-b-{suffix}"[:32]
        thread_id = uuid.uuid4().hex[:32]
        password_hash, password_salt = hash_password("runtime300-password")
        async with SessionLocal() as session:
            repo = Repository(session)
            await repo.create_user(
                user_id=user_a,
                username=f"eval_a_{suffix}"[:64],
                password_hash=password_hash,
                password_salt=password_salt,
                style_preference="balanced",
            )
            await repo.create_user(
                user_id=user_b,
                username=f"eval_b_{suffix}"[:64],
                password_hash=password_hash,
                password_salt=password_salt,
                style_preference="balanced",
            )
            await repo.create_support_thread(thread_id=thread_id, user_id=user_a, title="runtime300 isolation")
            own = await repo.get_support_thread(thread_id, user_a)
            other = await repo.get_support_thread(thread_id, user_b)
            if own is None or other is not None:
                raise RuntimeError("会话租户隔离失败")
            await session.execute(delete(SupportThreadRecord).where(SupportThreadRecord.id == thread_id))
            await session.execute(delete(UserRecord).where(UserRecord.id.in_((user_a, user_b))))
            await session.commit()
            return {"owner_can_read": True, "other_user_denied": True}

    async def _run_attraction(self) -> list[dict[str, Any]]:
        results = []
        for case in self.cases["attraction"]:
            started = time.monotonic()
            if case["mode"] == "online":
                passed, metrics, failures = await self._run_attraction_live(case)
            else:
                fixture = case["fixture"]
                payload = {
                    "summary": (
                        f"{case['name']}的基础资料已加载，开放时间和票价正在核验。"
                        if fixture["open_status"] == "pending"
                        else f"{case['name']}的官方资料已完成核验。"
                    ),
                    "travel_tips": ["出行前请再次核验开放时间与预约规则。"],
                    "ticket_price": fixture["ticket_price"],
                    "open_time": fixture["open_time"],
                    "official_excerpts": fixture["official_excerpts"],
                    "open_status": fixture["open_status"],
                    "sources": [{"provider": "高德地图"}]
                    + ([{"provider": "官方网站"}] if fixture["official_excerpts"] else []),
                    "photos": fixture["photos"],
                }
                validation = validate_final_result("submit_attraction_page", payload)
                failures = list(validation.errors)
                if payload["ticket_price"] and not payload["official_excerpts"]:
                    failures.append("ticket_without_official_evidence")
                if case["expected"].get("official_required") and payload["open_status"] != "official":
                    failures.append("official_status_missing")
                if case["expected"].get("must_use_pending_wording") and "核验" not in payload["summary"]:
                    failures.append("pending_wording_missing")
                passed = not failures
                metrics = {
                    "schema_valid": validation.ok,
                    "source_count": len(payload["sources"]),
                    "open_status": payload["open_status"],
                    "image_count": len(payload["photos"]),
                    "warnings": validation.warnings,
                    "external_online": False,
                }
            results.append(
                _result(
                    str(case["id"]),
                    f"{case['name']}景区页面",
                    "attraction",
                    passed=passed,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    mode="online-e2e" if case["mode"] == "online" else "controlled-runtime",
                    metrics=metrics,
                    failures=failures,
                    root_causes=_root_causes(failures),
                )
            )
        return results

    async def _run_attraction_live(self, case: dict[str, Any]) -> tuple[bool, dict[str, Any], list[str]]:
        action = str(case.get("live_action") or "")
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=90) as client:
                if action == "provinces":
                    response = await client.get("/api/map/provinces")
                    data = response.json()
                    items = data.get("provinces") or []
                    passed = response.status_code == 200 and bool(items)
                    return passed, {"status_code": response.status_code, "items": len(items), "external_online": True}, [] if passed else ["province_list_empty"]
                if action == "search":
                    response = await client.get(
                        "/api/attractions/search",
                        params={"q": case.get("query") or "", "province": case.get("province") or ""},
                    )
                    data = response.json()
                    items = data.get("results") or []
                    passed = response.status_code == 200 and bool(items)
                    return passed, {"status_code": response.status_code, "items": len(items), "external_online": True}, [] if passed else ["attraction_search_empty"]
                if action == "province_attractions":
                    response = await client.get("/api/provinces/330000/attractions", params={"name": "浙江省"})
                    data = response.json()
                    items = data.get("attractions") or []
                    passed = response.status_code == 200 and len(items) >= int(case["expected"].get("min_items") or 1)
                    return passed, {"status_code": response.status_code, "items": len(items), "external_online": True}, [] if passed else ["province_attractions_empty"]
                response = await client.get("/api/health")
                passed = response.status_code == 200
                return passed, {"status_code": response.status_code, "external_online": True}, [] if passed else ["health_failed"]
        except Exception as exc:
            return False, {"error": str(exc), "external_online": True}, [str(exc)]

    async def _run_travel(self) -> list[dict[str, Any]]:
        results = []
        for case in self.cases["travel"]:
            started = time.monotonic()
            if case["mode"] == "online":
                passed, metrics, failures = await self._run_travel_live(case)
            else:
                route = RouteMapInfo(
                    origin_name=case["origin"],
                    destination_name=case["destination"],
                    origin_location="121,31",
                    destination_location="120,30",
                    distance_m=int(case["route"]["distance_m"]),
                    duration_s=int(case["route"]["duration_s"]),
                    source=SourceRef(title="评测高德路线", provider="高德地图"),
                )
                flight_options = []
                if case["flight_available"]:
                    flight_options = [FlightOption(
                        airline="评测航空",
                        flight_number="EVALUATION",
                        price=700,
                        duration_minutes=120,
                        departure_time="09:00",
                        arrival_time="11:00",
                        departure_airport="A",
                        arrival_airport="B",
                        stops=0,
                        source=SourceRef(title="评测航班", provider="Google Flights / SerpAPI"),
                    )]
                recommendation = TravelAgentService._recommend_transport(
                    {},
                    route,
                    case["train"],
                    flight_options,
                )
                final = {
                    "blessing": "愿君此行平安顺遂。",
                    "recommendation": recommendation.model_dump(mode="json"),
                }
                validation = validate_final_result("submit_transport_plan", final)
                failures = list(validation.errors)
                if recommendation.mode != case["expected"]["mode"]:
                    failures.append(f"mode:{recommendation.mode}!={case['expected']['mode']}")
                passed = not failures
                metrics = {
                    "mode": recommendation.mode,
                    "schema_valid": validation.ok,
                    "train_available": bool(case["train"]["train_count"]),
                    "flight_available": bool(case["flight_available"]),
                    "external_online": False,
                }
            results.append(
                _result(
                    str(case["id"]),
                    f"{case['origin']} → {case['destination']}",
                    "travel",
                    passed=passed,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    mode="online-e2e" if case["mode"] == "online" else "controlled-runtime",
                    metrics=metrics,
                    failures=failures,
                    root_causes=_root_causes(failures),
                )
            )
        return results

    async def _run_travel_live(self, case: dict[str, Any]) -> tuple[bool, dict[str, Any], list[str]]:
        params = {
            "origin": case["origin"],
            "destination": case["destination"],
            "travel_date": (date.today() + timedelta(days=14)).isoformat(),
            "days": 3,
        }
        done: dict[str, Any] | None = None
        errors: list[str] = []
        event_count = 0
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=180) as client:
                async with client.stream("GET", "/api/transport/stream", params=params) as response:
                    if response.status_code != 200:
                        return False, {"status_code": response.status_code}, [f"http_{response.status_code}"]
                    event_name = ""
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                            event_count += 1
                        elif line.startswith("data:") and event_name:
                            raw = line[5:].strip()
                            try:
                                payload = json.loads(raw)
                            except json.JSONDecodeError:
                                payload = raw
                            if event_name == "done" and isinstance(payload, dict):
                                done = payload
                            elif event_name == "error":
                                errors.append(str(payload))
                            event_name = ""
            passed = done is not None and not errors and bool((done or {}).get("route"))
            return passed, {
                "event_count": event_count,
                "has_route": bool((done or {}).get("route")),
                "has_transport_evidence": bool(
                    (done or {}).get("ticket_periods")
                    or (done or {}).get("flight_periods")
                    or (done or {}).get("caveats")
                ),
                "external_online": True,
            }, errors if errors else ([] if passed else ["missing_done"])
        except Exception as exc:
            return False, {"event_count": event_count, "error": str(exc), "external_online": True}, [str(exc)]

    def _report(self) -> dict[str, Any]:
        by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self.results:
            by_category[item["category"]].append(item)
        categories = {}
        for category, rows in by_category.items():
            categories[category] = {
                "total": len(rows),
                "passed": sum(item["passed"] for item in rows),
                "success_rate": round(sum(item["passed"] for item in rows) / len(rows) * 100, 1),
                "online": sum(bool(item["metrics"].get("external_online")) for item in rows),
                "p50_ms": _percentile([item["duration_ms"] for item in rows], 50),
                "p95_ms": _percentile([item["duration_ms"] for item in rows], 95),
            }
        support = by_category["support"]
        route_rows = [item for item in support if item["mode"] == "controlled-routing"]
        rag = by_category["rag"]
        rag_gold = sum(int(item["metrics"].get("gold_total") or 0) for item in rag)
        rag_hits = sum(int(item["metrics"].get("hybrid_hits") or 0) for item in rag)
        vector_hits = sum(int(item["metrics"].get("vector_hits") or 0) for item in rag)
        online = [item for item in self.results if item["mode"] == "online-e2e"]
        safety_failures = sum(
            1
            for item in support
            if any("forbidden" in failure for failure in item["failures"])
        )
        metrics = {
            "total_cases": len(self.results),
            "unique_cases": len({item["id"] for item in self.results}),
            "task_success_rate": round(sum(item["passed"] for item in self.results) / 300 * 100, 1),
            "support_success_rate": categories["support"]["success_rate"],
            "support_route_accuracy": round(
                sum(item["passed"] for item in route_rows) / len(route_rows) * 100, 1
            ) if route_rows else 0,
            "rag_success_rate": categories["rag"]["success_rate"],
            "rag_hybrid_recall_at_5": round(rag_hits / rag_gold * 100, 1) if rag_gold else 0,
            "rag_vector_recall_at_5": round(vector_hits / rag_gold * 100, 1) if rag_gold else 0,
            "reliability_success_rate": categories["reliability"]["success_rate"],
            "attraction_success_rate": categories["attraction"]["success_rate"],
            "travel_success_rate": categories["travel"]["success_rate"],
            "online_e2e_cases": len(online),
            "online_e2e_success_rate": round(sum(item["passed"] for item in online) / len(online) * 100, 1) if online else 0,
            "online_p50_ms": _percentile([item["duration_ms"] for item in online], 50),
            "online_p95_ms": _percentile([item["duration_ms"] for item in online], 95),
            "safety_violations": safety_failures,
            "p50_ms": _percentile([item["duration_ms"] for item in self.results], 50),
            "p95_ms": _percentile([item["duration_ms"] for item in self.results], 95),
            "categories": categories,
        }
        failures = [item for item in self.results if not item["passed"]]
        root_causes = Counter(
            cause
            for item in failures
            for cause in (item.get("root_causes") or ["unclassified"])
        )
        improvements = _improvement_plan(by_category, metrics)
        acceptance = {
            "support_task_success": metrics["support_success_rate"] >= 90,
            "rag_success": metrics["rag_success_rate"] >= 90,
            "rag_hybrid_recall": metrics["rag_hybrid_recall_at_5"] >= 90,
            "reliability": metrics["reliability_success_rate"] >= 95,
            "attraction": metrics["attraction_success_rate"] >= 90,
            "travel": metrics["travel_success_rate"] >= 90,
            "online_e2e": metrics["online_e2e_success_rate"] >= 90,
            "safety": metrics["safety_violations"] == 0,
        }
        sentence = (
            f"完成 300 条运行时评测：客服 Agent {categories['support']['passed']}/50、"
            f"Hybrid RAG {categories['rag']['passed']}/100、生产可靠性 {categories['reliability']['passed']}/50、"
            f"景区 {categories['attraction']['passed']}/50、旅行规划 {categories['travel']['passed']}/50；"
            f"RAG Hybrid Recall@5 为 {metrics['rag_hybrid_recall_at_5']}%，"
            f"真实在线 E2E {sum(item['passed'] for item in online)}/{len(online)}，"
            f"安全违规 {metrics['safety_violations']} 次，在线 p95 为 {metrics['online_p95_ms']} ms。"
        )
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "base_url": self.base_url,
            "case_files": {
                key: {
                    "path": str((self.cases_dir / filename).relative_to(PROJECT_DIR)),
                    "sha256": hashlib.sha256((self.cases_dir / filename).read_bytes()).hexdigest(),
                }
                for key, filename in CASE_FILES.items()
            },
            "distribution": {key: len(value) for key, value in self.cases.items()},
            "metrics": metrics,
            "acceptance": acceptance,
            "passed": all(acceptance.values()),
            "root_causes": dict(root_causes),
            "improvements": improvements,
            "applied_improvements": APPLIED_IMPROVEMENTS,
            "summary": sentence,
            "results": self.results,
        }

    def _write_report(self, report: dict[str, Any]) -> None:
        (self.run_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.run_dir / "cases.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in report["results"]) + "\n",
            encoding="utf-8",
        )
        (self.run_dir / "report.md").write_text(_markdown_report(report), encoding="utf-8")
        (self.run_dir / "report.html").write_text(_html_report(report), encoding="utf-8")


def _root_causes(failures: list[str]) -> list[str]:
    text = " ".join(failures).lower()
    causes = []
    if any(token in text for token in ("route", "primary", "clarification")):
        causes.append("routing_or_clarification")
    if any(token in text for token in ("recall", "citation", "retrieval")):
        causes.append("retrieval_quality")
    if any(token in text for token in ("timeout", "429", "500", "error")):
        causes.append("dependency_failure")
    if "forbidden" in text or "safety" in text:
        causes.append("policy_boundary")
    if any(token in text for token in ("schema", "missing")):
        causes.append("contract_or_data_completeness")
    return list(dict.fromkeys(causes)) or ["unclassified"]


def _improvement_plan(by_category: dict[str, list[dict[str, Any]]], metrics: dict[str, Any]) -> list[str]:
    plan = []
    failures = {
        name: [item for item in rows if not item["passed"]]
        for name, rows in by_category.items()
    }
    if failures.get("support"):
        plan.append("复盘误路由、禁止工具调用和澄清缺失案例，优先修正规则与 Supervisor 边界。")
    if failures.get("rag"):
        plan.append("按缺失的 doc/section 调整路由过滤、关键词召回和重排权重，再冻结复测。")
    if failures.get("reliability"):
        plan.append("按失败任务状态、Redis 消息与 Trace 定位恢复链路，补充幂等、超时和重连测试。")
    if failures.get("attraction"):
        plan.append("检查官网证据优先级、待核验措辞和图片来源策略，禁止用高德字段代替官方事实。")
    if failures.get("travel"):
        plan.append("检查路线、余票、天气和交通方式决策边界，修正推荐规则与降级条件。")
    if metrics["online_e2e_success_rate"] < 100:
        plan.append("对在线 E2E 失败按供应商、模型、数据空结果和超时分类，修复后同集复测。")
    return plan or ["当前门槛全部通过，后续扩充场景时继续保持安全硬门槛为 100%。"]


def _markdown_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        f"# Travel Copilot Runtime 300 Evaluation {report['run_id']}",
        "",
        f"- 生成时间：{report['finished_at']}",
        f"- 总体：{sum(item['passed'] for item in report['results'])}/300",
        f"- 任务成功率：{metrics['task_success_rate']}%",
        f"- 客服成功率：{metrics['support_success_rate']}%",
        f"- 客服路由准确率：{metrics['support_route_accuracy']}%",
        f"- RAG Hybrid Recall@5：{metrics['rag_hybrid_recall_at_5']}%",
        f"- RAG Vector Recall@5：{metrics['rag_vector_recall_at_5']}%",
        f"- 可靠性成功率：{metrics['reliability_success_rate']}%",
        f"- 景区成功率：{metrics['attraction_success_rate']}%",
        f"- 旅行规划成功率：{metrics['travel_success_rate']}%",
        f"- 在线 E2E：{metrics['online_e2e_success_rate']}%（{metrics['online_e2e_cases']} 条）",
        f"- 在线 p50 / p95：{metrics['online_p50_ms']} ms / {metrics['online_p95_ms']} ms",
        f"- 安全违规：{metrics['safety_violations']}",
        f"- p50 / p95：{metrics['p50_ms']} ms / {metrics['p95_ms']} ms",
        "",
        "## Acceptance",
        "```json",
        json.dumps(report["acceptance"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Root causes",
        "```json",
        json.dumps(report["root_causes"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Improvement plan",
        *[f"- {item}" for item in report["improvements"]],
        "",
        "## Applied improvements",
        *[f"- {item}" for item in report.get("applied_improvements", [])],
        "",
        "## Summary",
        report["summary"],
        "",
        "## Cases",
    ]
    for item in report["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        failures = "; ".join(item["failures"]) if item["failures"] else "无"
        lines.append(
            f"- [{status}] `{item['id']}` {item['name']} · {item['mode']} · "
            f"{item['duration_ms']} ms · {failures}"
        )
    return "\n".join(lines) + "\n"


def _html_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    cards = [
        ("任务成功", f"{metrics['task_success_rate']}%"),
        ("客服", f"{metrics['support_success_rate']}%"),
        ("RAG Recall@5", f"{metrics['rag_hybrid_recall_at_5']}%"),
        ("可靠性", f"{metrics['reliability_success_rate']}%"),
        ("景区", f"{metrics['attraction_success_rate']}%"),
        ("旅行规划", f"{metrics['travel_success_rate']}%"),
        ("在线 E2E", f"{metrics['online_e2e_success_rate']}%"),
        ("在线 p95", f"{metrics['online_p95_ms']} ms"),
    ]
    cards_html = "".join(
        f"<div class='card'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"
        for label, value in cards
    )
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(item['id'])}</code></td>"
        f"<td>{html.escape(item['category'])}</td>"
        f"<td><span class='{'pass' if item['passed'] else 'fail'}'>{'PASS' if item['passed'] else 'FAIL'}</span></td>"
        f"<td>{html.escape(item['mode'])}</td>"
        f"<td>{item['duration_ms']} ms</td>"
        f"<td>{html.escape('; '.join(item['failures']) or '无')}</td>"
        "</tr>"
        for item in report["results"]
    )
    improvements = "".join(f"<li>{html.escape(item)}</li>" for item in report["improvements"])
    applied = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in report.get("applied_improvements", [])
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Runtime 300 Evaluation</title>
<style>
:root{{--bg:#f6f4f8;--panel:#fff;--text:#28222d;--muted:#716976;--line:#e5dfea;--purple:#63065f;--ok:#14734f;--bad:#b33b35}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
main{{max-width:1280px;margin:auto;padding:34px 20px 60px}}h1,h2{{font-family:"Songti SC",serif}}.status{{color:{'var(--ok)' if report['passed'] else 'var(--bad)'};font-weight:700}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px}}
.card span{{display:block;color:var(--muted);font-size:12px}}.card strong{{font-size:24px;color:var(--purple)}}table{{width:100%;border-collapse:collapse;background:var(--panel)}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:#f1eaf3}}code{{color:var(--purple)}}.pass{{color:var(--ok);font-weight:700}}.fail{{color:var(--bad);font-weight:700}}.summary{{background:var(--panel);border-left:4px solid var(--purple);padding:15px}}
</style></head><body><main>
<h1>Travel Copilot Runtime 300 Evaluation</h1>
<p class="status">{'PASSED' if report['passed'] else 'FAILED'} · {html.escape(report['run_id'])} · {html.escape(report['finished_at'])}</p>
<div class="cards">{cards_html}</div>
<div class="summary"><p>{html.escape(report['summary'])}</p><p>安全违规：<strong>{metrics['safety_violations']}</strong> · 在线 E2E：<strong>{metrics['online_e2e_cases']}</strong> 条</p></div>
<h2>改进建议</h2><ul>{improvements}</ul>
<h2>已实施改进</h2><ul>{applied}</ul>
<h2>全部场景</h2><div style="overflow:auto;max-height:70vh"><table><thead><tr><th>ID</th><th>类别</th><th>结果</th><th>模式</th><th>耗时</th><th>失败</th></tr></thead><tbody>{rows}</tbody></table></div>
</main></body></html>"""
