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

    # V5 (Reliability): a query with no timeout can hold a pool connection
    # forever, so one slow statement eventually starves every request of the
    # small pool above. statement_timeout is enforced server-side by
    # PostgreSQL itself, which is the only place that can actually stop a
    # query already running — see docs/adr/ADR-006-reliability.md.
    db_connect_timeout_seconds: int = 5
    db_statement_timeout_seconds: int = 5

    # V6 (Database HA): optional asynchronous read replica for product GET
    # endpoints. Assembled from DB_READ_HOST + the same credentials as the
    # primary (physical replication shares them), or set wholesale via
    # DATABASE_READ_URL under Docker Compose. When read_replica_enabled is
    # false or no read URL/host is set, the read engine *is* the write
    # engine — tests and single-DB local runs need no configuration.
    # See docs/adr/ADR-007-database-ha.md.
    database_read_url: Optional[str] = None
    db_read_host: Optional[str] = None
    read_replica_enabled: bool = False

    # Bounds how long a pooled connection to a pre-failover primary can
    # linger. pool_pre_ping already discards connections killed by a
    # failover; recycle is the belt for connections that look fine to the
    # client but point at a primary that no longer exists.
    db_pool_recycle_seconds: int = 300

    # Soft lag budget reported by /health/ready (does not fail the probe).
    db_max_replica_lag_seconds: float = 5.0

    # Short transient-error retry on *reads only* around a Multi-AZ failover.
    # Writes fail fast — retrying a write whose commit outcome is unknown is
    # the database version of V5's PAYMENT_PENDING problem.
    db_transient_retry_attempts: int = 2

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
    #
    # V5 raised this from 30s to 60s: app/worker.py now retries each handler
    # in-process with backoff, and that retry budget has to fit inside the
    # visibility window. Otherwise SQS makes the message visible again while
    # the first worker is still retrying it, and a *second* worker starts
    # processing the same event concurrently.
    sqs_visibility_timeout_seconds: int = 60
    sqs_max_receive_count: int = 5

    # How long app/worker.py remembers an event_id as "already processed",
    # to make at-least-once delivery idempotent for the duration a redelivery
    # is realistically still possible. V5: this is now the TTL of the Redis
    # *cache* in front of the authoritative processed_events table, not the
    # dedup record itself (app/core/idempotency.py).
    sqs_idempotency_ttl_seconds: int = 86400

    # V5 (Reliability): boto3's own retries are disabled in favour of the
    # explicit, logged, jittered retries in app/core/reliability.py — two
    # independent retry layers multiply load and make the real attempt count
    # invisible. read_timeout only applies to the producer's short calls;
    # app/worker.py builds its own client with a longer read_timeout because
    # a 20-second long poll would otherwise time out on every single receive.
    sqs_connect_timeout_seconds: int = 3
    sqs_read_timeout_seconds: int = 5
    sqs_publish_retry_attempts: int = 3

    # V5: the order-events publish is no longer single-shot. It stays
    # best-effort (never fails the order), but a transient blip now costs a
    # few hundred milliseconds of retry instead of silently losing that
    # order's side effects.
    publish_retry_base_delay_seconds: float = 0.1

    # V5 (Reliability) — payment gateway. In AWS the fake gateway runs as a
    # sidecar in the same task, so this is localhost; under Docker Compose
    # it's a separate service by name. Only this URL differs between the two,
    # the same "one env var differs" shape as SQS_ENDPOINT_URL above.
    payment_gateway_url: str = "http://payment-gateway:9000"

    # Split connect/read timeouts, because they fail for different reasons:
    # connect failing fast means "nothing is listening", read timing out
    # means "it accepted the request and then didn't answer" — the ambiguous
    # case where the charge may actually have gone through.
    payment_connect_timeout_seconds: float = 1.0
    payment_read_timeout_seconds: float = 2.0

    # attempts=4 with base=1.0/multiplier=2/max=8.0 produces the V5 spec's
    # exact 1s / 2s / 4s ladder between four attempts.
    payment_retry_attempts: int = 4
    retry_base_delay_seconds: float = 1.0
    retry_multiplier: float = 2.0
    retry_max_delay_seconds: float = 8.0

    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_seconds: float = 30.0

    # Roughly a quarter of FastAPI's default 40-thread pool: enough
    # concurrency for normal order volume, low enough that a hanging gateway
    # can never consume every thread and take product reads down with it.
    payment_bulkhead_max_concurrency: int = 10
    payment_bulkhead_acquire_timeout_seconds: float = 0.5

    # V5: per-handler retry inside app/worker.py. Deliberately a *smaller*
    # budget than the payment ladder above — the whole ladder must fit inside
    # sqs_visibility_timeout_seconds alongside the handlers' own runtime, and
    # SQS redelivery already provides the slow, long-horizon retries.
    worker_handler_retry_attempts: int = 3
    worker_handler_retry_base_delay_seconds: float = 0.5
    worker_handler_retry_max_delay_seconds: float = 2.0

    # How long one worker's claim on an event lasts. Sized to the visibility
    # timeout: the lease should expire at roughly the moment SQS would let
    # another worker receive the same message anyway.
    idempotency_lease_ttl_seconds: int = 60

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
    def _assemble_database_read_url_from_parts(self) -> "Settings":
        # Prefer an explicitly set DATABASE_READ_URL (Compose). Otherwise,
        # when ECS injects DB_READ_HOST alongside the primary's credentials,
        # assemble a read URL that reuses them — a replica inherits the
        # master password via physical replication.
        if self.database_read_url:
            return self
        if (
            self.db_read_host
            and self.db_name
            and self.db_username
            and self.db_password
        ):
            user = quote_plus(self.db_username)
            password = quote_plus(self.db_password)
            self.database_read_url = (
                f"postgresql+psycopg://{user}:{password}"
                f"@{self.db_read_host}:{self.db_port}/{self.db_name}"
            )
        return self

    @model_validator(mode="after")
    def _assemble_redis_url_from_parts(self) -> "Settings":
        if self.redis_host:
            self.redis_url = f"redis://{self.redis_host}:{self.redis_port}/0"
        return self


settings = Settings()
