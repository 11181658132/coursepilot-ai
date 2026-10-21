from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"
    knowledge_base_dir: Path = PROJECT_ROOT / "knowledge_base"
    index_state_path: Path = PROJECT_ROOT / "backend/data/index_state.json"
    frontend_origin: str = "http://localhost:3000"
    max_search_results: int = 8
    telemetry_path: Path = PROJECT_ROOT / "backend/data/chat_events.jsonl"
    conversation_db_path: Path = PROJECT_ROOT / "backend/data/coursepilot.db"
    openai_input_cost_per_million: float = 0.10
    openai_output_cost_per_million: float = 0.50
    openai_file_search_cost_per_thousand: float = 2.50
    max_upload_mb: int = 25
    chat_requests_per_minute: int = 12
    admin_requests_per_minute: int = 30
    openai_max_retries: int = 2
    openai_timeout_seconds: float = 60.0
    rag_engine: Literal["file_search", "hybrid"] = "file_search"
    rag_db_path: Path = PROJECT_ROOT / "backend/data/rag.sqlite3"
    rag_embedding_model: str = "text-embedding-3-small"
    rag_embedding_cost_per_million: float = 0.02
    rag_candidate_count: int = Field(default=12, ge=1, le=24)
    rag_rerank: bool = True
    rag_chunk_chars: int = Field(default=1600, ge=100, le=2000)
    rag_chunk_overlap: int = Field(default=200, ge=0)
    rag_index_max_embedding_tokens: int = Field(default=5_000_000, ge=1)
    agent_run_budget_usd: float = Field(default=0.05, gt=0, le=0.05)
    agent_daily_budget_usd: float = Field(default=0.50, gt=0, le=0.50)
    agent_max_turns: int = Field(default=6, ge=1, le=6)
    agent_deadline_seconds: float = Field(default=120, gt=0, le=120)
    agent_request_timeout_seconds: float = Field(default=20, gt=0, le=20)

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("index_state_path", "rag_db_path", mode="after")
    @classmethod
    def resolve_state_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("telemetry_path", mode="after")
    @classmethod
    def resolve_telemetry_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("conversation_db_path", mode="after")
    @classmethod
    def resolve_conversation_db_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value


@lru_cache
def get_settings() -> Settings:
    return Settings()
