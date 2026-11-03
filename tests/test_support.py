from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.auth import hash_password
from backend.app.db import Base, Repository
from backend.app.support.intent import SupportIntent, SupportIntentRecognizer, SupportIntentResult
from backend.app.support.schemas import SupportPageContext
from backend.app.support.service import SupportService
from backend.app.support.skill_registry import SupportSkillRegistry, skill_registry


@pytest.mark.asyncio
async def test_support_intent_and_supervisor_routing():
    recognizer = SupportIntentRecognizer(allow_llm=False)
    context = SupportPageContext(
        route="/attraction/B02190A3JB",
        page_type="attraction",
        poi_id="B02190A3JB",
        attraction_name="黄山风景区",
    )
    result = await recognizer.recognize("为什么图片不显示，顺便说下开放时间", context)

    assert result.intent == SupportIntent.TROUBLESHOOT
    assert result.secondary_intent == SupportIntent.PAGE_EXPLAIN

    service = SupportService(session=None, allow_llm=False)
    route = service._route(result, context)
    assert route.primary_agent == "troubleshooter"
    assert route.supporting_agents == ["travel_concierge"]


@pytest.mark.asyncio
async def test_support_intent_feedback_and_question_fallback():
    recognizer = SupportIntentRecognizer(allow_llm=False)
    feedback = await recognizer.recognize("你不太行", SupportPageContext(page_type="plan"))
    assert feedback.intent == SupportIntent.FEEDBACK

    question = await recognizer.recognize("我可以坐飞机吗", SupportPageContext(page_type="plan"))
    assert question.intent == SupportIntent.PAGE_EXPLAIN
    assert question.source == "rules"


@pytest.mark.asyncio
async def test_low_confidence_without_llm_requests_clarification():
    recognizer = SupportIntentRecognizer(allow_llm=False)
    result = await recognizer.recognize("嗯", SupportPageContext(page_type="home"))

    assert result.intent == SupportIntent.UNKNOWN
    assert result.source == "clarification"
    route = SupportService(session=None, allow_llm=False)._route(
        result,
        SupportPageContext(page_type="home"),
    )
    assert route.primary_agent == "clarification"


@pytest.mark.asyncio
async def test_low_confidence_llm_result_requests_clarification():
    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"intent":"site_guide","confidence":0.4,"entities":{},"reason":"不确定"}'
                        )
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    recognizer = SupportIntentRecognizer(client=client, allow_llm=True)
    result = await recognizer.recognize("嗯", SupportPageContext(page_type="home"))

    assert result.intent == SupportIntent.UNKNOWN
    assert result.source == "clarification"


@pytest.mark.asyncio
async def test_nonexplicit_smalltalk_from_llm_is_clarified():
    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"intent":"smalltalk","confidence":0.9,"entities":{},"reason":"问候"}'
                        )
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    recognizer = SupportIntentRecognizer(client=client, allow_llm=True)
    result = await recognizer.recognize("嗯", SupportPageContext(page_type="home"))

    assert result.intent == SupportIntent.UNKNOWN
    assert result.source == "clarification"


@pytest.mark.asyncio
async def test_site_concept_routes_to_site_guide_even_on_attraction_page():
    recognizer = SupportIntentRecognizer(allow_llm=False)
    context = SupportPageContext(page_type="attraction", poi_id="B02190A3JB")
    result = await recognizer.recognize("什么是官方核验", context)
    route = SupportService(session=None, allow_llm=False)._route(result, context)

    assert route.primary_agent == "site_guide"


@pytest.mark.asyncio
async def test_sensitive_and_factual_phrases_route_correctly():
    recognizer = SupportIntentRecognizer(allow_llm=False)
    service = SupportService(session=None, allow_llm=False)
    cases = [
        ("我要退票", "plan", SupportIntent.OUT_OF_SCOPE, "policy"),
        ("查看另一个用户的消息", "account", SupportIntent.OUT_OF_SCOPE, "policy"),
        ("照片重复了怎么处理", "attraction", SupportIntent.TROUBLESHOOT, "troubleshooter"),
        ("景点几点开放", "attraction", SupportIntent.PAGE_EXPLAIN, "travel_concierge"),
    ]
    for message, page_type, expected_intent, expected_agent in cases:
        context = SupportPageContext(page_type=page_type, poi_id="TEST")
        result = await recognizer.recognize(message, context)
        route = service._route(result, context)
        assert result.intent == expected_intent
        assert route.primary_agent == expected_agent


@pytest.mark.asyncio
async def test_domain_rule_prevents_llm_site_guide_override():
    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"intent":"site_guide","confidence":0.95,"entities":{},"reason":"网站功能"}'
                        )
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    recognizer = SupportIntentRecognizer(client=client, allow_llm=True)
    result = await recognizer.recognize("余票时段怎么看", SupportPageContext(page_type="plan"))

    assert result.intent == SupportIntent.PAGE_EXPLAIN
    assert result.source == "rules"


