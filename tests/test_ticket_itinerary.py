from backend.app.agent.ticket_periods import (
    build_itinerary_ticket_periods,
    build_rail_itinerary,
    summarize_rail_itinerary,
)


def period(label, minutes, price=100):
    return {
        "label": label,
        "start_time": label.split("–", 1)[0],
        "end_time": label.split("–", 1)[1],
        "train_count": 2,
        "available_count": 1,
        "status": "available",
        "seat_summary": ["二等座"],
        "summary": f"{label} 有余票",
        "min_price": price,
        "duration_minutes": minutes,
    }


def test_builds_complete_transfer_itinerary_and_all_segment_periods():
    results = [
        {"arguments": {"origin": "深圳", "destination": "崇左市"}, "result": {"ok": False, "error": "没有可购车次"}},
        {
            "arguments": {"origin": "深圳", "destination": "南宁"},
            "result": {
                "ok": True,
                "train_count": 3,
                "train_summary": {"train_count": 3, "high_speed_count": 3, "min_duration_minutes": 214, "min_price": 324.5},
                "ticket_periods": [period("15:00–17:59", 214, 324.5), period("18:00–20:59", 227, 324.5)],
            },
        },
        {
            "arguments": {"origin": "南宁", "destination": "崇左市"},
            "result": {
                "ok": True,
                "train_count": 23,
                "train_summary": {"train_count": 23, "high_speed_count": 22, "min_duration_minutes": 30, "min_price": 19.5},
                "ticket_periods": [period("15:00–17:59", 35, 45), period("18:00–20:59", 30, 19.5), period("21:00–23:59", 35, 45)],
            },
        },
    ]
    itinerary = build_rail_itinerary(results, "深圳", "崇左市")
    assert itinerary["available"] is True
    assert itinerary["total_minutes"] == 244
    assert [segment["origin"] for segment in itinerary["segments"]] == ["深圳", "南宁"]
    periods = build_itinerary_ticket_periods(itinerary)
    assert len(periods) == 5
    assert periods[0].label == "深圳→南宁 · 15:00–17:59"
    assert periods[-1].label == "南宁→崇左市 · 21:00–23:59"
    summary = summarize_rail_itinerary(itinerary)
    assert summary["min_duration_minutes"] == 244
    assert summary["train_count"] == 26


def test_builds_three_segment_itinerary_without_transfer_limit():
    results = []
    for index, (origin, destination) in enumerate([("A", "B"), ("B", "C"), ("C", "D")], start=1):
        results.append({
            "arguments": {"origin": origin, "destination": destination},
            "result": {
                "ok": True,
                "train_count": 1,
                "train_summary": {"train_count": 1, "high_speed_count": 1, "min_duration_minutes": 60, "min_price": index * 100},
                "ticket_periods": [period("09:00–11:59", 60, index * 100)],
            },
        })
    itinerary = build_rail_itinerary(results, "A", "D")
    assert itinerary["available"] is True
    assert itinerary["direct"] is False
    assert itinerary["total_minutes"] == 180
    assert [segment["destination"] for segment in itinerary["segments"]] == ["B", "C", "D"]
