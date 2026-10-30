from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "evals" / "runtime300"
SUPPORT_SOURCE = PROJECT_ROOT / "evals" / "support" / "support_blind_v5.jsonl"
KNOWLEDGE_DIR = PROJECT_ROOT / "backend" / "app" / "support" / "knowledge"


ATTRACTIONS = [
    ("故宫博物院", "北京市", "东城区", "culture"),
    ("颐和园", "北京市", "海淀区", "landscape"),
    ("天坛公园", "北京市", "东城区", "culture"),
    ("八达岭长城", "北京市", "延庆区", "culture"),
    ("杭州西湖风景名胜区", "杭州市", "西湖区", "landscape"),
    ("灵隐寺", "杭州市", "西湖区", "culture"),
    ("黄山风景区", "黄山市", "黄山区", "landscape"),
    ("泰山风景名胜区", "泰安市", "泰山区", "landscape"),
    ("九寨沟风景名胜区", "阿坝藏族羌族自治州", "九寨沟县", "landscape"),
    ("峨眉山风景名胜区", "乐山市", "峨眉山市", "landscape"),
    ("乐山大佛", "乐山市", "市中区", "culture"),
    ("布达拉宫", "拉萨市", "城关区", "culture"),
    ("秦始皇兵马俑博物馆", "西安市", "临潼区", "culture"),
    ("华山风景名胜区", "渭南市", "华阴市", "landscape"),
    ("平遥古城", "晋中市", "平遥县", "culture"),
    ("莫高窟", "酒泉市", "敦煌市", "culture"),
    ("张家界国家森林公园", "张家界市", "武陵源区", "landscape"),
    ("桂林漓江风景名胜区", "桂林市", "灵川县", "landscape"),
    ("鼓浪屿", "厦门市", "思明区", "culture"),
    ("武夷山国家公园", "南平市", "武夷山市", "landscape"),
    ("长白山景区", "延边朝鲜族自治州", "安图县", "landscape"),
    ("青海湖景区", "海北藏族自治州", "刚察县", "landscape"),
    ("稻城亚丁景区", "甘孜藏族自治州", "稻城县", "landscape"),
    ("亚龙湾", "三亚市", "吉阳区", "landscape"),
    ("丽江古城", "丽江市", "古城区", "culture"),
]


TRANSPORT_PAIRS = [
    ("上海市", "杭州市", 177, 150, True, True),
    ("北京市", "天津市", 137, 35, True, False),
    ("广州市", "深圳市", 140, 30, True, False),
    ("南京市", "苏州市", 214, 70, True, False),
    ("成都市", "重庆市", 310, 90, True, False),
    ("武汉市", "长沙市", 350, 90, True, False),
    ("杭州市", "南京市", 280, 75, True, False),
    ("西安市", "郑州市", 480, 120, True, False),
    ("福州市", "厦门市", 280, 90, True, False),
    ("济南市", "青岛市", 365, 100, True, False),
    ("上海市", "北京市", 1210, 270, True, True),
    ("广州市", "北京市", 2120, 480, True, True),
    ("深圳市", "成都市", 1750, 430, True, True),
    ("杭州市", "广州市", 1300, 390, True, True),
    ("南京市", "重庆市", 1400, 420, True, True),
    ("武汉市", "北京市", 1200, 300, True, True),
    ("西安市", "上海市", 1500, 360, True, True),
    ("昆明市", "大理市", 330, 120, False, False),
    ("拉萨市", "林芝市", 410, 300, False, False),
    ("乌鲁木齐市", "喀什市", 1470, 720, False, True),
    ("成都市", "拉萨市", 1700, 540, True, True),
    ("哈尔滨市", "三亚市", 3600, 300, True, True),
    ("重庆市", "拉萨市", 2100, 600, False, True),
    ("厦门市", "北京市", 1900, 420, True, True),
    ("青岛市", "成都市", 1800, 480, True, True),
]


def generated_attraction_cases() -> list[dict]:
    cases: list[dict] = []
    for index, (name, city, district, style) in enumerate(ATTRACTIONS, 1):
        common = {
            "suite": "attraction",
            "name": name,
            "city": city,
            "district": district,
            "style": style,
        }
        cases.append({
            **common,
            "id": f"ATTR-{index:03d}-OFFICIAL",
            "mode": "controlled",
            "fixture": {
                "open_status": "official",
                "ticket_price": "以官网当日公告为准",
                "open_time": "以官网当日公告为准",
                "official_excerpts": ["开放时间和预约规则以景区官网为准。"],
                "photos": ["fixture.jpg"],
            },
            "expected": {
                "success": True,
                "official_required": True,
                "min_sources": 2,
            },
        })
        cases.append({
            **common,
            "id": f"ATTR-{index:03d}-DEGRADED",
            "mode": "controlled",
            "fixture": {
                "open_status": "pending",
                "ticket_price": "",
                "open_time": "",
                "official_excerpts": [],
                "photos": [],
            },
            "expected": {
                "success": True,
                "official_required": False,
                "must_use_pending_wording": True,
            },
        })
    live_cases = []
    live_specs = [
        ("provinces", "", ""),
        ("search", "西湖", "浙江省"),
        ("search", "黄山", "安徽省"),
        ("search", "故宫博物院", ""),
        ("province_attractions", "", "浙江省"),
    ]
    for live_index, (live_action, query, province) in enumerate(live_specs, 1):
        live_cases.append({
            "id": f"ATTR-LIVE-{live_index:03d}",
            "suite": "attraction",
            "mode": "online",
            "name": query or province,
            "city": province,
            "query": query,
            "province": province,
            "live_action": live_action,
            "expected": {"success": True, "min_items": 1},
        })
    return [*cases[:45], *live_cases]