@pytest.mark.asyncio
async def test_medium_confidence_rule_survives_llm_failure():
    class FailingCompletions:
        async def create(self, **_kwargs):
            raise RuntimeError("model unavailable")

    client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))
    recognizer = SupportIntentRecognizer(client=client, allow_llm=True)
    result = await recognizer.recognize("我可以坐飞机吗", SupportPageContext(page_type="plan"))

    assert result.intent == SupportIntent.PAGE_EXPLAIN
    assert result.source == "rules"


@pytest.mark.asyncio
async def test_llm_intent_prompt_accepts_datetime_history():
    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"intent":"page_explain","confidence":0.8,"entities":{},"reason":"交通问题"}'
                        )
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    recognizer = SupportIntentRecognizer(client=client, allow_llm=True)
    result = await recognizer._llm_recognize(
        "我可以坐飞机吗",
        SupportPageContext(page_type="plan"),
        [{"role": "user", "content": "上一轮", "created_at": datetime.now(timezone.utc)}],
    )

    assert result is not None
    assert result.intent == SupportIntent.PAGE_EXPLAIN


def test_support_reply_prompt_accepts_datetime_facts():
    service = SupportService(session=None, allow_llm=False)
    selected_skills = skill_registry.select(
        primary_agent="troubleshooter",
        supporting_agents=[],
        intent="troubleshoot",
    )
    prompt = service._reply_prompt(
        "有的图片好模糊为什么",
        SupportPageContext(page_type="attraction", poi_id="B02190A3JB"),
        SupportIntentResult(
            intent=SupportIntent.TROUBLESHOOT,
            confidence=0.88,
            secondary_intent=SupportIntent.PAGE_EXPLAIN,
        ),
        service._route(
            SupportIntentResult(intent=SupportIntent.TROUBLESHOOT, confidence=0.88),
            SupportPageContext(page_type="attraction"),
        ),
        {
            "page": {"route": "/attraction/B02190A3JB"},
            "tools": {"get_attraction_context": {"checked_at": datetime.now(timezone.utc)}},
        },
        [],
        [],
        selected_skills,
    )
    assert "有的图片好模糊为什么" in prompt
    assert f"[Skill: troubleshooter@{selected_skills[0].version}]" in prompt


def test_support_skills_load_hot_reload_and_route_injection(tmp_path):
    skill_dir = tmp_path / "troubleshooter"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"

    def write_skill(version: str, body: str) -> None:
        skill_file.write_text(
            "---\n"
            "name: troubleshooter\n"
            "route: troubleshooter\n"
            "description: 故障排查\n"
            f"version: {version}\n"
            "priority: 100\n"
            "intents: troubleshoot\n"
            "---\n\n"
            f"{body}\n",
            encoding="utf-8",
        )

    write_skill("1", "先检查图片状态。")
    registry = SupportSkillRegistry(tmp_path)
    first = registry.select(
        primary_agent="troubleshooter",
        supporting_agents=[],
        intent="troubleshoot",
    )
    assert len(first) == 1
    assert first[0].version == "1"
    assert "先检查图片状态" in first[0].content

    write_skill("2", "先区分图片模糊、缺失和重复。")
    second = registry.select(
        primary_agent="troubleshooter",
        supporting_agents=[],
        intent="troubleshoot",
    )
    assert second[0].version == "2"
    assert "区分图片模糊" in second[0].content


def test_default_support_skills_cover_three_agents_and_mixed_routes():
    description = skill_registry.describe()
    routes = {item["route"] for item in description["skills"]}
    assert {"site_guide", "troubleshooter", "travel_concierge"}.issubset(routes)

    selected = skill_registry.select(
        primary_agent="troubleshooter",
        supporting_agents=["travel_concierge"],
        intent="troubleshoot",
    )
    assert [skill.name for skill in selected] == ["troubleshooter", "travel_concierge"]


@pytest.mark.asyncio
async def test_support_stream_persists_thread_and_messages():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        repo = Repository(session)
        password_hash, salt = hash_password("secure-pass-123")
        user = await repo.create_user(
            user_id="support-user",
            username="support_fox",
            password_hash=password_hash,
            password_salt=salt,
            style_preference="landscape",
        )
        service = SupportService(session, allow_llm=False)
        events = [
            event
            async for event in service.stream_reply(
                user=user,
                message="你好",
                context=SupportPageContext(page_type="home"),
            )
        ]

        names = [event.event for event in events]
        assert "status" in names
        assert "message" in names
        assert names[-1] == "done"
        done = events[-1].data
        assert done["agent"] == "site_guide"
        assert done["thread_id"]
        skill_names = [item["name"] for item in events[-1].meta.get("skills", [])]
        assert skill_names == ["site_guide"]

        threads = await service.list_threads(user)
        assert len(threads) == 1
        messages = await service.thread_messages(user, done["thread_id"])
        assert messages is not None
        assert [item["role"] for item in messages] == ["user", "assistant"]

        other = await repo.create_user(
            user_id="other-user",
            username="other_fox",
            password_hash=password_hash,
            password_salt=salt,
            style_preference="balanced",
        )
        assert await service.thread_messages(other, done["thread_id"]) is None
    await engine.dispose()
