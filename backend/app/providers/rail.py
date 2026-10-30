from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from ..config import get_settings
from .amap import ConfigurationError, ProviderError


class Train12306Client:
    INIT_URL = "https://kyfw.12306.cn/otn/leftTicket/init"
    STATION_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
    PRICE_URL = "https://kyfw.12306.cn/otn/leftTicketPrice/queryAllPublicPrice"
    TICKET_URL = "https://kyfw.12306.cn/otn/leftTicket/queryI"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Referer": INIT_URL,
        "Origin": "https://kyfw.12306.cn",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    PRICE_FIELDS = (
        ("wz_price", "无座"), ("yz_price", "硬座"), ("yw_price", "硬卧"),
        ("rw_price", "软卧"), ("gr_price", "高级软卧"), ("ze_price", "二等座"),
        ("zy_price", "一等座"), ("swz_price", "商务座"),
    )
    SEAT_FIELDS = (
        (21, "高级软卧"), (23, "软卧"), (24, "软座"), (26, "无座"),
        (28, "硬卧"), (29, "硬座"), (30, "二等座"), (31, "一等座"),
        (32, "商务座"), (33, "动卧"),
    )

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None
        self._station_cache: tuple[float, list[dict[str, str]]] | None = None
        self._lock = asyncio.Lock()

    async def _http(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=get_settings().http_timeout_seconds,
                follow_redirects=True,
                headers=self.HEADERS,
            )
        response = await self._client.request(method, url, **kwargs)
        if "error.html" in str(response.url) or "/ntce/" in str(response.url):
            raise ProviderError("12306 触发反爬或访问受限，请稍后重试")
        response.raise_for_status()
        return response

    async def close(self) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    async def _ensure_session(self) -> None:
        await self._http("GET", self.INIT_URL)

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                await self._ensure_session()
                response = await self._http("GET", url, params=params)
                data = json.loads(response.content.decode("utf-8-sig"))
                if not isinstance(data, dict):
                    raise ProviderError("12306 返回结构不正确")
                return data
            except (httpx.HTTPError, json.JSONDecodeError, ProviderError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.4)
        raise ProviderError(f"12306 查询失败：{last_error}")

    async def stations(self) -> list[dict[str, str]]:
        async with self._lock:
            if self._station_cache and time.time() - self._station_cache[0] < 86400:
                return self._station_cache[1]
        response = await self._http("GET", self.STATION_URL)
        text = response.content.decode("utf-8", "ignore")
        records = []
        for match in re.finditer(r"@[^|]*\|([^|]+)\|([A-Z]+)\|([^|]*)\|([^|]*)\|", text):
            records.append({
                "name": match.group(1).strip(),
                "code": match.group(2).strip(),
                "pinyin": match.group(3).strip(),
                "short": match.group(4).strip(),
            })
        if not records:
            raise ProviderError("12306 车站数据格式发生变化")
        async with self._lock:
            self._station_cache = (time.time(), records)
        return records

    @staticmethod
    def normalize_station(value: str) -> str:
        return re.sub(r"[\s站]", "", value or "")

    def station_candidates(self, value: str, stations: list[dict[str, str]]) -> list[dict[str, str]]:
        normalized = self.normalize_station(value).removesuffix("市")
        if re.fullmatch(r"[A-Z]{3}", value or ""):
            exact = [item for item in stations if item["code"] == value]
            if exact:
                return exact
        matches = [item for item in stations if normalized and normalized in self.normalize_station(item["name"])]
        def priority(item: dict[str, str]) -> tuple[int, str]:
            name = item["name"]
            if "虹桥" in name:
                return (0, name)
            if name.endswith(("南", "东")):
                return (1, name)
            if name == normalized:
                return (2, name)
            if name.endswith(("西", "北")):
                return (3, name)
            return (4, name)
        return sorted(matches, key=priority)[:4]

    @staticmethod
    def format_price(raw: Any) -> float:
        value = str(raw or "").strip()
        if not value or value == "--":
            return 0.0
        if value.isdigit():
            return float(value) / 10 if len(value) > 1 else float(f"0.{value}")
        try:
            return float(value)
        except ValueError:
            return 0.0

    @staticmethod
    def format_duration(raw: str) -> str:
        parts = (raw or "").split(":")
        if len(parts) != 2:
            return raw or ""
        hours, minutes = int(parts[0]), int(parts[1])
        if hours and minutes:
            return f"{hours}小时{minutes}分"
        if hours:
            return f"{hours}小时"
        return f"{minutes}分钟"

    def parse_availability(self, rows: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for raw in rows:
            parts = raw.split("|")
            if len(parts) < 34:
                continue
            key = f"{parts[3]}|{parts[6]}|{parts[7]}"
            seats = {
                label: parts[index]
                for index, label in self.SEAT_FIELDS
                if index < len(parts) and parts[index] and parts[index] not in {"无", "--"}
            }
            result[key] = {"seats": seats, "can_buy": parts[11] == "Y"}
        return result

    async def query(self, origin: str, destination: str, travel_date: str) -> list[dict[str, Any]]:
        stations = await self.stations()
        origins = self.station_candidates(origin, stations)
        destinations = self.station_candidates(destination, stations)
        if not origins:
            raise ProviderError(f"12306 找不到出发站：{origin}")
        if not destinations:
            raise ProviderError(f"12306 找不到到达站：{destination}")
        pairs = [(origins[0], destinations[0])]
        if len(origins) > 1:
            pairs.append((origins[1], destinations[0]))
        merged: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for from_station, to_station in pairs:
            params = {
                "leftTicketDTO.train_date": travel_date,
                "leftTicketDTO.from_station": from_station["code"],
                "leftTicketDTO.to_station": to_station["code"],
                "purpose_codes": "ADULT",
            }
            try:
                price_data = await self._get_json(self.PRICE_URL, params)
            except ProviderError as exc:
                errors.append(str(exc))
                continue
            if not price_data.get("data"):
                errors.extend(str(item) for item in (price_data.get("messages") or []))
                continue
            availability: dict[str, dict[str, Any]] = {}
            availability_error = ""
            try:
                ticket_data = await self._get_json(self.TICKET_URL, params)
                availability = self.parse_availability(ticket_data.get("data", {}).get("result", []))
            except ProviderError as exc:
                availability_error = str(exc)
            for item in price_data.get("data", []):
                dto = item.get("queryLeftNewDTO") or {}
                code = dto.get("station_train_code") or ""
                from_code = dto.get("from_station_telecode") or ""
                to_code = dto.get("to_station_telecode") or ""
                if not code:
                    continue
                key = f"{code}|{from_code}|{to_code}"
                prices = {
                    label: self.format_price(dto.get(field))
                    for field, label in self.PRICE_FIELDS
                    if self.format_price(dto.get(field)) > 0
                }
                merged[key] = {
                    "train_code": code,
                    "from_station": dto.get("from_station_name") or from_station["name"],
                    "to_station": dto.get("to_station_name") or to_station["name"],
                    "start_time": dto.get("start_time") or "",
                    "arrive_time": dto.get("arrive_time") or "",
                    "duration": dto.get("lishi") or "",
                    "date": travel_date,
                    "prices": prices,
                    "seats": availability.get(key, {}).get("seats", {}),
                    "availability_error": availability_error,
                    "source_url": "https://www.12306.cn/index/",
                }
            if merged:
                break
        results = sorted(merged.values(), key=lambda item: (item["start_time"], item["train_code"]))
        if not results:
            detail = errors[-1] if errors else "没有可购车次"
            if errors and ("反爬" in detail or "查询失败" in detail or "访问受限" in detail):
                raise ProviderError(f"12306 查询失败：{detail}")
            return []
        return results
