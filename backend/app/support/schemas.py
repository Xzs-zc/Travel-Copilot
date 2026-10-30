from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PageType = Literal[
    "home",
    "province",
    "attraction",
    "plan",
    "account",
    "unknown",
]


class SupportPageContext(BaseModel):
    route: str = "/"
    page_type: PageType = "unknown"
    province_adcode: str = ""
    province_name: str = ""
    poi_id: str = ""
    attraction_name: str = ""
    plan_session_id: str = ""
    origin: str = ""
    destination: str = ""
    travel_date: str = ""
    ui_state: dict[str, Any] = Field(default_factory=dict)


class SupportAction(BaseModel):
    type: Literal["navigate", "reload"] = "navigate"
    label: str
    href: str = ""


class SupportSource(BaseModel):
    title: str
    provider: str
    url: str = ""
    excerpt: str = ""


class SupportReply(BaseModel):
    thread_id: str
    reply: str
    agent: str
    intent: str
    confidence: float = 0
    sources: list[SupportSource] = Field(default_factory=list)
    actions: list[SupportAction] = Field(default_factory=list)
    created_at: str = ""
