import uuid

import pytest

from backend.app.config import get_settings
from backend.app.support.rag import SupportRAG


class FakeEmbedder:
    def __init__(self):
        self.last_usage = {"prompt_tokens": 12, "total_tokens": 12}

    async def embed(self, texts):
        return [[1.0, float(len(text) % 7), 0.25] for text in texts]


def write_knowledge(path, routes="troubleshooter"):
    path.write_text(
        "---\n"
        "title: 图片故障排查\n"
        "version: 1\n"
        f"routes: {routes}\n"
        "priority: 100\n"
        "---\n\n"
        "# 图片故障排查\n\n"
        "## 图片模糊\n\n"
        "图片模糊可能来自低分辨率、压缩或展示尺寸过大。\n\n"
        "## 图片重复\n\n"
        "系统使用感知哈希识别近似重复图片。\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_chroma_index_and_route_filtered_search(tmp_path):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    write_knowledge(knowledge_root / "troubleshooting.md")
    settings = get_settings().model_copy(
        update={
            "siliconflow_api_key": "test-key",
            "rag_collection": f"test_support_{uuid.uuid4().hex[:8]}",
            "rag_top_k": 4,
        }
    )
    rag = SupportRAG(
        settings=settings,
        knowledge_root=knowledge_root,
        chroma_dir=tmp_path / "chroma",
        manifest_path=tmp_path / "manifest.json",
        embedder=FakeEmbedder(),
        enabled=True,
    )

    assert await rag.ensure_index(force=True)
    status = rag.status()
    assert status["vector_ready"] is True
    assert status["chunk_count"] == 2

    result = await rag.search("图片为什么模糊", routes=["troubleshooter"], limit=2)
    assert result["retrieval"] in {"hybrid", "vector", "lexical"}
    assert result["documents"]
    assert all("troubleshooter" in item["routes"] for item in result["documents"])

    vector_only = await rag.search("图片为什么模糊", routes=["troubleshooter"], limit=2, mode="vector")
    assert vector_only["retrieval"] == "vector"
    assert vector_only["documents"]

    wrong_route = await rag.search("图片为什么模糊", routes=["site_guide"], limit=2)
    assert wrong_route["documents"] == []


def test_default_knowledge_has_six_documents():
    settings = get_settings().model_copy(update={"siliconflow_api_key": ""})
    rag = SupportRAG(settings=settings, enabled=False)
    status = rag.status()

    assert status["document_count"] == 6
    assert status["chunk_count"] >= 20
    assert status["retrieval_mode"] == "lexical"


def test_quoted_section_heading_is_boosted():
    rag = SupportRAG(settings=get_settings().model_copy(update={"siliconflow_api_key": ""}), enabled=False)
    rag.load_chunks()
    hits = rag._lexical_search("请说明“图片重复或与景区不匹配”的具体规则", ("troubleshooter",), limit=2)
    assert hits
    assert hits[0][0].section == "图片重复或与景区不匹配"


@pytest.mark.asyncio
async def test_quoted_section_survives_document_diversity(tmp_path):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    write_knowledge(knowledge_root / "troubleshooting.md")
    settings = get_settings().model_copy(update={"siliconflow_api_key": "test-key"})
    rag = SupportRAG(
        settings=settings,
        knowledge_root=knowledge_root,
        chroma_dir=tmp_path / "chroma",
        manifest_path=tmp_path / "manifest.json",
        embedder=FakeEmbedder(),
        enabled=True,
    )
    await rag.ensure_index(force=True)
    result = await rag.search(
        "请说明“图片重复”的具体规则",
        routes=["troubleshooter"],
        limit=1,
        mode="hybrid",
    )
    assert result["documents"][0]["section"] == "图片重复"
