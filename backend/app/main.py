from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from .agent.service import TravelAgentService
from .auth import (
    AUTH_COOKIE_NAME,
    current_user,
    hash_password,
    issue_session_token,
    optional_user,
    session_cookie_kwargs,
    user_profile,
    verify_password,
)
from .agent.graph.checkpoints import close_graph_checkpointer, init_graph_checkpointer, read_graph_checkpoint
from .attractions.jobs import (
    configure_attraction_jobs,
    prepare_attraction,
    shutdown_attraction_jobs,
    stream_attraction_events,
)
from .attractions.durable import (
    DurableAttractionStore,
    prepare_durable_attraction,
    stream_durable_attraction_events,
    task_snapshot,
)
from .config import PROJECT_DIR, get_settings
from .db import Repository, UserRecord, get_session, init_db
from .errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from .mcp.client import McpToolClient
from .observability import finish_trace, flush, score_trace, start_trace, trace_value, update
from .providers.amap import ConfigurationError, ProviderError
from .schemas import AgentEvent, LoginPayload, PreferencePayload, ProfileUpdatePayload, RegisterPayload
from .support.schemas import SupportPageContext
from .support.service import SupportService
from .support.skill_registry import skill_registry
from .support.rag import rag_index
from .rate_limit import RequestGuardMiddleware
from .redis_runtime import close_redis, get_redis, init_redis, redis_health
from .tasks.queue import task_queue

settings = get_settings()
mcp_client = McpToolClient()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    await init_graph_checkpointer()
    await init_redis()
    if get_redis() is not None:
        await task_queue.ensure_group()
    await mcp_client.start()
    configure_attraction_jobs(lambda session: TravelAgentService(session, mcp_client=mcp_client))
    app.state.mcp_client = mcp_client
    try:
        yield
    finally:
        await shutdown_attraction_jobs()
        await mcp_client.stop()
        await close_graph_checkpointer()
        await close_redis()
        flush()


