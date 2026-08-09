from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / .env.

    V0 keeps this intentionally small. As later versions introduce more
    infrastructure (queues, caches, external APIs), their configuration
    belongs here too, rather than being scattered across modules.
    """

    database_url: str = "postgresql+psycopg://commerceops:commerceops@db:5432/commerceops"
    app_name: str = "CommerceOps AI Platform"
    environment: str = "local"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
