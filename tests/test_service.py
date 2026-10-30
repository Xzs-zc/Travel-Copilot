from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.agent.service import TravelAgentService
from backend.app.db import Base
from backend.app.schemas import AgentEvent, FlightOption, RouteMapInfo, SourceRef, TicketPeriod


def poi(poi_id: str, name: str, rating: float = 4.5, location: str = "120.0,30.0"):
    return {
        "poi_id": poi_id, "name": name, "type": "风景名胜", "address": "测试地址",
        "location": location, "province": "浙江省", "city": "杭州市", "district": "西湖区",
        "tel": "", "website": "", "rating": rating, "cost": 50, "open_time": "08:00-17:00",
        "photos": [], "children": [], "source": {"title": name, "provider": "高德地图", "url": "", "excerpt": ""},
    }


class FakeAmap:
    async def china_provinces(self):
        return []

    async def search_pois(self, keywords, **kwargs):
        return [poi("1", "西湖", 4.9), poi("2", "灵隐寺", 4.8), poi("3", "西溪湿地", 4.7), poi("4", "宋城", 4.6)]

    async def geocode(self, address, city=""):
        return {"location": "120,30"}

    async def driving_route(self, origin, destination):
        return {"distance_m": 1000, "duration_s": 1200, "tolls": 0, "source": {"title": "路线", "provider": "高德地图", "url": "", "excerpt": ""}}

    async def weather(self, city):
        return {}


class FakeRuntime:
    def __init__(self, final):
        self.final = final

    async def run_stream(self, **kwargs):
        yield AgentEvent(event="done", data=self.final, step=1, run_id="run1")

    @staticmethod
    def transport_system_prompt():
        return "transport"

    @staticmethod
    def attraction_system_prompt():
        return "attraction"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_province_attractions_use_deterministic_rating_order(session):
    service = TravelAgentService(session, runtime=FakeRuntime({}), amap=FakeAmap())
    items = await service.province_attractions("330000", "浙江省")
    assert len(items) == 4
    assert {item.poi_id for item in items} == {"1", "2", "3", "4"}
    assert items[0].rank_reason == "根据均衡偏好，结合高德评分与景区类型排序"


@pytest.mark.asyncio
async def test_search_exact_direct_and_ambiguous(session):
    service = TravelAgentService(session, runtime=FakeRuntime({}), amap=FakeAmap())
    exact = await service.search("西湖", "浙江省")
    assert exact.direct_poi_id == "1"
    ambiguous = await service.search("景区", "浙江省")
    assert ambiguous.direct_poi_id is None
    assert len(ambiguous.results) == 4


def test_transport_plan_validation(session):
    service = TravelAgentService(session, runtime=FakeRuntime({}), amap=FakeAmap())
    route = RouteMapInfo(
        origin_name="上海", destination_name="杭州",
        origin_location="121.47,31.23", destination_location="120.15,30.27",
        distance_m=180000, duration_s=9000, tolls=80, path=[[121.47, 31.23], [120.15, 30.27]],
        source=SourceRef(title="高德驾车路线", provider="高德地图"),
    )
    periods = [TicketPeriod(
        label="09:00–11:59", start_time="09:00", end_time="11:59",
        train_count=5, available_count=4, status="ample",
        seat_summary=["二等座"], summary="共 5 个车次，4 个有余票",
    )]
    plan = service._build_transport_plan(
        "上海", "杭州", date(2026, 10, 1),
        {"blessing": "愿君此行，湖山相迎。", "caveats": []},
        route, periods,
        {"city": "杭州市", "report_time": "2026-09-20 08:00", "casts": []},
        3,
    )
    assert plan.route.distance_m == 180000
    assert plan.ticket_periods[0].status == "ample"
    assert plan.days == 3
    assert "湖山" in plan.blessing.text


