from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote

import httpx


class WikimediaImageProvider:
    API_URL = "https://commons.wikimedia.org/w/api.php"
    WIKIDATA_API = "https://www.wikidata.org/w/api.php"
    USER_AGENT = "TravelCopilot/1.0 (https://example.local; local personal travel assistant)"

    def __init__(self) -> None:
        self._cache: dict[str, list[dict[str, Any]]] = {}

    async def search(
        self,
        name: str,
        city: str = "",
        limit: int = 8,
        width: int = 2400,
        expected_location: str = "",
        allow_generic: bool = False,
    ) -> list[dict[str, Any]]:
        cache_key = f"exact|{name}|{city}|{expected_location}|{limit}|{width}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        exact = await self._search_exact(name, city, width, expected_location)
        if allow_generic:
            generic = await self._search_commons(name, city, limit, width)
            seen = {str(item.get("url") or "") for item in exact}
            exact.extend(item for item in generic if str(item.get("url") or "") not in seen)
        exact = exact[:limit]
        self._cache[cache_key] = exact
        return exact

    async def _search_exact(self, name: str, city: str, width: int, expected_location: str) -> list[dict[str, Any]]:
        queries = [name, f"{city} {name}".strip()]
        ids: list[str] = []
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": self.USER_AGENT}) as client:
            for query in queries:
                response = await client.get(
                    self.WIKIDATA_API,
                    params={
                        "action": "wbsearchentities",
                        "search": query,
                        "language": "zh",
                        "uselang": "zh",
                        "type": "item",
                        "limit": "10",
                        "format": "json",
                    },
                )
                response.raise_for_status()
                for item in response.json().get("search", []):
                    if item.get("id") and item["id"] not in ids:
                        ids.append(item["id"])
                if ids:
                    break
            if not ids:
                return []
            response = await client.get(
                self.WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(ids[:12]),
                    "props": "claims|labels|descriptions",
                    "languages": "zh|en",
                    "format": "json",
                },
            )
            response.raise_for_status()
            entities = response.json().get("entities", {})
        point = self._parse_location(expected_location)
        candidates: list[dict[str, Any]] = []
        normalized_name = self._normalize(name)
        for qid, entity in entities.items():
            labels = entity.get("labels", {})
            descriptions = entity.get("descriptions", {})
            label = (labels.get("zh") or labels.get("en") or {}).get("value", "")
            description = (descriptions.get("zh") or descriptions.get("en") or {}).get("value", "")
            normalized_label = self._normalize(label)
            if not self._name_matches(normalized_name, normalized_label):
                continue
            if any(word in description for word in ("法规", "条例", "管理委员会", "公司", "电影", "电视剧")):
                continue
            claims = entity.get("claims", {})
            coordinates = self._claim_value(claims, "P625")
            coordinate_score = 0
            if coordinates and point:
                distance = self._distance_km(point, (float(coordinates["longitude"]), float(coordinates["latitude"])))
                if distance <= 3:
                    coordinate_score = 60
                elif distance <= 15:
                    coordinate_score = 25
                else:
                    continue
            elif point and coordinates:
                continue
            description_score = 20 if any(word in description for word in ("湖", "山", "景区", "风景", "公园", "寺", "塔", "博物院", "博物馆", "古镇")) else 0
            images = self._claim_values(claims, "P18")
            base_score = coordinate_score + description_score + (100 if normalized_label == normalized_name else 70)
            for image_name in images:
                image_url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(str(image_name))}?width={width}"
                candidates.append({
                    "title": label or name,
                    "url": image_url,
                    "page_url": f"https://www.wikidata.org/wiki/{qid}",
                    "width": width,
                    "height": 0,
                    "mime": "image/jpeg",
                    "score": base_score,
                    "qid": qid,
                })
            category_name = self._claim_value(claims, "P373")
            if category_name:
                for item in await self._category_images(category_name, width=width, limit=12):
                    candidates.append({**item, "score": base_score - 5})
        return sorted(candidates, key=lambda item: item["score"], reverse=True)

    async def _category_images(self, category_name: str, *, width: int, limit: int = 12) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": self.USER_AGENT}) as client:
            response = await client.get(
                self.API_URL,
                params={
                    "action": "query",
                    "generator": "categorymembers",
                    "gcmtitle": f"Category:{category_name}",
                    "gcmtype": "file",
                    "gcmlimit": str(limit),
                    "prop": "imageinfo",
                    "iiprop": "url|size|mime",
                    "iiurlwidth": str(width),
                    "format": "json",
                },
            )
            response.raise_for_status()
            data = response.json()
        results = []
        for page in (data.get("query", {}).get("pages", {}) or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            image_width = int(info.get("width") or 0)
            image_height = int(info.get("height") or 0)
            mime = str(info.get("mime") or "")
            image_url = str(info.get("thumburl") or info.get("url") or "")
            if image_width < 800 or image_height < 500 or mime not in {"image/jpeg", "image/png", "image/webp"}:
                continue
            results.append({
                "title": page.get("title") or category_name,
                "url": image_url,
                "page_url": f"https://commons.wikimedia.org/wiki/{str(page.get('title') or '').replace(' ', '_')}",
                "width": image_width,
                "height": image_height,
                "mime": mime,
                "qid": "",
                "category": category_name,
            })
        return results

    async def _search_commons(self, name: str, city: str, limit: int, width: int) -> list[dict[str, Any]]:
        query = " ".join(part for part in (name, city, "China") if part).strip()
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": self.USER_AGENT}) as client:
            response = await client.get(
                self.API_URL,
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrnamespace": "6",
                    "gsrlimit": str(limit),
                    "prop": "imageinfo",
                    "iiprop": "url|size|mime",
                    "iiurlwidth": str(width),
                    "format": "json",
                },
            )
            response.raise_for_status()
            data = response.json()
        candidates = []
        for page in (data.get("query", {}).get("pages", {}) or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            image_width = int(info.get("width") or 0)
            image_height = int(info.get("height") or 0)
            mime = str(info.get("mime") or "")
            image_url = str(info.get("thumburl") or info.get("url") or "")
            if image_width < 1200 or image_height < 700 or mime not in {"image/jpeg", "image/png", "image/webp"}:
                continue
            if image_width / max(image_height, 1) < 1.2:
                continue
            candidates.append({
                "title": page.get("title") or name,
                "url": image_url,
                "page_url": f"https://commons.wikimedia.org/wiki/{str(page.get('title') or '').replace(' ', '_')}",
                "width": image_width,
                "height": image_height,
                "mime": mime,
                "score": image_width * image_height,
            })
        return sorted(candidates, key=lambda item: item["score"], reverse=True)

    @staticmethod
    def _claim_values(claims: dict[str, Any], property_id: str) -> list[Any]:
        values = []
        for claim in claims.get(property_id, []):
            value = (claim.get("mainsnak", {}).get("datavalue", {}) or {}).get("value")
            if value is not None:
                values.append(value)
        return values

    def _claim_value(self, claims: dict[str, Any], property_id: str) -> Any:
        values = self._claim_values(claims, property_id)
        return values[0] if values else None

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    @staticmethod
    def _name_matches(left: str, right: str) -> bool:
        if not left or not right:
            return False
        return left == right or left in right or right in left or len(set(left) & set(right)) >= 2

    @staticmethod
    def _parse_location(value: str) -> tuple[float, float] | None:
        try:
            lng, lat = [float(part) for part in value.split(",", 1)]
            return lng, lat
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _distance_km(left: tuple[float, float], right: tuple[float, float]) -> float:
        lng1, lat1 = left
        lng2, lat2 = right
        radius = 6371
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lng2 - lng1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * radius * math.asin(math.sqrt(a))
