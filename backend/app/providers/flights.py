from __future__ import annotations

import re
from typing import Any

import httpx

from ..config import get_settings
from .amap import ConfigurationError, ProviderError


CITY_AIRPORTS: dict[str, tuple[str, ...]] = {
    "北京": ("PEK", "PKX"), "上海": ("SHA", "PVG"), "广州": ("CAN",), "深圳": ("SZX",),
    "成都": ("CTU", "TFU"), "重庆": ("CKG",), "杭州": ("HGH",), "南京": ("NKG",),
    "武汉": ("WUH",), "西安": ("XIY",), "厦门": ("XMN",), "福州": ("FOC",),
    "昆明": ("KMG",), "桂林": ("KWL",), "三亚": ("SYX",), "海口": ("HAK",),
    "青岛": ("TAO",), "天津": ("TSN",), "长沙": ("CSX",), "郑州": ("CGO",),
    "济南": ("TNA",), "哈尔滨": ("HRB",), "沈阳": ("SHE",), "大连": ("DLC",),
    "乌鲁木齐": ("URC",), "拉萨": ("LXA",), "贵阳": ("KWE",), "南宁": ("NNG",),
    "合肥": ("HFE",), "南昌": ("KHN",), "太原": ("TYN",), "兰州": ("LHW",),
    "银川": ("INC",), "西宁": ("XNN",), "呼和浩特": ("HET",), "石家庄": ("SJW",),
    "宁波": ("NGB",), "温州": ("WNZ",), "无锡": ("WUX",), "珠海": ("ZUH",),
    "张家界": ("DYG",), "黄山": ("TXN",), "丽江": ("LJG",), "大理": ("DLU",),
    "西双版纳": ("JHG",), "敦煌": ("DNH",), "喀什": ("KHG",), "泉州": ("JJN",),
    "林芝": ("LZY",), "济宁": ("JNG",), "徐州": ("XUZ",), "临沂": ("LYI",),
    "日喀则": ("RKZ",), "昌都": ("BPX",), "阿里": ("NGQ",),
}


class SerpApiFlightProvider:
    """Query Google Flights through SerpAPI and return normalized flight options."""

    def __init__(self, *, api_key: str = "", base_url: str = "", timeout: int = 30):
        settings = get_settings()
        self.api_key = api_key or settings.serpapi_api_key
        self.base_url = (base_url or settings.serpapi_base_url).rstrip("/")
        self.timeout = timeout

    @staticmethod
    def resolve_airport(value: str) -> str:
        text = (value or "").strip()
        if re.fullmatch(r"[A-Z]{3}(,[A-Z]{3})*", text):
            return text
        normalized = re.sub(r"(特别行政区|自治区|省|市|县|区|景区|风景名胜区)$", "", text)
        for city, codes in CITY_AIRPORTS.items():
            if city in normalized or normalized in city:
                return ",".join(codes)
        return ""

    async def search(self, origin: str, destination: str, travel_date: str) -> list[dict[str, Any]]:
        if not self.api_key.strip():
            raise ConfigurationError("未配置 SERPAPI_API_KEY，无法查询航班")
        departure_id = self.resolve_airport(origin)
        arrival_id = self.resolve_airport(destination)
        if not departure_id or not arrival_id:
            raise ProviderError(f"暂未匹配到机场代码：{origin} → {destination}")
        params = {
            "engine": "google_flights",
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": travel_date,
            "type": "2",
            "currency": "CNY",
            "hl": "zh-cn",
            "gl": "cn",
            "travel_class": "1",
            "adults": "1",
            "api_key": self.api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(f"{self.base_url}/search.json", params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"SerpAPI 航班查询失败：{exc}") from exc
        if payload.get("error"):
            raise ProviderError(f"SerpAPI 航班查询失败：{payload['error']}")
        options = [
            parsed
            for item in [*(payload.get("best_flights") or []), *(payload.get("other_flights") or [])]
            if (parsed := self._parse_option(item)) is not None
        ]
        return options[:3]

    @staticmethod
    def _parse_option(item: dict[str, Any]) -> dict[str, Any] | None:
        segments = item.get("flights") or []
        if not segments:
            return None
        first = segments[0] or {}
        last = segments[-1] or {}
        departure = first.get("departure_airport") or {}
        arrival = last.get("arrival_airport") or {}
        total_duration = int(item.get("total_duration") or 0)
        if not total_duration:
            total_duration = sum(int(segment.get("duration") or 0) for segment in segments)
        return {
            "airline": str(first.get("airline") or ""),
            "flight_number": str(first.get("flight_number") or ""),
            "price": float(item.get("price") or 0),
            "currency": "CNY",
            "duration_minutes": total_duration,
            "departure_time": str(departure.get("time") or ""),
            "arrival_time": str(arrival.get("time") or ""),
            "departure_airport": str(departure.get("name") or departure.get("id") or ""),
            "arrival_airport": str(arrival.get("name") or arrival.get("id") or ""),
            "stops": max(0, len(segments) - 1),
            "source": {
                "title": "Google Flights",
                "url": "https://www.google.com/travel/flights",
                "provider": "Google Flights / SerpAPI",
                "excerpt": "航班价格和总耗时",
            },
        }