def test_transport_recommendation_respects_drive_and_rail_rules():
    short_route = RouteMapInfo(
        origin_name="上海", destination_name="杭州",
        origin_location="121.47,31.23", destination_location="120.15,30.27",
        distance_m=180000, duration_s=2 * 3600, tolls=80,
        source=SourceRef(title="高德驾车路线", provider="高德地图"),
    )
    short = TravelAgentService._recommend_transport(
        {}, short_route, {"train_count": 4, "min_duration_minutes": 150}, []
    )
    assert short.mode == "high_speed_rail"

    long_route = short_route.model_copy(update={"duration_s": 5 * 3600})
    flight = FlightOption(airline="测试航空", price=800, duration_minutes=150, stops=0)
    long = TravelAgentService._recommend_transport(
        {"mode": "high_speed_rail_or_flight"}, long_route, {"train_count": 2, "min_duration_minutes": 420}, [flight]
    )
    assert long.mode == "high_speed_rail_or_flight"
    invalid = TravelAgentService._recommend_transport(
        {"mode": "flight"}, long_route, {"train_count": 2, "min_duration_minutes": 420}, []
    )
    assert invalid.mode == "high_speed_rail"


def test_weather_advice_suggests_dry_alternative():
    weather = {"casts": [
        {"date": "2026-10-05", "dayweather": "小雨"},
        {"date": "2026-10-06", "dayweather": "中雨"},
        {"date": "2026-10-07", "dayweather": "晴"},
    ]}
    advice = TravelAgentService._weather_advice(weather, date(2026, 10, 5), 2, True)
    assert advice.suggested_dates == ["2026-10-07"]
    assert advice.best_date == "2026-10-07"


class StreamRuntime:
    def __init__(self, task, payload):
        self.task = task
        self.payload = payload

    async def run_stream(self, **kwargs):
        if kwargs["task"] == "attraction_page":
            base = poi("B1", "西湖", 4.9, "120.1,30.2")
            yield AgentEvent(event="tool_result", data={"tool": "get_attraction_detail", "result": {"ok": True, "poi": base, "nearby": []}}, step=1)
            yield AgentEvent(event="tool_result", data={"tool": "verify_official_page", "result": {"ok": True, "status": "official", "url": "https://westlake.example", "matches": ["开放时间：08:00-17:00"]}}, step=2)
            yield AgentEvent(event="done", data={"summary": "湖山胜景", "travel_tips": ["提前预约"], "sub_spots": []}, step=3)
        else:
            yield AgentEvent(event="tool_result", data={"tool": "route_estimate", "result": {"ok": True, "route": {"origin_name": "上海", "destination_name": "杭州", "origin_location": "121.47,31.23", "destination_location": "120.15,30.27", "distance_m": 180000, "duration_s": 9000, "tolls": 80, "path": [[121.47,31.23],[120.15,30.27]], "source": {"title": "高德驾车路线", "provider": "高德地图", "url": "", "excerpt": ""}}}}, step=1)
            yield AgentEvent(event="tool_result", data={"tool": "query_train", "result": {"ok": True, "ticket_periods": [{"label": "09:00–11:59", "start_time": "09:00", "end_time": "11:59", "train_count": 5, "available_count": 4, "status": "ample", "seat_summary": ["二等座"], "summary": "共 5 个车次，4 个有余票"}]}}, step=2)
            yield AgentEvent(event="done", data=self.payload, step=3)

    @staticmethod
    def attraction_system_prompt(): return "attraction"
    @staticmethod
    def transport_system_prompt(): return "transport"


class FakeImages:
    async def cache(self, url): return ""
    async def cache_many(self, urls, limit=8): return []


class RecordingImages:
    async def cache(self, url): return f"/media/{url.rsplit('/', 1)[-1]}" if url else ""
    async def cache_many(self, urls, limit=8):
        return [await self.cache(url) for url in urls[:limit] if url]


class EmptyWikimedia:
    async def search(self, *args, **kwargs): return []


