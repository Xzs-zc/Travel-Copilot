from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

from ..config import Settings, get_settings


KNOWLEDGE_ROOT = Path(__file__).resolve().parent / "knowledge"
MAX_CHUNK_CHARS = 1400
CHUNK_OVERLAP_CHARS = 80
EMBEDDING_BATCH_SIZE = 32


class EmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True)
class RAGChunk:
    id: str
    doc_id: str
    title: str
    section: str
    content: str
    source: str
    routes: tuple[str, ...]
    version: str
    priority: int
    content_hash: str

    def as_result(self, retrieval: str, score: float = 0.0) -> dict[str, Any]:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "title": self.title,
            "section": self.section,
            "content": self.content,
            "source": self.source,
            "routes": list(self.routes),
            "version": self.version,
            "priority": self.priority,
            "retrieval": retrieval,
            "score": round(score, 6),
        }


@dataclass(frozen=True)
class MarkdownKnowledge:
    doc_id: str
    title: str
    version: str
    routes: tuple[str, ...]
    priority: int
    body: str
    source: str
    file_hash: str


class SiliconFlowEmbedder:
    """SiliconFlow OpenAI-compatible embeddings client.

    Contract: https://docs.siliconflow.cn/docs/api/embeddings-post
    BAAI/bge-m3 accepts up to 8192 input tokens and 32 inputs per batch.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.siliconflow_base_url.rstrip("/")
        self.last_usage: dict[str, int] = {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.settings.siliconflow_api_key:
            raise EmbeddingError("未配置 SILICONFLOW_API_KEY")
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.settings.siliconflow_embedding_model,
            "input": texts,
            "encoding_format": "float",
        }
        headers = {
            "Authorization": f"Bearer {self.settings.siliconflow_api_key}",
            "Content-Type": "application/json",
        }
        last_error = ""
        async with httpx.AsyncClient(timeout=max(20, self.settings.http_timeout_seconds * 2)) as client:
            for attempt in range(3):
                try:
                    response = await client.post(
                        f"{self.base_url}/embeddings",
                        headers=headers,
                        json=payload,
                    )
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                        await asyncio.sleep(0.6 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    data = response.json()
                    rows = data.get("data") or []
                    if len(rows) != len(texts):
                        raise EmbeddingError("Embedding 返回数量与输入数量不一致")
                    ordered = sorted(rows, key=lambda item: int(item.get("index") or 0))
                    vectors = [list(map(float, item.get("embedding") or [])) for item in ordered]
                    if any(not vector for vector in vectors):
                        raise EmbeddingError("Embedding 返回了空向量")
                    usage = data.get("usage") or {}
                    self.last_usage = {
                        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                        "total_tokens": int(usage.get("total_tokens") or 0),
                    }
                    return vectors
                except Exception as exc:
                    last_error = str(exc)[:300]
                    if attempt < 2:
                        await asyncio.sleep(0.6 * (attempt + 1))
                        continue
        raise EmbeddingError(last_error or "SiliconFlow Embedding 请求失败")


class SupportRAG:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        knowledge_root: Path | str = KNOWLEDGE_ROOT,
        chroma_dir: Path | str | None = None,
        manifest_path: Path | str | None = None,
        embedder: SiliconFlowEmbedder | Any | None = None,
        enabled: bool | None = None,
    ):
        self.settings = settings or get_settings()
        self.knowledge_root = Path(knowledge_root)
        self.chroma_dir = Path(chroma_dir or self.settings.chroma_dir)
        self.manifest_path = Path(manifest_path or self.settings.rag_manifest_path)
        self.embedder = embedder or SiliconFlowEmbedder(self.settings)
        self.enabled = self.settings.rag_enabled if enabled is None else enabled
        self._chunks: list[RAGChunk] = []
        self._snapshot: tuple[tuple[str, int, int], ...] = ()
        self._source_hash = ""
        self._collection: Any | None = None
        self._vector_ready = False
        self._last_error = ""
        self._last_index_at = ""
        self._query_cache: dict[tuple[Any, ...], tuple[float, list[float]]] = {}
        self._lock = asyncio.Lock()

    def load_chunks(self, force: bool = False) -> list[RAGChunk]:
        snapshot = self._snapshot_files()
        if not force and snapshot == self._snapshot and self._chunks:
            return self._chunks
        documents = self._load_documents()
        chunks: list[RAGChunk] = []
        hasher = hashlib.sha256()
        for document in documents:
            hasher.update(document.file_hash.encode("ascii"))
            hasher.update(b"\0")
            hasher.update(document.body.encode("utf-8"))
            hasher.update(b"\0")
            chunks.extend(self._chunk_document(document))
        self._snapshot = snapshot
        self._source_hash = hasher.hexdigest()
        self._chunks = chunks
        self._vector_ready = False
        return chunks

    async def search(
        self,
        query: str,
        *,
        routes: Iterable[str],
        limit: int | None = None,
        mode: str = "hybrid",
    ) -> dict[str, Any]:
        query = query.strip()
        route_values = tuple(dict.fromkeys(route for route in routes if route))
        if not query or not route_values:
            return {"documents": [], "retrieval": "empty", "usage": {}, "error": ""}
        if mode not in {"hybrid", "vector", "lexical"}:
            raise ValueError(f"不支持的 RAG 检索模式：{mode}")
        self.load_chunks()
        lexical = self._lexical_search(query, route_values, limit=8) if mode in {"hybrid", "lexical"} else []
        vector: list[tuple[RAGChunk, float]] = []
        vector_error = ""
        if mode in {"hybrid", "vector"} and self.enabled and self.settings.siliconflow_api_key:
            try:
                vector = await self._vector_search(query, route_values, limit=8)
            except Exception as exc:
                vector_error = str(exc)[:300]
                self._last_error = vector_error
        elif mode == "vector":
            vector_error = "向量检索未启用或未配置 Embedding Key"
        fused = self._rrf(lexical, vector, lexical_weight=1.2, vector_weight=1.0) if mode == "hybrid" else [
            (chunk, "vector", score) for chunk, score in vector
        ] if mode == "vector" else [
            (chunk, "lexical", score) for chunk, score in lexical
        ]
        quoted_sections = {
            re.sub(r"\s+", "", item).lower()
            for item in re.findall(r"“([^”]+)”", query)
        }
        if quoted_sections:
            fused.sort(
                key=lambda item: 0
                if re.sub(r"\s+", "", item[0].section).lower() in quoted_sections
                else 1
            )
        if mode == "hybrid":
            fused = self._diversify(fused, max_per_doc=2)
        final_limit = limit or self.settings.rag_top_k
        documents = [chunk.as_result(retrieval, score) for chunk, retrieval, score in fused[:final_limit]]
        retrieval_mode = "hybrid" if vector and lexical else "vector" if vector else "lexical" if lexical else "none"
        return {
            "documents": documents,
            "retrieval": retrieval_mode,
            "usage": dict(getattr(self.embedder, "last_usage", {}) or {}),
            "error": vector_error,
        }

    async def ensure_index(self, force: bool = False) -> bool:
        if not self.enabled or not self.settings.siliconflow_api_key:
            self._vector_ready = False
            return False
        async with self._lock:
            self.load_chunks(force=force)
            collection = self._get_collection()
            manifest = self._read_manifest()
            if (
                not force
                and manifest.get("source_hash") == self._source_hash
                and manifest.get("collection") == self.settings.rag_collection
                and manifest.get("embedding_model") == self.settings.siliconflow_embedding_model
                and int(manifest.get("chunk_count") or 0) > 0
                and collection.count() > 0
            ):
                self._vector_ready = True
                self._last_error = ""
                self._last_index_at = str(manifest.get("indexed_at") or "")
                return True
            try:
                await self._rebuild_index(collection)
                self._vector_ready = True
                self._last_error = ""
                return True
            except Exception as exc:
                self._vector_ready = False
                self._last_error = str(exc)[:300]
                return False

    async def reload(self) -> dict[str, Any]:
        self.load_chunks(force=True)
        await self.ensure_index(force=True)
        return self.status()

    def status(self) -> dict[str, Any]:
        self.load_chunks()
        manifest = self._read_manifest()
        configured = bool(self.settings.siliconflow_api_key)
        return {
            "enabled": self.enabled,
            "configured": configured,
            "embedding_model": self.settings.siliconflow_embedding_model,
            "collection": self.settings.rag_collection,
            "document_count": len({chunk.doc_id for chunk in self._chunks}),
            "chunk_count": len(self._chunks),
            "vector_ready": self._vector_ready,
            "retrieval_mode": "hybrid" if self._vector_ready else "lexical",
            "source_hash": self._source_hash,
            "last_index_at": self._last_index_at or manifest.get("indexed_at"),
            "last_error": self._last_error,
            "chroma_dir": str(self.chroma_dir),
        }

    def _snapshot_files(self) -> tuple[tuple[str, int, int], ...]:
        if not self.knowledge_root.exists():
            return ()
        snapshot: list[tuple[str, int, int]] = []
        for path in sorted(self.knowledge_root.glob("*.md")):
            try:
                stat = path.stat()
                snapshot.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        return tuple(snapshot)

    def _load_documents(self) -> list[MarkdownKnowledge]:
        documents: list[MarkdownKnowledge] = []
        for path in sorted(self.knowledge_root.glob("*.md")):
            text = path.read_text(encoding="utf-8").strip()
            metadata, body = self._parse_frontmatter(text)
            routes = tuple(
                route.strip()
                for route in re.split(r"[,，]", metadata.get("routes") or "")
                if route.strip()
            )
            documents.append(
                MarkdownKnowledge(
                    doc_id=path.stem,
                    title=metadata.get("title") or path.stem,
                    version=metadata.get("version") or "1",
                    routes=routes or ("site_guide", "troubleshooter", "travel_concierge"),
                    priority=int(metadata.get("priority") or 0),
                    body=body,
                    source=path.name,
                    file_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
        return documents

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
        if not match:
            raise ValueError("知识文档缺少 frontmatter")
        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip('"').strip("'")
        return metadata, match.group(2).strip()

    def _chunk_document(self, document: MarkdownKnowledge) -> list[RAGChunk]:
        sections: list[tuple[str, str]] = []
        current_title = "概述"
        current_lines: list[str] = []
        body_lines = document.body.splitlines()
        if body_lines and body_lines[0].startswith("# "):
            body_lines = body_lines[1:]
        for line in body_lines:
            if line.startswith("## "):
                if any(item.strip() for item in current_lines):
                    sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = line[3:].strip()
                current_lines = []
            else:
                current_lines.append(line)
        if any(item.strip() for item in current_lines):
            sections.append((current_title, "\n".join(current_lines).strip()))

        chunks: list[RAGChunk] = []
        for section, content in sections:
            for part in self._split_long_text(content):
                digest = hashlib.sha256(
                    f"{document.doc_id}\0{section}\0{part}".encode("utf-8")
                ).hexdigest()
                chunks.append(
                    RAGChunk(
                        id=digest[:24],
                        doc_id=document.doc_id,
                        title=document.title,
                        section=section,
                        content=part,
                        source=document.source,
                        routes=document.routes,
                        version=document.version,
                        priority=document.priority,
                        content_hash=digest,
                    )
                )
        return chunks

    @staticmethod
    def _split_long_text(text: str) -> list[str]:
        if len(text) <= MAX_CHUNK_CHARS:
            return [text]
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
        parts: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 2 > MAX_CHUNK_CHARS:
                parts.append(current)
                current = current[-CHUNK_OVERLAP_CHARS:] + "\n\n" + paragraph
            elif current:
                current += "\n\n" + paragraph
            else:
                current = paragraph
        if current:
            parts.append(current)
        return parts

    def _lexical_search(
        self,
        query: str,
        routes: tuple[str, ...],
        *,
        limit: int,
    ) -> list[tuple[RAGChunk, float]]:
        terms = self._query_terms(query)
        quoted_sections = {
            re.sub(r"\s+", "", item).lower()
            for item in re.findall(r"“([^”]+)”", query)
        }
        scored: list[tuple[RAGChunk, float]] = []
        for chunk in self._chunks:
            if not set(chunk.routes).intersection(routes):
                continue
            haystack = f"{chunk.title}\n{chunk.section}\n{chunk.content}".lower()
            score = 0.0
            normalized_query = re.sub(r"\s+", "", query.lower())
            normalized_section = re.sub(r"\s+", "", chunk.section).lower()
            if normalized_section in quoted_sections:
                score += 50
            elif normalized_section and normalized_section in normalized_query:
                score += 20
            if normalized_query and normalized_query in re.sub(r"\s+", "", haystack):
                score += 12
            for term, weight in terms:
                if term in haystack:
                    score += weight * min(3, haystack.count(term))
            if score > 0:
                score += chunk.priority / 1000
                scored.append((chunk, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    @staticmethod
    def _query_terms(query: str) -> list[tuple[str, float]]:
        compact = re.sub(r"\s+", "", query.lower())
        terms: dict[str, float] = {}
        for word in re.findall(r"[a-z0-9_./-]{2,}", query.lower()):
            terms[word] = 4
        chinese = re.findall(r"[\u4e00-\u9fff]+", compact)
        for sequence in chinese:
            if len(sequence) <= 6:
                terms.setdefault(sequence, 4)
            for size, weight in ((2, 1.5), (3, 2.0), (4, 2.5)):
                if len(sequence) < size:
                    continue
                for index in range(len(sequence) - size + 1):
                    terms.setdefault(sequence[index : index + size], weight)
        return list(terms.items())

    async def _vector_search(
        self,
        query: str,
        routes: tuple[str, ...],
        *,
        limit: int,
    ) -> list[tuple[RAGChunk, float]]:
        if not await self.ensure_index():
            return []
        collection = self._get_collection()
        query_vector = await self._query_embedding(query, routes)
        where = {"route": {"$in": list(routes)}}
        try:
            result = collection.query(
                query_embeddings=[query_vector],
                n_results=limit,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            result = collection.query(
                query_embeddings=[query_vector],
                n_results=max(limit * 3, 12),
                include=["documents", "metadatas", "distances"],
            )
        ids = (result.get("ids") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        by_id = {chunk.id: chunk for chunk in self._chunks}
        rows: list[tuple[RAGChunk, float]] = []
        seen: set[str] = set()
        for index, record_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            metadata = metadata or {}
            chunk_id = str(metadata.get("chunk_id") or "")
            route = str(metadata.get("route") or "")
            chunk = by_id.get(chunk_id)
            if chunk is None or chunk_id in seen or route not in routes:
                continue
            seen.add(chunk_id)
            distance = float(distances[index]) if index < len(distances) else 1.0
            rows.append((chunk, max(0.0, 1.0 - distance)))
        return rows

    async def _query_embedding(self, query: str, routes: tuple[str, ...]) -> list[float]:
        cache_key = (
            query,
            tuple(sorted(routes)),
            self.settings.siliconflow_embedding_model,
            self._source_hash,
        )
        cached = self._query_cache.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < 300:
            return cached[1]
        vectors = await self.embedder.embed([query])
        vector = vectors[0]
        if len(self._query_cache) > 128:
            self._query_cache.clear()
        self._query_cache[cache_key] = (now, vector)
        return vector

    async def _rebuild_index(self, collection: Any) -> None:
        desired: list[dict[str, Any]] = []
        for chunk in self._chunks:
            for route in chunk.routes:
                desired.append(
                    {
                        "id": f"{route}:{chunk.id}",
                        "chunk_id": chunk.id,
                        "route": route,
                        "content": chunk.content,
                        "content_hash": chunk.content_hash,
                        "metadata": {
                            "chunk_id": chunk.id,
                            "doc_id": chunk.doc_id,
                            "title": chunk.title,
                            "section": chunk.section,
                            "route": route,
                            "source": chunk.source,
                            "version": chunk.version,
                            "priority": chunk.priority,
                            "content_hash": chunk.content_hash,
                        },
                    }
                )
        existing = collection.get(include=["metadatas"])
        existing_ids = list(existing.get("ids") or [])
        existing_meta = existing.get("metadatas") or []
        existing_hashes = {
            record_id: str((existing_meta[index] or {}).get("content_hash") or "")
            for index, record_id in enumerate(existing_ids)
        }
        changed = [
            item
            for item in desired
            if existing_hashes.get(item["id"]) != item["content_hash"]
        ]
        texts = [item["content"] for item in changed]
        vectors = await self.embedder.embed(texts) if texts else []
        for start in range(0, len(changed), EMBEDDING_BATCH_SIZE):
            batch = changed[start : start + EMBEDDING_BATCH_SIZE]
            batch_vectors = vectors[start : start + EMBEDDING_BATCH_SIZE]
            collection.upsert(
                ids=[item["id"] for item in batch],
                documents=[item["content"] for item in batch],
                embeddings=batch_vectors,
                metadatas=[item["metadata"] for item in batch],
            )
        desired_ids = {item["id"] for item in desired}
        stale_ids = [record_id for record_id in existing_ids if record_id not in desired_ids]
        if stale_ids:
            collection.delete(ids=stale_ids)
        indexed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        manifest = {
            "source_hash": self._source_hash,
            "collection": self.settings.rag_collection,
            "embedding_model": self.settings.siliconflow_embedding_model,
            "chunk_count": len(desired),
            "indexed_at": indexed_at,
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._last_index_at = indexed_at

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=str(self.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = client.get_or_create_collection(
            name=self.settings.rag_collection,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": self.settings.siliconflow_embedding_model,
                "schema_version": 1,
            },
        )
        return self._collection

    def _read_manifest(self) -> dict[str, Any]:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _rrf(
        lexical: list[tuple[RAGChunk, float]],
        vector: list[tuple[RAGChunk, float]],
        *,
        lexical_weight: float = 1.0,
        vector_weight: float = 1.0,
    ) -> list[tuple[RAGChunk, str, float]]:
        scores: dict[str, float] = {}
        chunks: dict[str, RAGChunk] = {}
        retrieval: dict[str, set[str]] = {}
        for label, rows, weight in (
            ("lexical", lexical, lexical_weight),
            ("vector", vector, vector_weight),
        ):
            for rank, (chunk, _) in enumerate(rows, start=1):
                chunks[chunk.id] = chunk
                scores[chunk.id] = scores.get(chunk.id, 0.0) + weight / (60 + rank)
                retrieval.setdefault(chunk.id, set()).add(label)
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [
            (chunks[chunk_id], "+".join(sorted(retrieval[chunk_id])), score)
            for chunk_id, score in ordered
        ]

    @staticmethod
    def _diversify(
        rows: list[tuple[RAGChunk, str, float]],
        *,
        max_per_doc: int,
    ) -> list[tuple[RAGChunk, str, float]]:
        selected: list[tuple[RAGChunk, str, float]] = []
        deferred: list[tuple[RAGChunk, str, float]] = []
        counts: dict[str, int] = {}
        for row in rows:
            doc_id = row[0].doc_id
            if counts.get(doc_id, 0) < max_per_doc:
                selected.append(row)
                counts[doc_id] = counts.get(doc_id, 0) + 1
            else:
                deferred.append(row)
        return [*selected, *deferred]


rag_index = SupportRAG()