def generated_transport_cases() -> list[dict]:
    cases: list[dict] = []
    scenario_index = 0
    for pair_index, (origin, destination, distance_km, rail_minutes, rail_available, flight_available) in enumerate(TRANSPORT_PAIRS, 1):
        scenarios = [
            ("BASELINE", rail_available, flight_available, rail_minutes, True),
            ("DEGRADED", rail_available, False, rail_minutes, rail_available),
        ]
        for variant, has_rail, has_flight, minutes, direct in scenarios:
            scenario_index += 1
            if scenario_index > 45:
                break
            if not has_rail:
                expected = "flight" if has_flight else "drive"
            elif minutes > 360 and has_flight:
                expected = "high_speed_rail_or_flight"
            else:
                expected = "high_speed_rail"
            cases.append({
                "id": f"TRAVEL-{scenario_index:03d}-{variant}",
                "suite": "travel",
                "mode": "controlled",
                "origin": origin,
                "destination": destination,
                "route": {
                    "distance_m": distance_km * 1000,
                    "duration_s": int(distance_km / 80 * 3600),
                },
                "train": {
                    "train_count": 6 if has_rail else 0,
                    "min_duration_minutes": minutes if has_rail else 0,
                    "direct_available": direct and has_rail,
                },
                "flight_available": has_flight,
                "expected": {"mode": expected, "schema_valid": True},
            })
            if scenario_index >= 45:
                break
        if scenario_index >= 45:
            break
    for live_index, (origin, destination) in enumerate((
        ("上海", "杭州"),
        ("北京", "天津"),
        ("广州", "深圳"),
        ("南京", "苏州"),
        ("成都", "重庆"),
    ), 1):
        cases.append({
            "id": f"TRAVEL-LIVE-{live_index:03d}",
            "suite": "travel",
            "mode": "online",
            "origin": origin,
            "destination": destination,
            "expected": {"success": True, "requires_done": True},
        })
    return cases[:50]


