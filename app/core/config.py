from typing import Optional
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / .env.

    V0 keeps this intentionally small. As later versions introduce more
    infrastructure (queues, caches, external APIs), their configuration
    belongs here too, rather than being scattered across modules.

    V1 (AWS): ECS injects the RDS master credentials as separate DB_USERNAME /
    DB_PASSWORD "secrets" (pulled from the Secrets Manager secret AWS manages
    for the RDS instance) plus plain DB_HOST / DB_PORT / DB_NAME environment
    variables, rather than one pre-assembled connection string secret. When
    those are present, database_url is assembled from them below. Local
    Docker Compose still just sets DATABASE_URL directly, so that path is
    unchanged.
    """

    database_url: str = "postgresql+psycopg://commerceops:commerceops@db:5432/commerceops"
    app_name: str = "CommerceOps AI Platform"
    environment: str = "local"

    db_host: Optional[str] = None
    db_port: int = 5432
    db_name: Optional[str] = None
    db_username: Optional[str] = None
    db_password: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _assemble_database_url_from_parts(self) -> "Settings":
        if self.db_host and self.db_name and self.db_username and self.db_password:
            user = quote_plus(self.db_username)
            password = quote_plus(self.db_password)
            self.database_url = (
                f"postgresql+psycopg://{user}:{password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        return self


settings = Settings()
