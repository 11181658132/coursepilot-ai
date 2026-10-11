from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    max_upload_mb: int = 25
    chat_requests_per_minute: int = 12
    admin_requests_per_minute: int = 30
    openai_max_retries: int = 2
    openai_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("index_state_path", mode="after")
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