class EmptyOfficialImages:
    async def find_for_attraction(self, *args, **kwargs): return []


def test_hero_image_prefers_scenic_landscape():
    selected = TravelAgentService._hero_image_url(
        [
            {"url": "https://example.com/night-street.jpg", "width": 4370, "height": 3277, "title": "Huangshan bei Nacht"},
            {"url": "https://example.com/scenic.jpg", "width": 3648, "height": 2736, "title": "China Huang Shan scenic view"},
        ],
        [],
        "黄山风景区",
    )
    assert selected == "https://example.com/scenic.jpg"


@pytest.mark.asyncio
async def test_best_spot_image_prefers_matching_poi_photo(session):
    service = TravelAgentService(
        session,
        runtime=FakeRuntime({}),
        amap=FakeAmap(),
        image_cache=RecordingImages(),
        wikimedia=EmptyWikimedia(),
    )
    image = await service._best_spot_image(
        "排云亭",
        "黄山市",
        ["https://example.com/paixianting.jpg"],
        fallback_first=True,
    )
    assert image == "/media/paixianting.jpg"


@pytest.mark.asyncio
async def test_attraction_stream_emits_partial_then_final_structured_done(session):
    service = TravelAgentService(
        session,
        runtime=StreamRuntime("attraction_page", {}),
        amap=FakeAmap(),
        image_cache=FakeImages(),
        official_images=EmptyOfficialImages(),
        wikimedia=EmptyWikimedia(),
    )
    events = [event async for event in service.attraction_stream("B1")]
    assert [event.event for event in events] == ["tool_result", "partial", "tool_result", "done"]
    assert events[1].data["name"] == "西湖"
    assert events[1].data["open_status"] == "pending"
    assert events[-1].data["name"] == "西湖"
    assert events[-1].data["open_status"] == "official"


@pytest.mark.asyncio
async def test_transport_stream_emits_single_final_plan(session):
    payload = {"blessing": "愿君此行，湖山相迎。", "caveats": []}
    service = TravelAgentService(session, runtime=StreamRuntime("transport_plan", payload), amap=FakeAmap())
    events = [event async for event in service.transport_stream(origin="上海", destination="杭州", travel_date=date(2026, 10, 1))]
    assert [event.event for event in events] == ["tool_result", "tool_result", "done"]
    assert events[-1].data["ticket_periods"][0]["status"] == "available"
    assert events[-1].data["route"]["distance_m"] == 1000


@pytest.mark.asyncio
async def test_transport_stream_allows_missing_date_and_days(session):
    payload = {"blessing": "山高水远路迢迢，证件雨伞都带好。", "caveats": []}
    service = TravelAgentService(session, runtime=StreamRuntime("transport_plan", payload), amap=FakeAmap())
    events = [event async for event in service.transport_stream(origin="上海", destination="杭州")]
    result = events[-1].data
    assert result["date_is_recommended"] is True
    assert result["assumed_days"] is True
    assert result["days"] == 3


