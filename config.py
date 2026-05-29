"""ContextO configuration from environment (.env)."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    github_personal_access_token: str = Field(
        validation_alias=AliasChoices(
            "GITHUB_PERSONAL_ACCESS_TOKEN",
            "GITHUB_PERSONAL_TOKEN",
        )
    )
    github_owner: str = Field(validation_alias="GITHUB_OWNER")
    github_repo: str = Field(validation_alias="GITHUB_REPO")
    log_source_url: str = Field(
        default="http://127.0.0.1:5000/api/logs",
        validation_alias="LOG_SOURCE_URL",
    )
    poll_interval: int = Field(default=30, validation_alias="POLL_INTERVAL")
    commit_poll_interval: int = Field(
        default=60, validation_alias="COMMIT_POLL_INTERVAL"
    )
    llm_model: str = Field(default="gemini-3.1-flash-lite-preview", validation_alias="LLM_MODEL")
    google_api_key: str = Field(validation_alias="GOOGLE_API_KEY")
    db_path: str = Field(default="contexto.db", validation_alias="DB_PATH")
    slack_webhook_url: str = Field(default="", validation_alias="SLACK_WEBHOOK_URL")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
