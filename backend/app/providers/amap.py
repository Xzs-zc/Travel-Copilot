from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings
from ..schemas import ProvinceSummary, SourceRef


class ProviderError(RuntimeError):
    pass


class ConfigurationError(ProviderError):
    pass


class AmapClient:
    BASE_URL = "https://restapi.amap.com/v3"

    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else get_settings().amap_web_service_key
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "AmapClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=get_settings().http_timeout_seconds, follow_redirects=True)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ConfigurationError("未配置 AMAP_WEB_SERVICE_KEY")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=get_settings().http_timeout_seconds, follow_redirects=True)
        response = await self._client.get(
            f"{self.BASE_URL}/{path.lstrip('/')}",
            params={**params, "key": self.api_key, "output": "json"},
        )
        response.raise_for_status()
        data = response.json()
        if str(data.get("status")) != "1":
            raise ProviderError(f"高德 API 错误：{data.get('info') or '未知错误'}")
        return data

    async def china_provinces(self) -> list[ProvinceSummary]:
        data = await self._get(
            "config/district",
            {"keywords": "中国", "subdistrict": 1, "extensions": "all", "level": "country"},
        )
        districts = (data.get("districts") or [{}])[0].get("districts") or []
        results: list[ProvinceSummary] = []
        for item in districts:
            name = item.get("name") or ""
            if not name:
                continue
            unsupported = name in {"香港特别行政区", "澳门特别行政区", "台湾省"}
            results.append(ProvinceSummary(
                name=name,
                adcode=item.get("adcode") or "",
                citycode=item.get("citycode") or "",
                center=item.get("center") or "",
                level=item.get("level") or "province",
                supported=True,
                unsupported_reason="部分跨城交通能力可能受限" if unsupported else "",
            ))
        return results

    async def search_pois(
        self,
        keywords: str,
        *,
        city: str = "",
        types: str = "",
        offset: int = 25,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "keywords": keywords,
            "offset": max(1, min(25, offset)),
            "page": max(1, page),
            "extensions": "all",
        }
        if city:
            params["city"] = city
            params["citylimit"] = "true"
        if types:
            params["types"] = types
        data = await self._get("place/text", params)
        return [self.normalize_poi(item) for item in (data.get("pois") or [])]

    async def search_nearby(self, keywords: str, location: str, *, radius: int = 5000, offset: int = 20) -> list[dict[str, Any]]:
        data = await self._get(
            "place/around",
            {"keywords": keywords, "location": location, "radius": radius, "offset": offset, "extensions": "all"},
        )
        return [self.normalize_poi(item) for item in (data.get("pois") or [])]

    async def poi_detail(self, poi_id: str) -> dict[str, Any]:
        data = await self._get("place/detail", {"id": poi_id, "extensions": "all"})
        pois = data.get("pois") or []
        if not pois:
            raise ProviderError(f"高德没有找到 POI：{poi_id}")
        return self.normalize_poi(pois[0])

    async def geocode(self, address: str, city: str = "") -> dict[str, Any]:
        params = {"address": address}
        if city:
            params["city"] = city
        data = await self._get("geocode/geo", params)
        geocodes = data.get("geocodes") or []
        if not geocodes:
            raise ProviderError(f"高德无法解析地点：{address}")
        item = geocodes[0]
        return {
            "formatted_address": item.get("formatted_address") or address,
            "province": item.get("province") or "",
            "city": item.get("city") or city,
            "district": item.get("district") or "",
            "adcode": item.get("adcode") or "",
            "location": item.get("location") or "",
        }

    async def weather(self, city: str) -> dict[str, Any]:
        data = await self._get("weather/weatherInfo", {"city": city, "extensions": "all"})
        forecasts = data.get("forecasts") or []
        if not forecasts:
            raise ProviderError(f"高德没有返回 {city} 的天气")
        forecast = forecasts[0]
        return {
            "city": forecast.get("city") or city,
            "report_time": forecast.get("reporttime") or "",
            "casts": forecast.get("casts") or [],
        }

    async def driving_route(self, origin: str, destination: str) -> dict[str, Any]:
        data = await self._get("direction/driving", {"origin": origin, "destination": destination, "extensions": "all"})
        paths = ((data.get("route") or {}).get("paths") or [])
        if not paths:
            raise ProviderError("高德没有返回可用驾车路线")
        path = paths[0]
        route_path: list[list[float]] = []
        for step in path.get("steps") or []:
            for pair in str(step.get("polyline") or "").split(";"):
                try:
                    lng, lat = [float(part) for part in pair.split(",", 1)]
                except (ValueError, TypeError):
                    continue
                route_path.append([lng, lat])
        return {
            "distance_m": int(float(path.get("distance") or 0)),
            "duration_s": int(float(path.get("duration") or 0)),
            "tolls": float(path.get("tolls") or 0),
            "path": route_path,
            "source": SourceRef(
                title="高德驾车路线",
                provider="高德地图",
                url="https://www.amap.com/",
                excerpt=f"{path.get('distance')} 米，约 {path.get('duration')} 秒",
            ).model_dump(mode="json"),
        }

    @staticmethod
    def normalize_poi(poi: dict[str, Any]) -> dict[str, Any]:
        biz = poi.get("biz_ext") or {}
        if not isinstance(biz, dict):
            biz = {}
        photos = []
        for photo in poi.get("photos") or []:
            url = photo.get("url") if isinstance(photo, dict) else ""
            if url:
                photos.append(url)
        return {
            "poi_id": poi.get("id") or "",
            "name": poi.get("name") or "",
            "type": poi.get("type") or "",
            "address": poi.get("address") if isinstance(poi.get("address"), str) else "",
            "location": poi.get("location") or "",
            "distance_m": int(float(poi.get("distance") or 0)),
            "province": poi.get("pname") or "",
            "city": poi.get("cityname") or "",
            "district": poi.get("adname") or "",
            "tel": poi.get("tel") if isinstance(poi.get("tel"), str) else "",
            "website": poi.get("website") if isinstance(poi.get("website"), str) else "",
            "rating": float(biz.get("rating") or 0),
            "cost": float(biz.get("cost") or biz.get("price") or 0),
            "open_time": biz.get("open_time") or "",
            "photos": photos,
            "children": poi.get("children") or [],
            "source": SourceRef(
                title=poi.get("name") or "高德 POI",
                provider="高德地图",
                url="https://www.amap.com/",
                excerpt=poi.get("address") or "",
            ).model_dump(mode="json"),
        }