class TransferStreamRuntime:
    @staticmethod
    def transport_system_prompt(): return "transport"

    @staticmethod
    def attraction_system_prompt(): return "attraction"

    async def run_stream(self, **kwargs):
        route = {
            "origin_name": "深圳", "destination_name": "崇左市",
            "origin_location": "114.05,22.54", "destination_location": "107.36,22.37",
            "distance_m": 889801, "duration_s": 34250, "tolls": 0,
            "path": [[114.05,22.54], [107.36,22.37]],
            "source": {"title": "高德驾车路线", "provider": "高德地图", "url": "", "excerpt": ""},
        }
        yield AgentEvent(event="tool_result", data={"tool": "route_estimate", "result": {"ok": True, "route": route}}, step=1)
        yield AgentEvent(event="tool_result", data={
            "tool": "query_train",
            "arguments": {"origin": "深圳", "destination": "崇左市", "travel_date": "2026-10-06"},
            "result": {"ok": False, "error": "没有可购车次"},
        }, step=1)
        yield AgentEvent(event="tool_result", data={
            "tool": "query_train",
            "arguments": {"origin": "深圳", "destination": "南宁", "travel_date": "2026-10-06"},
            "result": {
                "ok": True, "train_count": 3,
                "train_summary": {"train_count": 3, "high_speed_count": 3, "min_duration_minutes": 214, "min_price": 324.5},
                "ticket_periods": [{"label": "15:00–17:59", "start_time": "15:00", "end_time": "17:59", "train_count": 2, "available_count": 1, "status": "available", "seat_summary": [], "summary": "共 2 个车次，0 个有余票", "min_price": 324.5, "duration_minutes": 214}],
            },
        }, step=2)
        yield AgentEvent(event="tool_result", data={
            "tool": "query_train",
            "arguments": {"origin": "南宁", "destination": "崇左市", "travel_date": "2026-10-06"},
            "result": {
                "ok": True, "train_count": 23,
                "train_summary": {"train_count": 23, "high_speed_count": 22, "min_duration_minutes": 30, "min_price": 19.5},
                "ticket_periods": [{"label": "18:00–20:59", "start_time": "18:00", "end_time": "20:59", "train_count": 11, "available_count": 1, "status": "available", "seat_summary": [], "summary": "共 11 个车次，0 个有余票", "min_price": 19.5, "duration_minutes": 30}],
            },
        }, step=2)
        yield AgentEvent(event="tool_result", data={
            "tool": "query_flight",
            "result": {"ok": True, "flights": [{"airline": "测试航司", "flight_number": "ZZ1", "price": 2000, "duration_minutes": 300, "departure_time": "2026-10-06 10:00", "arrival_time": "2026-10-06 15:00", "departure_airport": "深圳", "arrival_airport": "南宁", "stops": 0, "source": {"title": "Google Flights", "provider": "Google Flights / SerpAPI", "url": "", "excerpt": ""}}]},
        }, step=3)
        yield AgentEvent(event="done", data={
            "blessing": "证件带好别慌张，车票手快才稳当。",
            "caveats": [],
            "recommendation": {"mode": "high_speed_rail", "summary": "高铁中转更快", "reason": "完整铁路行程约 244 分钟"},
        }, step=4)


@pytest.mark.asyncio
async def test_transport_stream_keeps_all_transfer_periods_and_allows_flight_without_direct_rail(session):
    service = TravelAgentService(session, runtime=TransferStreamRuntime(), amap=FakeAmap())
    events = [event async for event in service.transport_stream(
        origin="深圳", destination="崇左市", travel_date=date(2026, 10, 6)
    )]
    result = events[-1].data
    labels = [item["label"] for item in result["ticket_periods"]]
    assert labels == ["深圳→南宁 · 15:00–17:59", "南宁→崇左市 · 18:00–20:59"]
    assert result["train_summary"]["min_duration_minutes"] == 244
    assert result["flight_options"] == []
    assert result["flight_periods"]
    assert result["flight_periods"][0]["min_price"] == 2000
    assert result["flight_comparison_allowed"] is True


def test_personalized_selection_keeps_secondary_style():
    candidates = [
        {"poi_id": f"l{i}", "name": f"山湖瀑布{i}", "type": "风景名胜", "rating": 5 - i * 0.1}
        for i in range(8)
    ] + [
        {"poi_id": f"c{i}", "name": f"古城博物馆{i}", "type": "文化场馆", "rating": 4.5 - i * 0.1}
        for i in range(4)
    ]
    selected = TravelAgentService._fallback_personalized_selection(candidates, "landscape")
    styles = [TravelAgentService._attraction_style(item) for item in selected]
    assert len(selected) == 7
    assert styles.count("landscape") >= 4
    assert 1 <= styles.count("culture") <= 2
