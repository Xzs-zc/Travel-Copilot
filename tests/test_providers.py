from PIL import Image

from backend.app.agent.ticket_periods import build_ticket_periods, summarize_trains
from backend.app.providers.amap import AmapClient
from backend.app.providers.flights import SerpApiFlightProvider
from backend.app.providers.images import ImageCache
from backend.app.providers.official import OfficialPageProvider
from backend.app.providers.rail import Train12306Client


def test_amap_poi_normalization():
    raw = {
        "id": "B0001", "name": "西湖", "type": "风景名胜", "address": "龙井路",
        "location": "120.1,30.2", "pname": "浙江省", "cityname": "杭州市", "adname": "西湖区",
        "tel": "0571-12345", "website": "https://westlake.example",
        "biz_ext": {"rating": "4.8", "cost": "60", "open_time": "08:00-17:00"},
        "photos": [{"url": "https://img.example/1.jpg"}],
        "children": [],
    }
    poi = AmapClient.normalize_poi(raw)
    assert poi["poi_id"] == "B0001"
    assert poi["rating"] == 4.8
    assert poi["photos"] == ["https://img.example/1.jpg"]
    assert poi["source"]["provider"] == "高德地图"


def test_train_parser_and_price():
    client = Train12306Client()
    assert client.format_price("04760") == 476.0
    assert client.format_duration("01:04") == "1小时4分"
    raw = "|".join([
        "s", "预订", "train", "G1", "AOH", "HGH", "AOH", "HGH",
        "08:00", "09:00", "01:00", "Y", "yp", "20261001", "3", "O",
        "loc", "01", "02", "1", "0", "无", "", "有", "", "无", "无", "有", "有", "20", "5", "1", "无", "",
    ])
    parsed = client.parse_availability([raw])
    assert parsed["G1|AOH|HGH"]["seats"]["二等座"] == "5"


def test_official_provider_blocks_ota():
    provider = OfficialPageProvider()
    assert provider.validate_url("https://www.dpm.org.cn/")[0] is True
    assert provider.validate_url("https://www.ctrip.com/")[0] is False
    lines = provider._relevant_lines("开放时间：09:00-17:00\n欢迎光临\n门票：60元", ("开放时间", "门票"))
    assert lines == ["开放时间：09:00-17:00", "门票：60元"]


def test_image_cache_center_crop_to_gallery_bounds(tmp_path):
    Image.new("RGB", (800, 1600), (40, 120, 80)).save(tmp_path / "portrait.jpg")
    cache = ImageCache(tmp_path)
    cropped = cache.center_crop("/media/portrait.jpg", 760, 420)
    assert cropped == "/media/portrait-crop.jpg"
    with Image.open(tmp_path / "portrait-crop.jpg") as image:
        assert image.width >= 760
        assert image.height >= 420
        assert image.width / image.height >= 1.1


def test_train_summary_and_period_compare_fields():
    trains = [
        {"train_code": "G1", "start_time": "08:00", "duration": "02:30", "prices": {"二等座": 120}, "seats": {"二等座": "5"}},
        {"train_code": "D2", "start_time": "09:00", "duration": "04:00", "prices": {"二等座": 80}, "seats": {"二等座": "有"}},
    ]
    summary = summarize_trains(trains)
    periods = build_ticket_periods(trains)
    assert summary["min_price"] == 80
    assert summary["min_duration_minutes"] == 150
    assert periods[1].min_price == 80
    assert periods[1].duration_minutes == 240


def test_flight_option_normalization():
    option = SerpApiFlightProvider._parse_option({
        "price": 680.0,
        "total_duration": 155,
        "flights": [{
            "airline": "测试航空",
            "flight_number": "MU1234",
            "departure_airport": {"name": "上海虹桥", "time": "08:00"},
            "arrival_airport": {"name": "厦门高崎", "time": "10:35"},
        }],
    })
    assert option is not None
    assert option["price"] == 680.0
    assert option["duration_minutes"] == 155
    assert option["stops"] == 0


class FakeHubAmap:
    async def geocode(self, address, city=""):
        return {"formatted_address": address, "city": address, "location": "100,30" if address == "梁山" else "110,30"}

    async def search_nearby(self, keywords, location, *, radius=5000, offset=20):
        if "机场" in keywords:
            return [{"name": "林芝机场" if location == "110,30" else "济南遥墙机场", "type": "机场", "city": "林芝" if location == "110,30" else "济南", "location": location, "distance_m": 1200}]
        return [{"name": "林芝站" if location == "110,30" else "梁山站", "type": "火车站", "city": "林芝" if location == "110,30" else "济宁", "location": location, "distance_m": 500}]


def test_resolve_transport_hubs_returns_rail_and_air_candidates():
    import asyncio
    from backend.app.agent.transport_hubs import resolve_transport_hubs

    payload = asyncio.run(resolve_transport_hubs(FakeHubAmap(), "梁山", "林芝"))
    assert payload["ok"] is True
    assert payload["origin_rail_stations"][0]["name"] == "梁山站"
    assert payload["destination_airports"][0]["name"] == "林芝机场"
    assert any(item["name"] == "林芝机场" for item in payload["candidate_hubs"])
