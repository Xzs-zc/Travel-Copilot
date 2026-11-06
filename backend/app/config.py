from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8765
    app_env: str = "development"
    cookie_secure: bool | None = None

    amap_web_service_key: str = ""
    amap_js_key: str = ""
    amap_js_security_code: str = ""

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    vision_api_key: str = ""
    vision_base_url: str = "https://api.deepseek.com"
    vision_model: str = "deepseek-flash"

    serpapi_api_key: str = ""
    serpapi_base_url: str = "https://serpapi.com"

    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_embedding_model: str = "BAAI/bge-m3"
    siliconflow_rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rag_enabled: bool = True
    rag_collection: str = "support_knowledge_v1"
    rag_top_k: int = 4
    rag_candidate_k: int = 15
    rag_max_per_doc: int = 3
    chroma_dir: Path = DATA_DIR / "chroma"
    rag_manifest_path: Path = DATA_DIR / "rag_manifest.json"

    langfuse_enabled: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR / 'travel_v2.db'}"
    database_auto_create: bool = True
    database_pool_size: int = 10
    database_max_overflow: int = 20
    redis_url: str = ""
    trust_proxy_headers: bool = False
    media_dir: Path = MEDIA_DIR
    max_agent_steps: int = 12
    max_agent_tool_calls: int = 24
    registration_invite_code: str = ""
    agent_timeout_seconds: int = 60
    http_timeout_seconds: int = 20
    http_max_attempts: int = 3
    http_retry_base_seconds: float = 0.2
    http_retry_max_seconds: float = 2.0
    provider_failure_threshold: int = 5
    provider_recovery_seconds: int = 30
    provider_max_concurrency: int = 8
    rate_limit_per_minute: int = 120
    auth_rate_limit_per_minute: int = 10
    stream_rate_limit_per_minute: int = 20
    task_queue_stream: str = "travel:agent-tasks"
    task_queue_group: str = "travel-agent-workers"
    task_consumer_name: str = "worker-1"
    task_max_attempts: int = 3
    task_stale_seconds: int = 300
    task_stream_poll_seconds: float = 0.25
    mcp_transport: str = "stdio"
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    ablation_variant: str = "full"
    langgraph_checkpoint_path: Path = DATA_DIR / "langgraph_checkpoints.db"

    def missing_required(self) -> list[str]:
        fields = {
            "AMAP_WEB_SERVICE_KEY": self.amap_web_service_key,
            "AMAP_JS_KEY": self.amap_js_key,
            "AMAP_JS_SECURITY_CODE": self.amap_js_security_code,
            "DEEPSEEK_API_KEY": self.deepseek_api_key,
        }
        return [name for name, value in fields.items() if not str(value).strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    return settings
