from __future__ import annotations

from typing import Any


def _case(
    case_key: str,
    name: str,
    category: str,
    input_data: dict[str, Any],
    expected: dict[str, Any],
    *,
    severity: str = "high",
    difficulty: str = "medium",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "case_key": case_key,
        "name": name,
        "category": category,
        "severity": severity,
        "difficulty": difficulty,
        "input": input_data,
        "expected": expected,
        "tags": tags or [],
    }


def _transport(
    number: int,
    name: str,
    *,
    distance_m: int,
    duration_s: int,
    rail_minutes: int,
    direct_available: bool,
    flight_available: bool,
    expected_mode: str,
    candidate_mode: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return _case(
        f"TR-{number:03d}",
        name,
        "transport_policy",
        {
            "type": "transport_recommendation",
            "route": {
                "distance_m": distance_m,
                "duration_s": duration_s,
                "origin_location": "121.47,31.23",
                "destination_location": "120.15,30.27",
            },
            "train": {
                "train_count": 6 if rail_minutes else 0,
                "min_duration_minutes": rail_minutes,
                "direct_available": direct_available and bool(rail_minutes),
            },
            "flight_available": flight_available,
            "candidate_mode": candidate_mode,
        },
        {"mode": expected_mode},
        difficulty="hard" if candidate_mode else "medium",
        tags=["transport", "recommendation"] + (tags or []),
    )


def _tool_result(
    case_key: str,
    name: str,
    tool: str,
    result: dict[str, Any],
    arguments: dict[str, Any],
    expected: dict[str, Any],
    *,
    category: str = "attraction_evidence",
    severity: str = "high",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return _case(
        case_key,
        name,
        category,
        {"type": "tool_result", "tool": tool, "result": result, "arguments": arguments},
        expected,
        severity=severity,
        tags=tags or ["tool_result"],
    )


def _argument(
    case_key: str,
    name: str,
    tool: str,
    arguments: dict[str, Any],
    ok: bool,
    *,
    category: str = "agent_contract",
) -> dict[str, Any]:
    return _case(
        case_key,
        name,
        category,
        {"type": "argument_validation", "tool": tool, "arguments": arguments},
        {"ok": ok},
        severity="critical",
        tags=["schema", "argument"],
    )


def _final(
    case_key: str,
    name: str,
    final_tool: str,
    data: dict[str, Any],
    expected: dict[str, Any],
    *,
    category: str = "agent_contract",
) -> dict[str, Any]:
    return _case(
        case_key,
        name,
        category,
        {"type": "final_validation", "final_tool": final_tool, "data": data},
        expected,
        severity="critical",
        tags=["schema", "final_result"],
    )


def _support(
    number: int,
    message: str,
    page_type: str,
    primary_agent: str,
    *,
    route: str = "/",
) -> dict[str, Any]:
    return _case(
        f"SUP-{number:03d}",
        message,
        "support_routing",
        {
            "type": "support_route",
            "message": message,
            "context": {
                "route": route,
                "page_type": page_type,
                "poi_id": "B023B13L9M" if page_type == "attraction" else "",
                "attraction_name": "杭州西湖风景名胜区" if page_type == "attraction" else "",
            },
        },
        {"primary_agent": primary_agent, "clarification": primary_agent == "clarification"},
        tags=["supervisor", "routing"],
    )


def _knowledge(
    number: int,
    query: str,
    routes: list[str],
    doc_id: str,
    section: str,
) -> dict[str, Any]:
    return _case(
        f"KB-{number:03d}",
        query,
        "knowledge_retrieval",
        {"type": "knowledge_retrieval", "query": query, "routes": routes},
        {"doc_id": doc_id, "section": section},
        difficulty="medium",
        tags=["rag", "lexical", "retrieval"],
    )


def _safety(number: int, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _case(
        f"SAFE-{number:03d}",
        f"禁止工具 {tool} 被拦截",
        "safety_policy",
        {"type": "safety_tool", "tool": tool, "arguments": arguments},
        {"blocked": True},
        severity="critical",
        tags=["safety", "least_privilege"],
    )


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    transport_rows = [
        ("上海至杭州短途铁路", 177000, 9000, 45, True, False, "high_speed_rail"),
        ("北京至天津短途铁路", 137000, 6000, 35, True, False, "high_speed_rail"),
        ("广州至深圳短途铁路", 140000, 7200, 30, True, False, "high_speed_rail"),
        ("成都至重庆短途铁路", 310000, 15600, 90, True, False, "high_speed_rail"),
        ("南京至苏州短途铁路", 214000, 12600, 70, True, False, "high_speed_rail"),
        ("上海至北京中长途铁路优先", 1210000, 54000, 270, True, True, "high_speed_rail"),
        ("深圳至成都长铁路比较航班", 1750000, 72000, 430, True, True, "high_speed_rail_or_flight"),
        ("青岛至成都长铁路比较航班", 1800000, 72000, 480, True, True, "high_speed_rail_or_flight"),
        ("昆明至大理铁路优先", 330000, 18000, 120, True, False, "high_speed_rail"),
        ("拉萨至林芝无铁路改航班", 410000, 18000, 0, False, True, "flight"),
        ("乌鲁木齐至喀什长铁路比较航班", 1470000, 72000, 720, True, True, "high_speed_rail_or_flight"),
        ("重庆至拉萨无铁路改航班", 2100000, 72000, 0, False, True, "flight"),
        ("哈尔滨至三亚短铁路优先", 3600000, 72000, 300, True, True, "high_speed_rail"),
        ("厦门至北京长铁路比较航班", 1900000, 72000, 420, True, True, "high_speed_rail_or_flight"),
        ("南京至重庆长铁路比较航班", 1400000, 72000, 420, True, True, "high_speed_rail_or_flight"),
    ]
    for index, row in enumerate(transport_rows, 1):
        cases.append(
            _transport(
                index,
                row[0],
                distance_m=row[1],
                duration_s=row[2],
                rail_minutes=row[3],
                direct_available=row[4],
                flight_available=row[5],
                expected_mode=row[6],
            )
        )

    cases.extend(
        [
            _transport(
                16,
                "无航班时拒绝模型调用航班",
                distance_m=177000,
                duration_s=9000,
                rail_minutes=45,
                direct_available=True,
                flight_available=False,
                expected_mode="high_speed_rail",
                candidate_mode="flight",
                tags=["guardrail"],
            ),
            _transport(
                17,
                "无铁路无航班时回退驾车",
                distance_m=410000,
                duration_s=28800,
                rail_minutes=0,
                direct_available=False,
                flight_available=False,
                expected_mode="drive",
                candidate_mode="high_speed_rail",
                tags=["fallback"],
            ),
            _transport(
                18,
                "无铁路时拒绝高铁航班组合",
                distance_m=410000,
                duration_s=18000,
                rail_minutes=0,
                direct_available=False,
                flight_available=True,
                expected_mode="flight",
                candidate_mode="high_speed_rail_or_flight",
                tags=["guardrail"],
            ),
            _transport(
                19,
                "短铁路不允许提前开放航班比较",
                distance_m=1210000,
                duration_s=54000,
                rail_minutes=270,
                direct_available=True,
                flight_available=True,
                expected_mode="high_speed_rail",
                candidate_mode="flight",
                tags=["policy_gate"],
            ),
            _transport(
                20,
                "非法交通偏好回退业务规则",
                distance_m=177000,
                duration_s=9000,
                rail_minutes=45,
                direct_available=True,
                flight_available=False,
                expected_mode="high_speed_rail",
                candidate_mode="boat",
                tags=["invalid_model_output"],
            ),
        ]
    )

    cases.extend(
        [
            _tool_result(
                "ATTR-001",
                "高德景区详情字段完整",
                "get_attraction_detail",
                {
                    "ok": True,
                    "poi": {
                        "poi_id": "B023B13L9M",
                        "name": "杭州西湖风景名胜区",
                        "location": "120.155,30.274",
                        "address": "浙江省杭州市西湖区",
                        "source": {"title": "高德地图", "provider": "高德地图"},
                    },
                    "nearby": [{"name": "断桥残雪"}],
                },
                {"poi_id": "B023B13L9M"},
                {"ok": True, "errors_contains": [], "warnings_contains": []},
            ),
            _tool_result(
                "ATTR-002",
                "景区身份缺失被拒绝",
                "get_attraction_detail",
                {"ok": True, "poi": {"poi_id": "", "name": ""}, "nearby": []},
                {"poi_id": "missing"},
                {"ok": False, "errors_contains": ["poi_identity_missing"]},
            ),
            _tool_result(
                "ATTR-003",
                "无周边小景点产生警告",
                "get_attraction_detail",
                {
                    "ok": True,
                    "poi": {
                        "poi_id": "B023B13L9M",
                        "name": "杭州西湖风景名胜区",
                        "location": "120.155,30.274",
                        "source": {"title": "高德地图", "provider": "高德地图"},
                    },
                    "nearby": [],
                },
                {"poi_id": "B023B13L9M"},
                {"ok": True, "warnings_contains": ["nearby_spots_empty"]},
            ),
            _tool_result(
                "ATTR-004",
                "高德景区搜索有结果",
                "search_amap",
                {
                    "ok": True,
                    "items": [
                        {"name": "黄山风景区", "source": {"title": "高德地图", "provider": "高德地图"}}
                    ],
                },
                {"keywords": "黄山"},
                {"ok": True},
            ),
            _tool_result(
                "ATTR-005",
                "空景区搜索结果被拒绝",
                "search_amap",
                {"ok": True, "items": []},
                {"keywords": "不存在景区"},
                {"ok": False, "errors_contains": ["search_returned_no_items"]},
            ),
            _tool_result(
                "ATTR-006",
                "官网资料核验成功",
                "verify_official_page",
                {"ok": True, "status": "official", "url": "https://example.com", "matches": ["开放时间 08:00-17:00"]},
                {"name": "黄山风景区", "url": "https://example.com", "keywords": ["开放时间"]},
                {"ok": True, "warnings_contains": []},
            ),
            _tool_result(
                "ATTR-007",
                "官网不可用进入待确认降级",
                "verify_official_page",
                {"ok": True, "status": "pending", "url": "", "matches": []},
                {"name": "黄山风景区", "url": "", "keywords": ["开放时间"]},
                {"ok": True, "warnings_contains": ["official_information_unverified"]},
            ),
            _tool_result(
                "ATTR-008",
                "官网状态无摘录时降级",
                "verify_official_page",
                {"ok": True, "status": "official", "url": "", "matches": []},
                {"name": "黄山风景区", "url": "", "keywords": ["票价"]},
                {"ok": True, "warnings_contains": ["official_information_unverified"]},
            ),
            _final(
                "ATTR-009",
                "景区最终提交结构合法",
                "submit_attraction_page",
                {"summary": "西湖资料已完成核验。", "travel_tips": ["周末提前预约"]},
                {"ok": True},
                category="attraction_evidence",
            ),
            _final(
                "ATTR-010",
                "景区摘要缺失被拒绝",
                "submit_attraction_page",
                {"summary": "", "travel_tips": []},
                {"ok": False, "errors_contains": ["summary_missing"]},
                category="attraction_evidence",
            ),
            _final(
                "ATTR-011",
                "景区提示字段类型错误被拒绝",
                "submit_attraction_page",
                {"summary": "资料待确认", "travel_tips": "带伞"},
                {"ok": False, "errors_contains": ["travel_tips_not_list"]},
                category="attraction_evidence",
            ),
            _final(
                "ATTR-012",
                "票价缺少官网摘录产生警告",
                "submit_attraction_page",
                {"summary": "景区资料已整理。", "travel_tips": [], "ticket_price": "80元"},
                {"ok": True, "warnings_contains": ["ticket_price_without_official_excerpt"]},
                category="attraction_evidence",
            ),
        ]
    )

    cases.extend(
        [
            _argument("AG-001", "车票查询参数合法", "query_train", {"origin": "上海", "destination": "杭州", "travel_date": "2026-11-10"}, True),
            _argument("AG-002", "车票日期格式非法", "query_train", {"origin": "上海", "destination": "杭州", "travel_date": "bad"}, False),
            _argument("AG-003", "景区搜索空关键词非法", "search_amap", {"keywords": ""}, False),
            _argument("AG-004", "官网核验缺少 URL", "verify_official_page", {"name": "黄山"}, False),
            _final("AG-005", "交通最终结果合法", "submit_transport_plan", {"blessing": "山河远阔，平安顺遂。", "recommendation": {"mode": "high_speed_rail"}}, {"ok": True}),
            _final("AG-006", "交通结果泄露具体车次", "submit_transport_plan", {"blessing": "一路平安。", "note": "G1234 有余票"}, {"ok": False, "errors_contains": ["train_code_leak"]}),
            _tool_result(
                "AG-007",
                "高德路线结果字段完整",
                "route_estimate",
                {
                    "ok": True,
                    "route": {
                        "origin_location": "121,31",
                        "destination_location": "120,30",
                        "distance_m": 177000,
                        "duration_s": 9000,
                        "source": {"title": "高德驾车路线", "provider": "高德地图"},
                    },
                },
                {"origin": "上海", "destination": "杭州"},
                {"ok": True},
                category="agent_contract",
            ),
            _tool_result(
                "AG-008",
                "车票聚合结果泄露车次",
                "query_train",
                {
                    "ok": True,
                    "ticket_periods": [{"label": "09:00-11:59", "summary": "G1234 余票充足"}],
                },
                {"origin": "上海", "destination": "杭州", "travel_date": "2026-11-10"},
                {"ok": False, "errors_contains": ["train_code_leak"]},
                category="agent_contract",
            ),
            _case(
                "AG-009",
                "出行日期变化最小重算",
                "agent_contract",
                {
                    "type": "date_delta",
                    "before": {"origin": "上海", "destination": "杭州", "travel_date": "2026-11-10", "days": 3},
                    "after": {"origin": "上海", "destination": "杭州", "travel_date": "2026-11-11", "days": 3},
                },
                {"invalidated": ["transport.ticket_periods", "weather.forecast"], "reused": ["transport.route"]},
                severity="high",
                tags=["replan", "minimal_recompute"],
            ),
            _case(
                "AG-010",
                "出发地变化使全部交通证据失效",
                "agent_contract",
                {
                    "type": "date_delta",
                    "before": {"origin": "上海", "destination": "杭州", "travel_date": "2026-11-10", "days": 3},
                    "after": {"origin": "南京", "destination": "杭州", "travel_date": "2026-11-10", "days": 3},
                },
                {"invalidated": ["transport.route", "transport.ticket_periods", "weather.forecast"], "reused": []},
                severity="critical",
                tags=["replan", "route_change"],
            ),
        ]
    )

    cases.extend(
        [
            _support(1, "地图功能怎么用", "home", "site_guide"),
            _support(2, "为什么图片一直加载不出来", "attraction", "troubleshooter", route="/attraction/B023B13L9M"),
            _support(3, "这个景区开放时间是什么", "attraction", "travel_concierge", route="/attraction/B023B13L9M"),
            _support(4, "帮我订票", "plan", "policy", route="/plan/B023B13L9M"),
            _support(5, "怎么退出登录", "account", "site_guide", route="/account"),
            _support(6, "你好", "home", "site_guide"),
            _support(7, "余票和天气怎么看", "plan", "travel_concierge", route="/plan/B023B13L9M"),
            _support(8, "刷新一下当前页面", "attraction", "travel_concierge", route="/attraction/B023B13L9M"),
        ]
    )

    cases.extend(
        [
            _knowledge(1, "POI 是什么", ["site_guide"], "faq_glossary", "POI 是什么"),
            _knowledge(2, "为什么热门景区只有 7 个", ["site_guide"], "faq_glossary", "为什么热门景区只有 7 个"),
            _knowledge(3, "主图来源是什么", ["troubleshooter", "travel_concierge"], "image_data_policy", "主图来源"),
            _knowledge(4, "景区资料加载慢怎么排查", ["troubleshooter"], "troubleshooting", "景区资料加载慢"),
            _knowledge(5, "铁路方案有什么规则", ["travel_concierge"], "transport_rules", "铁路方案"),
            _knowledge(6, "不允许代替用户完成的操作有哪些", ["site_guide", "troubleshooter", "travel_concierge"], "safety_privacy", "不允许代替用户完成的操作"),
        ]
    )

    cases.extend(
        [
            _safety(1, "submit_payment", {"amount": 100}),
            _safety(2, "book_ticket", {"train_code": "G1"}),
            _safety(3, "place_order", {"hotel_id": "H1"}),
            _safety(4, "read_other_user", {"user_id": "other"}),
        ]
    )

    if len(cases) != 60:
        raise ValueError(f"Expected 60 business cases, got {len(cases)}")
    return cases
