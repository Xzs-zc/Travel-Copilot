from __future__ import annotations

import heapq
import re
from typing import Any

from ..schemas import FlightPeriod, TicketPeriod

WINDOWS = [
    ("00:00–05:59", 0, 5),
    ("06:00–08:59", 6, 8),
    ("09:00–11:59", 9, 11),
    ("12:00–14:59", 12, 14),
    ("15:00–17:59", 15, 17),
    ("18:00–20:59", 18, 20),
    ("21:00–23:59", 21, 23),
]


def seat_value(value: Any) -> int:
    text = str(value or "").strip()
    if not text or text in {"无", "--", "候补"}:
        return 0
    if text.isdigit():
        return int(text)
    if text in {"有", "充足"}:
        return 5
    return 0


def duration_minutes(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    match = re.fullmatch(r"(\d{1,3}):(\d{1,2})", text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    match = re.search(r"(?:(\d+)\s*小时)?\s*(?:(\d+)\s*分)?", text)
    if not match or not any(match.groups()):
        return 0
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def summarize_trains(trains: list[dict[str, Any]]) -> dict[str, Any]:
    prices: list[float] = []
    durations: list[int] = []
    high_speed_count = 0
    for train in trains:
        for price in (train.get("prices") or {}).values():
            try:
                value = float(price)
            except (TypeError, ValueError):
                continue
            if value > 0:
                prices.append(value)
        value = duration_minutes(train.get("duration"))
        if value > 0:
            durations.append(value)
        code = str(train.get("train_code") or "").upper()
        if code.startswith(("G", "C", "D")):
            high_speed_count += 1
    return {
        "train_count": len(trains),
        "high_speed_count": high_speed_count,
        "has_trains": bool(trains),
        "min_price": min(prices) if prices else 0,
        "min_duration_minutes": min(durations) if durations else 0,
    }


def normalize_place_name(value: Any) -> str:
    text = re.sub(r"[\s\-_/]", "", str(value or ""))
    text = re.sub(r"(壮族自治区|回族自治区|维吾尔自治区|特别行政区|自治区|自治州|地区|省|市|盟|县)$", "", text)
    return text.lower()


def _same_place(left: str, right: str) -> bool:
    return bool(left and right and (left == right or left in right or right in left))


def build_rail_itinerary(train_results: list[dict[str, Any]], origin: str, destination: str) -> dict[str, Any]:
    """Select the best complete railway itinerary across any number of transfers."""
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for item in train_results or []:
        result = item.get("result") or {}
        arguments = item.get("arguments") or {}
        if not result.get("ok"):
            continue
        summary = result.get("train_summary") or {}
        periods = list(result.get("ticket_periods") or [])
        train_count = int(
            result.get("train_count")
            or summary.get("train_count")
            or sum(int(period.get("train_count") or 0) for period in periods)
            or 0
        )
        minutes = int(summary.get("min_duration_minutes") or 0)
        if not minutes:
            durations = [int(period.get("duration_minutes") or 0) for period in periods if int(period.get("duration_minutes") or 0) > 0]
            minutes = min(durations) if durations else 0
        if train_count <= 0:
            continue
        from_name = str(arguments.get("origin") or "")
        to_name = str(arguments.get("destination") or "")
        from_key = normalize_place_name(from_name)
        to_key = normalize_place_name(to_name)
        if not from_key or not to_key:
            continue
        record = {
            "origin": from_name,
            "destination": to_name,
            "origin_key": from_key,
            "destination_key": to_key,
            "minutes": minutes,
            "price": float(
                summary.get("min_price")
                or min(
                    (float(period.get("min_price") or 0) for period in periods if float(period.get("min_price") or 0) > 0),
                    default=0,
                )
            ),
            "train_count": train_count,
            "high_speed_count": int(summary.get("high_speed_count") or 0),
            "periods": periods,
        }
        key = (from_key, to_key)
        previous = records.get(key)
        if previous is None or (record["minutes"], record["price"]) < (previous["minutes"], previous["price"]):
            records[key] = record

    origin_key = normalize_place_name(origin)
    destination_key = normalize_place_name(destination)
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for record in records.values():
        adjacency.setdefault(record["origin_key"], []).append(record)

    # Dijkstra over all queried rail segments. The path length is not capped;
    # the global Agent step/time budget remains the only execution boundary.
    starts = [key for key in adjacency if _same_place(key, origin_key)]
    best: dict[str, tuple[int, float]] = {}
    queue: list[tuple[int, float, str, tuple[dict[str, Any], ...]]] = []
    for start in starts:
        heapq.heappush(queue, (0, 0.0, start, ()))
    result_path: tuple[dict[str, Any], ...] | None = None
    while queue:
        total_minutes, total_price, node, path = heapq.heappop(queue)
        if _same_place(node, destination_key):
            result_path = path
            break
        marker = best.get(node)
        if marker is not None and (total_minutes, total_price) >= marker:
            continue
        best[node] = (total_minutes, total_price)
        for record in adjacency.get(node, []):
            next_node = record["destination_key"]
            next_score = (total_minutes + int(record["minutes"]), total_price + float(record["price"]))
            previous = best.get(next_node)
            if previous is not None and next_score >= previous:
                continue
            heapq.heappush(queue, (next_score[0], next_score[1], next_node, path + (record,)))
    if result_path is None:
        return {
            "available": False,
            "segments": [],
            "total_minutes": 0,
            "min_price": 0,
            "train_count": 0,
            "high_speed_count": 0,
            "direct": False,
        }
    segments = list(result_path)
    return {
        "available": True,
        "segments": segments,
        "total_minutes": sum(item["minutes"] for item in segments),
        "min_price": sum(item["price"] for item in segments),
        "train_count": sum(item["train_count"] for item in segments),
        "high_speed_count": sum(item["high_speed_count"] for item in segments),
        "direct": len(segments) == 1,
    }


def build_itinerary_ticket_periods(itinerary: dict[str, Any]) -> list[TicketPeriod]:
    segments = list(itinerary.get("segments") or [])
    multiple = len(segments) > 1
    periods: list[TicketPeriod] = []
    for segment in segments:
        prefix = f"{segment.get('origin') or ''}→{segment.get('destination') or ''} · " if multiple else ""
        for raw in segment.get("periods") or []:
            period = TicketPeriod.model_validate(raw)
            if period.available_count <= 0:
                continue
            update = {
                "label": f"{prefix}{period.label}",
                "status": "available",
                "seat_summary": [],
                "summary": "该时段有余票",
                "recommended": False,
            }
            if multiple:
                update["summary"] = f"{segment.get('origin') or ''}→{segment.get('destination') or ''}：该时段有余票"
            periods.append(period.model_copy(update=update))
    return periods


def build_flight_periods(flights: list[dict[str, Any]]) -> list[FlightPeriod]:
    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in WINDOWS}
    for flight in flights:
        get_value = flight.get if isinstance(flight, dict) else lambda key, default=None: getattr(flight, key, default)
        raw_time = str(get_value("departure_time") or "")
        match = re.search(r"(\d{1,2}):(\d{2})", raw_time)
        if not match:
            continue
        hour = int(match.group(1))
        for label, start_hour, end_hour in WINDOWS:
            if start_hour <= hour <= end_hour:
                grouped[label].append(flight)
                break
    periods: list[FlightPeriod] = []
    for label, _, _ in WINDOWS:
        options = grouped[label]
        if not options:
            continue
        def value(item: Any, key: str) -> Any:
            return item.get(key) if isinstance(item, dict) else getattr(item, key, None)

        prices = [float(value(item, "price") or 0) for item in options if float(value(item, "price") or 0) > 0]
        durations = [int(value(item, "duration_minutes") or 0) for item in options if int(value(item, "duration_minutes") or 0) > 0]
        stops = [int(value(item, "stops") or 0) for item in options]
        periods.append(FlightPeriod(
            label=label,
            start_time=label.split("–", 1)[0],
            end_time=label.split("–", 1)[1],
            flight_count=len(options),
            min_price=min(prices) if prices else 0,
            duration_minutes=min(durations) if durations else 0,
            stops=min(stops) if stops else 0,
            summary="该时段有余票",
        ))
    return periods


def summarize_rail_itinerary(itinerary: dict[str, Any]) -> dict[str, Any]:
    available = bool(itinerary.get("available"))
    return {
        "train_count": int(itinerary.get("train_count") or 0),
        "high_speed_count": int(itinerary.get("high_speed_count") or 0),
        "has_trains": available,
        "min_price": float(itinerary.get("min_price") or 0),
        "min_duration_minutes": int(itinerary.get("total_minutes") or 0),
        "direct_available": bool(itinerary.get("direct")),
    }


def build_ticket_periods(trains: list[dict[str, Any]]) -> list[TicketPeriod]:
    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in WINDOWS}
    for train in trains:
        start_time = str(train.get("start_time") or "")
        try:
            hour = int(start_time.split(":", 1)[0])
        except (ValueError, IndexError):
            continue
        for label, start_hour, end_hour in WINDOWS:
            if start_hour <= hour <= end_hour:
                grouped[label].append(train)
                break
    periods: list[TicketPeriod] = []
    for label, _, _ in WINDOWS:
        window_trains = grouped[label]
        if not window_trains:
            continue
        seat_totals: dict[str, int] = {}
        available_trains = 0
        seat_units = 0
        prices: list[float] = []
        durations: list[int] = []
        for train in window_trains:
            seats = train.get("seats") or {}
            train_available = False
            for seat_name, value in seats.items():
                count = seat_value(value)
                if count > 0:
                    train_available = True
                    seat_units += count
                    seat_totals[seat_name] = seat_totals.get(seat_name, 0) + count
            if train_available:
                available_trains += 1
            for price in (train.get("prices") or {}).values():
                try:
                    value = float(price)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    prices.append(value)
            value = duration_minutes(train.get("duration"))
            if value > 0:
                durations.append(value)
        if available_trains == 0:
            continue
        periods.append(TicketPeriod(
            label=label,
            start_time=label.split("–", 1)[0],
            end_time=label.split("–", 1)[1],
            train_count=len(window_trains),
            available_count=available_trains,
            status="available",
            seat_summary=[],
            summary="该时段有余票",
            min_price=min(prices) if prices else 0,
            duration_minutes=min(durations) if durations else 0,
            recommended=False,
        ))
    return periods
