from __future__ import annotations

from typing import Any

from ..providers.amap import AmapClient


def _hub(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(item.get("name") or ""),
        "type": str(item.get("type") or ""),
        "city": str(item.get("city") or ""),
        "district": str(item.get("district") or ""),
        "location": str(item.get("location") or ""),
        "distance_m": int(item.get("distance_m") or 0),
        "address": str(item.get("address") or ""),
    }


async def resolve_transport_hubs(
    amap: AmapClient,
    origin: str,
    destination: str,
    *,
    radius_m: int = 120000,
    limit: int = 8,
) -> dict[str, Any]:
    origin_info, destination_info = await _geocode_pair(amap, origin, destination)
    origin_rail, origin_air, destination_rail, destination_air = await _nearby_for_pair(
        amap,
        origin_info["location"],
        destination_info["location"],
        radius_m=radius_m,
        limit=limit,
    )
    return {
        "ok": True,
        "origin": origin_info,
        "destination": destination_info,
        "origin_rail_stations": origin_rail,
        "origin_airports": origin_air,
        "destination_rail_stations": destination_rail,
        "destination_airports": destination_air,
        "candidate_hubs": _candidate_hubs(origin_rail, origin_air, destination_rail, destination_air),
        "source": {"title": "高德地图交通枢纽", "provider": "高德地图", "url": "https://www.amap.com/", "excerpt": "按距离返回附近火车站和机场，仅作为中转候选，不替代12306或航班数据源。"},
    }


async def _geocode_pair(amap: AmapClient, origin: str, destination: str) -> tuple[dict[str, Any], dict[str, Any]]:
    import asyncio

    origin_result, destination_result = await asyncio.gather(
        amap.geocode(origin),
        amap.geocode(destination),
    )
    return origin_result, destination_result


async def _nearby_for_pair(
    amap: AmapClient,
    origin_location: str,
    destination_location: str,
    *,
    radius_m: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    import asyncio

    async def nearby(keyword: str, location: str) -> list[dict[str, Any]]:
        try:
            items = await amap.search_nearby(keyword, location, radius=radius_m, offset=25)
        except Exception:
            return []
        return [_hub(item) for item in sorted(items, key=lambda item: int(item.get("distance_m") or 0)) if item.get("name")][:limit]

    results = await asyncio.gather(
        nearby("火车站", origin_location),
        nearby("机场", origin_location),
        nearby("火车站", destination_location),
        nearby("机场", destination_location),
    )
    return results[0], results[1], results[2], results[3]


def _candidate_hubs(
    origin_rail: list[dict[str, Any]],
    origin_air: list[dict[str, Any]],
    destination_rail: list[dict[str, Any]],
    destination_air: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    candidates: list[dict[str, Any]] = []
    destination_names = {str(item.get("name") or "") for item in [*destination_air, *destination_rail]}
    for item in [*destination_air, *destination_rail, *origin_rail, *origin_air]:
        name = str(item.get("name") or "")
        city = str(item.get("city") or "")
        key = (name, city)
        if not name or key in seen:
            continue
        seen.add(key)
        candidates.append({
            "name": name,
            "city": city,
            "type": item.get("type") or "",
            "distance_m": int(item.get("distance_m") or 0),
            "role": "destination_hub" if name in destination_names else "origin_hub",
        })
    return candidates[:12]
