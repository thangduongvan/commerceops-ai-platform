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

    # V2 (Horizontal Scaling): explicit, small pool per task. Every ECS task
    # runs its own process with its own pool, so the RDS connection ceiling is
    # roughly `running_tasks * (db_pool_size + db_max_overflow)`, not a single
    # global number — see docs/adr/ADR-003-horizontal-scaling.md for the math
    # against db.t3.micro's default max_connections (~112) at max_capacity.
    db_pool_size: int = 5
    db_max_overflow: int = 3

    # V3 (Caching): same "assemble from parts" pattern as the database URL
    # above. ECS injects REDIS_HOST/REDIS_PORT as plain (non-secret) env vars
    # — Redis has no AUTH/TLS in this setup, see docs/adr/ADR-004-caching.md.
    # Local Docker Compose just sets REDIS_URL directly, unchanged.
    redis_url: str = "redis://redis:6379/0"
    redis_host: Optional[str] = None
    redis_port: int = 6379

    redis_max_connections: int = 10
    redis_socket_timeout_seconds: float = 0.2

    # Product reads (detail + listing) are cached for this long. Short enough
    # to bound staleness on the un-invalidated listing cache (see
    # app/product/service.py), long enough to cut DB load meaningfully during
    # a flash sale's 90%+ read traffic.
    cache_ttl_seconds: int = 15

    # Lets the "without cache vs with cache" experiment (docs/deployment.md)
    # toggle caching off entirely via one env var, no redeploy of infra needed.
    cache_enabled: bool = True

    # V4 (Asynchronous Processing): app/core/queue.py resolves a queue *name*
    # to its URL via get_queue_url at runtime, rather than threading a
    # pre-built URL through Terraform/Compose — the exact same call works
    # unchanged against LocalStack and real AWS SQS. sqs_endpoint_url is the
    # one thing that actually differs: None in AWS (default regional
    # endpoint, auth via the ECS task role's credentials), set to LocalStack's
    # URL locally (see docs/adr/ADR-005-async-processing.md).
    aws_region: str = "us-east-1"
    sqs_queue_name: str = "order-events"
    sqs_dlq_name: str = "order-events-dlq"
    sqs_endpoint_url: Optional[str] = None

    # Must match infra/modules/sqs's queue configuration so local (LocalStack)
    # and AWS behave the same way — see infra/localstack/create-queues.sh.
    sqs_visibility_timeout_seconds: int = 30
    sqs_max_receive_count: int = 5

    # How long app/worker.py remembers an event_id as "already processed",
    # to make at-least-once delivery idempotent for the duration a redelivery
    # is realistically still possible.
    sqs_idempotency_ttl_seconds: int = 86400

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

    @model_validator(mode="after")
    def _assemble_redis_url_from_parts(self) -> "Settings":
        if self.redis_host:
            self.redis_url = f"redis://{self.redis_host}:{self.redis_port}/0"
        return self


settings = Settings()