app = FastAPI(title="Travel Copilot Map Agent", version="0.4.0", lifespan=lifespan)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_middleware(RequestGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8765"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def service_dependency(session: Annotated[AsyncSession, Depends(get_session)]) -> TravelAgentService:
    return TravelAgentService(session, mcp_client=mcp_client)


def support_dependency(session: Annotated[AsyncSession, Depends(get_session)]) -> SupportService:
    return SupportService(session, mcp_client=mcp_client)


def sse_payload(event: AgentEvent) -> dict[str, str]:
    return {"event": event.event, "data": json.dumps(event.data, ensure_ascii=False, default=str)}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    redis = await redis_health()
    database_backend = settings.database_url.split(":", 1)[0]
    return {
        "ok": True,
        "missing_required": settings.missing_required(),
        "pid": os.getpid(),
        "variant": settings.ablation_variant,
        "agent_runtime": "langgraph",
        "database_backend": database_backend,
        "redis": redis,
        "durable_tasks": get_redis() is not None,
        "media_dir": str(settings.media_dir),
    }


@app.get("/api/config/frontend")
async def frontend_config() -> dict[str, Any]:
    return {
        "amap_js_key": settings.amap_js_key,
        "amap_js_security_code": settings.amap_js_security_code,
        "configured": not settings.missing_required(),
        "missing_required": settings.missing_required(),
    }


@app.post("/api/auth/register")
async def register(
    payload: RegisterPayload,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    configured_invite = settings.registration_invite_code.strip()
    if not configured_invite:
        raise HTTPException(status_code=403, detail="当前未开放注册")
    if payload.invite_code.strip() != configured_invite:
        raise HTTPException(status_code=403, detail="邀请码不正确")
    username = payload.username.strip()
    password_hash, password_salt = hash_password(payload.password)
    repo = Repository(session)
    try:
        user = await repo.create_user(
            user_id=uuid.uuid4().hex[:16],
            username=username,
            password_hash=password_hash,
            password_salt=password_salt,
            style_preference=payload.style_preference,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    token, token_hash, expires_at = issue_session_token()
    await repo.create_auth_session(token_hash=token_hash, user_id=user.id, expires_at=expires_at)
    response.set_cookie(**session_cookie_kwargs(expires_at), value=token)
    return {"user": user_profile(user).model_dump(mode="json")}


@app.post("/api/auth/login")
async def login(
    payload: LoginPayload,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    repo = Repository(session)
    user = await repo.get_user_by_username(payload.username.strip())
    if user is None or not verify_password(payload.password, user.password_hash, user.password_salt):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, token_hash, expires_at = issue_session_token()
    await repo.create_auth_session(token_hash=token_hash, user_id=user.id, expires_at=expires_at)
    response.set_cookie(**session_cookie_kwargs(expires_at), value=token)
    return {"user": user_profile(user).model_dump(mode="json")}


@app.post("/api/auth/logout")
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    import hashlib

    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        await Repository(session).delete_auth_session(hashlib.sha256(token.encode("utf-8")).hexdigest())
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user: Annotated[UserRecord | None, Depends(optional_user)]) -> dict[str, Any]:
    return {"user": user_profile(user).model_dump(mode="json") if user else None}


@app.get("/api/support/threads")
async def support_threads(
    user: Annotated[UserRecord, Depends(current_user)],
    service: Annotated[SupportService, Depends(support_dependency)],
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    return {"threads": await service.list_threads(user, limit=limit)}


@app.get("/api/support/skills")
async def support_skills(
    user: Annotated[UserRecord, Depends(current_user)],
) -> dict[str, Any]:
    return skill_registry.describe()


@app.post("/api/support/skills/reload")
async def reload_support_skills(
    user: Annotated[UserRecord, Depends(current_user)],
) -> dict[str, Any]:
    return skill_registry.reload()


@app.get("/api/support/knowledge/status")
async def support_knowledge_status(
    user: Annotated[UserRecord, Depends(current_user)],
) -> dict[str, Any]:
    return rag_index.status()


@app.post("/api/support/knowledge/reload")
async def reload_support_knowledge(
    user: Annotated[UserRecord, Depends(current_user)],
) -> dict[str, Any]:
    return await rag_index.reload()


@app.get("/api/support/threads/{thread_id}")
async def support_thread_messages(
    thread_id: str,
    user: Annotated[UserRecord, Depends(current_user)],
    service: Annotated[SupportService, Depends(support_dependency)],
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    items = await service.thread_messages(user, thread_id, limit=limit)
    if items is None:
        raise HTTPException(status_code=404, detail="客服会话不存在")
    return {"thread_id": thread_id, "messages": items}


@app.get("/api/support/stream")
async def support_stream(
    message: str,
    context: str,
    user: Annotated[UserRecord, Depends(current_user)],
    service: Annotated[SupportService, Depends(support_dependency)],
    thread_id: str = "",
) -> EventSourceResponse:
    try:
        page_context = SupportPageContext.model_validate_json(context)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="页面上下文格式不正确") from exc
    if not message.strip():
        raise HTTPException(status_code=422, detail="咨询内容不能为空")

    async def generate() -> AsyncIterator[dict[str, str]]:
        final: dict[str, Any] | None = None
        final_meta: dict[str, Any] = {}
        handle = start_trace(
            name="support:fox",
            input={"message": message[:200], "context": page_context.model_dump(mode="json")},
            metadata={"feature": "support", "thread_id": thread_id},
            session_id=thread_id or None,
            tags=["support", "travel-copilot", "fox"],
        )
        try:
            async for event in service.stream_reply(
                user=user,
                message=message,
                context=page_context,
                thread_id=thread_id,
            ):
                if event.event == "done" and isinstance(event.data, dict):
                    final = event.data
                    final_meta = event.meta
                yield sse_payload(event)
        except Exception as exc:
            update(handle.observation, level="ERROR", status_message=str(exc))
            yield sse_payload(AgentEvent(event="error", data={"code": "support_stream_failed", "detail": "客服流中断"}))
        finally:
            score_trace(handle, "task_success", 1 if final else 0, data_type="BOOLEAN")
            if final:
                score_trace(handle, "support_agent", str(final.get("agent") or ""), data_type="CATEGORICAL")
                score_trace(handle, "support_sources", len(final.get("sources") or []), data_type="NUMERIC")
                score_trace(handle, "support_skill_count", len(final_meta.get("skills") or []), data_type="NUMERIC")
                score_trace(handle, "support_rag_chunk_count", len(final_meta.get("rag_chunks") or []), data_type="NUMERIC")
            finish_trace(
                handle,
                output=trace_value(
                    {
                        "success": final is not None,
                        "intent": (final or {}).get("intent"),
                        "agent": (final or {}).get("agent"),
                        "source_count": len((final or {}).get("sources") or []),
                        "skills": final_meta.get("skills") or [],
                        "rag_retrieval": final_meta.get("rag_retrieval"),
                        "rag_chunks": final_meta.get("rag_chunks") or [],
                    }
                ),
            )

    return EventSourceResponse(generate(), ping=10)


@app.patch("/api/profile")
async def update_profile(
    payload: ProfileUpdatePayload,
    user: Annotated[UserRecord, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    updated = await Repository(session).update_user_style(user.id, payload.style_preference)
    if updated is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user": user_profile(updated).model_dump(mode="json")}


@app.get("/api/providers")
async def provider_status() -> dict[str, Any]:
    missing = settings.missing_required()
    return {
        "mode": "live",
        "amap_web": {"configured": bool(settings.amap_web_service_key)},
        "amap_js": {"configured": bool(settings.amap_js_key and settings.amap_js_security_code)},
        "deepseek": {"configured": bool(settings.deepseek_api_key), "model": settings.deepseek_model},
        "mcp": {"configured": bool(mcp_client.session), "transport": mcp_client.transport, "server": "travel-tools"},
        "rail_12306": {"configured": True},
        "rag": rag_index.status(),
        "missing_required": missing,
    }


@app.get("/api/map/provinces")
async def provinces(service: Annotated[TravelAgentService, Depends(service_dependency)]) -> dict[str, Any]:
    try:
        return {"provinces": [item.model_dump(mode="json") for item in await service.provinces()]}
    except (ProviderError, ConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/provinces/{adcode}/attractions")
async def province_attractions(
    adcode: str,
    service: Annotated[TravelAgentService, Depends(service_dependency)],
    user: Annotated[UserRecord | None, Depends(optional_user)],
    name: str = "",
) -> dict[str, Any]:
    try:
        style = user.style_preference if user else "balanced"
        items = await service.province_attractions(adcode, name, style_preference=style)
        return {"adcode": adcode, "name": name, "attractions": [item.model_dump(mode="json") for item in items]}
    except (ProviderError, ConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/attractions/search")
async def search_attractions(
    q: str,
    service: Annotated[TravelAgentService, Depends(service_dependency)],
    province: str = "",
    nationwide: bool = False,
) -> dict[str, Any]:
    try:
        return (await service.search(q, province, nationwide)).model_dump(mode="json")
    except (ProviderError, ConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/attractions/{poi_id}/prepare")
async def prepare_attraction_page(poi_id: str) -> dict[str, Any]:
    if get_redis() is not None:
        task = await prepare_durable_attraction(poi_id)
        return {"poi_id": poi_id, "job_id": task["job_id"], "status": task["status"]}
    job = prepare_attraction(poi_id)
    return {"poi_id": poi_id, "job_id": job.job_id, "status": job.status}


@app.get("/api/attractions/{poi_id}/stream")
async def attraction_stream(poi_id: str) -> EventSourceResponse:
    async def generate() -> AsyncIterator[dict[str, str]]:
        stream = stream_durable_attraction_events(poi_id) if get_redis() is not None else stream_attraction_events(poi_id)
        async for event in stream:
            yield sse_payload(event)

    return EventSourceResponse(generate(), ping=10)


@app.get("/api/attractions/tasks/{job_id}")
async def attraction_task(
    job_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if get_redis() is None:
        raise HTTPException(status_code=503, detail="持久化任务队列未启用")
    row = await DurableAttractionStore(session).get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="景区任务不存在")
    return {"task": task_snapshot(row)}


@app.get("/api/transport/stream")
async def transport_stream(
    origin: str,
    destination: str,
    service: Annotated[TravelAgentService, Depends(service_dependency)],
    travel_date: date | None = None,
    poi_id: str = "",
    days: int | None = Query(default=None, ge=1, le=14),
    session_id: str = "",
) -> EventSourceResponse:
    async def generate() -> AsyncIterator[dict[str, str]]:
        final: dict[str, Any] | None = None
        handle = start_trace(
            name=f"transport:{origin}->{destination}",
            input={"origin": origin, "destination": destination, "travel_date": travel_date.isoformat() if travel_date else None, "days": days},
            metadata={"feature": "transport", "poi_id": poi_id},
            session_id=session_id or None,
            tags=["transport", "travel-copilot"],
        )
        try:
            async for event in service.transport_stream(
                origin=origin,
                destination=destination,
                travel_date=travel_date,
                poi_id=poi_id,
                days=days,
                session_id=session_id,
                trace_context=handle.context,
            ):
                if event.event == "done" and isinstance(event.data, dict):
                    final = event.data
                yield sse_payload(event)
        except Exception as exc:
            update(handle.observation, level="ERROR", status_message=str(exc))
            yield sse_payload(AgentEvent(event="error", data=str(exc)))
        finally:
            success = final is not None
            score_trace(handle, "task_success", 1 if success else 0, data_type="BOOLEAN")
            output = {"success": success}
            if final:
                periods = final.get("ticket_periods") or []
                route = final.get("route") or {}
                agent = final.get("agent") or {}
                score_trace(handle, "ticket_period_count", len(periods), data_type="NUMERIC")
                score_trace(handle, "route_available", 1 if route.get("distance_m") else 0, data_type="BOOLEAN")
                score_trace(handle, "fallback_used", 1 if not periods and route.get("distance_m") else 0, data_type="BOOLEAN")
                score_trace(handle, "agent_tool_calls", int(agent.get("tool_calls") or 0), data_type="NUMERIC")
                score_trace(handle, "agent_replan_count", int(agent.get("replan_count") or 0), data_type="NUMERIC")
                output = {
                    "origin": final.get("origin"),
                    "destination": final.get("destination"),
                    "travel_date": final.get("travel_date"),
                    "ticket_period_count": len(periods),
                    "route_distance_m": route.get("distance_m"),
                    "caveats": final.get("caveats") or [],
                }
            finish_trace(handle, output=trace_value(output))

    return EventSourceResponse(generate(), ping=10)


@app.get("/api/preferences")
async def get_preferences(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    return {"preferences": await Repository(session).get_preferences()}


@app.post("/api/preferences")
async def save_preferences(payload: PreferencePayload, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    return {"preferences": await Repository(session).save_preferences(payload.model_dump())}


@app.get("/api/sessions/{session_id}")
async def get_session_state(session_id: str, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    data = await Repository(session).get_planning_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": data}


@app.get("/api/agent/graphs/{thread_id}")
async def get_graph_state(thread_id: str) -> dict[str, Any]:
    data = await read_graph_checkpoint(thread_id)
    if not data:
        raise HTTPException(status_code=404, detail="Graph Checkpoint 不存在")
    return {"graph": data}


@app.get("/api/agent/runs/{run_id}")
async def agent_run_detail(run_id: str, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    data = await Repository(session).get_agent_run(run_id)
    if not data:
        raise HTTPException(status_code=404, detail="Agent Run 不存在")
    return {"run": data}


@app.get("/api/agent/runs")
async def agent_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return {"runs": await Repository(session).list_runs(limit)}


app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str) -> FileResponse:
        target = FRONTEND_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(
            FRONTEND_DIST / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


def run() -> None:
    import uvicorn

    uvicorn.run("backend.app.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    run()
