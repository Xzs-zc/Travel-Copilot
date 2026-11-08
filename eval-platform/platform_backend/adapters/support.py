from __future__ import annotations

from typing import Any, Iterable

from backend.app.support.public_api import (
    route_support_message,
    search_support_knowledge,
)
from backend.app.support.rag import SupportRAG
from backend.app.support.schemas import SupportPageContext


def build_page_context(payload: dict[str, Any]) -> SupportPageContext:
    return SupportPageContext(**payload)


def route_message(message: str, context: dict[str, Any]) -> dict[str, Any]:
    return route_support_message(message, build_page_context(context))


async def search_knowledge(
    query: str,
    *,
    routes: Iterable[str],
    limit: int,
    mode: str,
) -> dict[str, Any]:
    return await search_support_knowledge(
        query,
        routes=routes,
        limit=limit,
        mode=mode,
    )


def knowledge_section_content() -> dict[tuple[str, str], str]:
    return {
        (chunk.doc_id, chunk.section): chunk.content
        for chunk in SupportRAG(enabled=False).load_chunks()
    }
