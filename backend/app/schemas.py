from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class SourceRef(BaseModel):
    title: str
    url: str = ""
    provider: str
    fetched_at: datetime = Field(default_factory=datetime.now)
    excerpt: str = ""


class ProvinceSummary(BaseModel):
    name: str
    adcode: str
    citycode: str = ""
    center: str = ""
    level: str = "province"
    supported: bool = True
    unsupported_reason: str = ""


class AttractionSummary(BaseModel):
    poi_id: str
    name: str
    province: str = ""
    city: str = ""
    district: str = ""
    address: str = ""
    location: str = ""
    rating: float = 0
    cost: float = 0
    type: str = ""
    photos: list[str] = Field(default_factory=list)
    source: SourceRef | None = None
    rank_reason: str = ""


class SubSpot(BaseModel):
    name: str
    location: str = ""
    image: str = ""
    image_source: Literal["exact", "contextual", "placeholder"] = "placeholder"
    description: str = ""
    open_time: str = ""
    cost: float = 0


class GuideLink(BaseModel):
    platform: str
    url: str


class AttractionDetail(BaseModel):
    poi_id: str
    name: str
    province: str = ""
    city: str = ""
    district: str = ""
    address: str = ""
    location: str = ""
    rating: float = 0
    reference_cost: float = 0
    ticket_price: str = ""
    open_time: str = ""
    telephone: str = ""
    open_status: Literal["official", "amap_only", "pending"] = "pending"
    official_url: str = ""
    official_excerpts: list[str] = Field(default_factory=list)
    summary: str = ""
    travel_tips: list[str] = Field(default_factory=list)
    hero_image: str = ""
    photos: list[str] = Field(default_factory=list)
    image_sources: list[dict[str, str]] = Field(default_factory=list)
    sub_spots: list[SubSpot] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    guide_links: list[GuideLink] = Field(default_factory=list)
    weather: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=datetime.now)


class RouteMapInfo(BaseModel):
    origin_name: str
    destination_name: str
    origin_location: str
    destination_location: str
    origin_province: str = ""
    origin_city: str = ""
    origin_district: str = ""
    destination_province: str = ""
    destination_city: str = ""
    destination_district: str = ""
    distance_m: int = 0
    duration_s: int = 0
    tolls: float = 0
    path: list[list[float]] = Field(default_factory=list)
    source: SourceRef


class TicketPeriod(BaseModel):
    label: str
    start_time: str
    end_time: str
    train_count: int
    available_count: int
    status: Literal["ample", "available", "tight"]
    seat_summary: list[str] = Field(default_factory=list)
    summary: str
    min_price: float = 0
    duration_minutes: int = 0
    recommended: bool = False


class FlightOption(BaseModel):
    airline: str = ""
    flight_number: str = ""
    price: float = 0
    currency: str = "CNY"
    duration_minutes: int = 0
    departure_time: str = ""
    arrival_time: str = ""
    departure_airport: str = ""
    arrival_airport: str = ""
    stops: int = 0
    source: SourceRef | None = None


class TransportRecommendation(BaseModel):
    mode: Literal["drive", "high_speed_rail", "flight", "high_speed_rail_or_flight", "mixed"] = "mixed"
    summary: str = ""
    reason: str = ""


class FlightPeriod(BaseModel):
    label: str
    start_time: str
    end_time: str
    flight_count: int
    min_price: float = 0
    duration_minutes: int = 0
    stops: int = 0
    summary: str = "该时段有余票"


class TransportComparison(BaseModel):
    train_price: float = 0
    flight_price: float = 0
    train_duration_minutes: int = 0
    flight_duration_minutes: int = 0
    price_gap: float = 0
    duration_gap_minutes: int = 0
    flight_available: bool = False


class WeatherAdvice(BaseModel):
    summary: str = ""
    suggested_dates: list[str] = Field(default_factory=list)
    best_date: str = ""


class Blessing(BaseModel):
    text: str
    style: Literal["ancient", "warm", "lively"] = "ancient"
    generated_at: datetime = Field(default_factory=datetime.now)


class TransportPlan(BaseModel):
    origin: str
    destination: str
    travel_date: date
    route: RouteMapInfo
    ticket_periods: list[TicketPeriod] = Field(default_factory=list)
    blessing: Blessing
    days: int = 1
    weather: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    recommendation: TransportRecommendation = Field(default_factory=TransportRecommendation)
    flight_options: list[FlightOption] = Field(default_factory=list)
    flight_periods: list[FlightPeriod] = Field(default_factory=list)
    transport_comparison: TransportComparison = Field(default_factory=TransportComparison)
    weather_advice: WeatherAdvice = Field(default_factory=WeatherAdvice)
    date_is_recommended: bool = False
    assumed_days: bool = False
    driving_recommended: bool = False
    show_driving_plan: bool = True
    generated_at: datetime = Field(default_factory=datetime.now)


class AgentEvent(BaseModel):
    event: Literal[
        "status", "plan", "state", "tool_start", "tool_result", "validation",
        "evidence", "replan", "message", "sources", "partial", "done", "error",
    ]
    data: Any = None
    step: int = 0
    run_id: str = ""
    duration_ms: int = 0
    tokens: dict[str, int] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    results: list[AttractionSummary] = Field(default_factory=list)
    direct_poi_id: str | None = None


class SessionState(BaseModel):
    id: str
    selected_poi_id: str = ""
    selected_attraction: AttractionDetail | None = None
    transport_plan: TransportPlan | None = None
    origin: str = ""
    updated_at: datetime = Field(default_factory=datetime.now)


class PreferencePayload(BaseModel):
    origin: str = ""
    preferred_transport: str = "high_speed"
    travel_style: str = ""
    avoid: str = ""


class UserProfile(BaseModel):
    id: str
    username: str
    style_preference: Literal["landscape", "culture", "balanced"] = "balanced"


class RegisterPayload(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)
    invite_code: str = Field(min_length=1)
    style_preference: Literal["landscape", "culture", "balanced"] = "balanced"


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class ProfileUpdatePayload(BaseModel):
    style_preference: Literal["landscape", "culture", "balanced"]