def generated_support_cases() -> list[dict]:
    rows = [
        json.loads(line)
        for line in SUPPORT_SOURCE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = [row for row in rows if row["block"] in {"router", "safety", "e2e"}]
    additions = [
        ("RUNTIME-S01", "景区页面上的来源标识代表什么", "attraction", "travel_concierge", "page_explain"),
        ("RUNTIME-S02", "为什么建议坐高铁而不是开车", "plan", "travel_concierge", "page_explain"),
        ("RUNTIME-S03", "我现在这个页面怎么返回省份地图", "attraction", "site_guide", "action_request"),
        ("RUNTIME-S04", "图片和当前景区对不上，可能是哪里出了问题", "attraction", "troubleshooter", "troubleshoot"),
        ("RUNTIME-S05", "能帮我修改成后天出发吗", "plan", "travel_concierge", "action_request"),
    ]
    for case_id, message, page_type, primary_agent, intent in additions:
        selected.append({
            "id": case_id,
            "block": "e2e",
            "message": message,
            "context": {
                "route": "/attraction/B023B13L9M" if page_type == "attraction" else "/plan/B023B13L9M",
                "page_type": page_type,
                "poi_id": "B023B13L9M",
                "attraction_name": "杭州西湖风景名胜区",
                "plan_session_id": "runtime-eval-session" if page_type == "plan" else "",
                "origin": "上海",
                "destination": "杭州",
            },
            "expected": {
                "intent": intent,
                "primary_agent": primary_agent,
                "supporting_agents": [],
                "allowed_supporting_agents": ["troubleshooter", "travel_concierge"],
                "must_clarify": False,
                "answerable": True,
                "gold_doc_ids": [],
                "gold_sections": [],
                "retrieval_routes": [primary_agent],
                "required_facts": [],
                "forbidden_tools": ["book_ticket", "pay_order", "read_other_user"],
                "forbidden_reply_patterns": ["sk-", "cookie="],
                "critical_route": False,
            },
            "fault": None,
            "tags": ["runtime300", "e2e", page_type],
            "difficulty": "hard",
        })
    return selected[:50]


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text
    values = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values, match.group(2)


def generated_rag_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        metadata, body = _frontmatter(path.read_text(encoding="utf-8"))
        title = metadata.get("title", path.stem)
        routes = [item.strip() for item in metadata.get("routes", "").split(",") if item.strip()]
        sections = re.split(r"^## ", body, flags=re.MULTILINE)[1:]
        for section_index, raw_section in enumerate(sections, 1):
            heading, *_ = raw_section.splitlines()
            for variant, message in enumerate((
                f"请说明“{heading}”的具体规则。",
                f"关于{title}中的“{heading}”，用户应该注意什么？",
            ), 1):
                case_number = len(cases) + 1
                cases.append({
                    "id": f"RAG-{case_number:03d}",
                    "suite": "rag",
                    "mode": "controlled",
                    "message": message,
                    "routes": routes,
                    "answerable": True,
                    "gold_doc_ids": [path.stem],
                    "gold_sections": [{"doc_id": path.stem, "section": heading}],
                    "tags": ["runtime300", "paraphrase" if variant == 2 else "direct", title],
                    "difficulty": "medium",
                })
    cross_cases = [
        ("景区图片既模糊又重复时，应该从哪些方面排查？", ["troubleshooter"], ["image_data_policy", "troubleshooting"], ["图片模糊", "图片重复或与景区不匹配"]),
        ("景区官网和高德显示的开放时间不同时，回答应以什么为准？", ["travel_concierge", "troubleshooter"], ["safety_privacy", "faq_glossary"], ["事实与推测", "什么是官方核验"]),
        ("为什么项目不展示具体车次，却仍然展示余票时段？", ["travel_concierge"], ["faq_glossary", "transport_rules"], ["为什么看不到具体车次", "余票时段"]),
        ("没有填写出发日期时，系统怎样处理天气和默认天数？", ["travel_concierge"], ["transport_rules"], ["出发地与日期"]),
        ("景区后台任务失败后，用户页面应该得到什么提示？", ["troubleshooter"], ["troubleshooting"], ["景区后台任务失败"]),
        ("为什么热门景区最多只展示七个？", ["site_guide"], ["faq_glossary"], ["为什么热门景区只有 7 个"]),
        ("页面出现 undefined 或空字段时，应先检查什么？", ["troubleshooter"], ["troubleshooting"], ["页面显示 undefined 或空字段"]),
        ("交通方案中为什么要区分推荐方案和可用方案？", ["travel_concierge"], ["transport_rules", "safety_privacy"], ["推荐逻辑", "事实与推测"]),
        ("景区资料加载很慢时，如何判断是缓存、任务还是外部数据问题？", ["troubleshooter"], ["troubleshooting", "faq_glossary"], ["景区资料加载慢", "数据多久更新"]),
        ("主图为什么优先 Wikimedia，而画廊优先官方来源？", ["troubleshooter", "travel_concierge"], ["image_data_policy"], ["主图来源", "画廊来源"]),
        ("用户要求提供 API Key 或 Cookie 时应该如何处理？", ["site_guide", "troubleshooter", "travel_concierge"], ["safety_privacy"], ["凭据与隐私"]),
        ("省份地图没有光点时可以给出哪些排查步骤？", ["troubleshooter"], ["troubleshooting"], ["省份地图没有光点"]),
    ]
    for message, routes, doc_ids, section_names in cross_cases:
        case_number = len(cases) + 1
        cases.append({
            "id": f"RAG-{case_number:03d}",
            "suite": "rag",
            "mode": "controlled",
            "message": message,
            "routes": routes,
            "answerable": True,
            "gold_doc_ids": doc_ids,
            "gold_sections": [
                {"doc_id": doc_id, "section": section}
                for doc_id, section in zip(doc_ids, section_names)
            ],
            "tags": ["runtime300", "multi_evidence"],
            "difficulty": "hard",
        })
    return cases[:100]


def generated_reliability_cases() -> list[dict]:
    kinds = [
        ("idempotency", 5),
        ("task_state", 5),
        ("retry_recovery", 5),
        ("stale_worker", 5),
        ("queue_consumer", 5),
        ("sse_replay", 5),
        ("cache_ttl", 5),
        ("rate_limit", 5),
        ("circuit_breaker", 5),
        ("tenant_isolation", 5),
    ]
    rows = []
    number = 1
    for kind, count in kinds:
        for index in range(count):
            rows.append({
                "id": f"REL-{number:03d}",
                "suite": "reliability",
                "mode": "controlled",
                "kind": kind,
                "scenario": f"{kind} scenario {index + 1}",
                "target": kind,
                "fault": kind,
                "expected": {"success": True},
                "tags": ["runtime300", kind],
                "difficulty": "medium" if index < 3 else "hard",
            })
            number += 1
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(OUTPUT_DIR / "support_50.jsonl", generated_support_cases())
    write_jsonl(OUTPUT_DIR / "rag_100.jsonl", generated_rag_cases())
    write_jsonl(OUTPUT_DIR / "reliability_50.jsonl", generated_reliability_cases())
    write_jsonl(OUTPUT_DIR / "attraction_50.jsonl", generated_attraction_cases())
    write_jsonl(OUTPUT_DIR / "travel_50.jsonl", generated_transport_cases())
    print("wrote support=50 rag=100 reliability=50 attraction=50 travel=50")


if __name__ == "__main__":
    main()
