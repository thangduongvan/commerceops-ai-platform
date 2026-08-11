"""V6 (Database HA) drills.

The V6 spec asks four "what happens if" questions. This script turns each one
into a measurable experiment against a running stack:

    python loadtest/ha_experiment.py lag             # replica lag / read-your-writes
    python loadtest/ha_experiment.py promote-local   # local standby promotion (RPO)
    python loadtest/ha_experiment.py failover-aws    # Multi-AZ forced failover (RTO)
    python loadtest/ha_experiment.py restore-pitr    # PITR restore timing (why backups ≠ HA)

Prerequisites for local drills (`lag`, `promote-local`):

    docker compose up -d
    $env:APP_URL = "http://localhost:8000"
    $env:PRIMARY_DSN = "postgresql://commerceops:commerceops@localhost:5432/commerceops"
    $env:REPLICA_DSN = "postgresql://commerceops:commerceops@localhost:5433/commerceops"

Prerequisites for AWS drills (`failover-aws`, `restore-pitr`):

    AWS credentials / region configured, Multi-AZ RDS deployed.
    $env:APP_URL = "http://<alb-dns>"
    $env:RDS_INSTANCE_ID = "commerceops-dev-postgres"   # terraform output rds_identifier

See docs/deployment.md §10 and docs/adr/ADR-007-database-ha.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
PRIMARY_DSN = os.environ.get(
    "PRIMARY_DSN",
    "postgresql://commerceops:commerceops@localhost:5432/commerceops",
)
REPLICA_DSN = os.environ.get(
    "REPLICA_DSN",
    "postgresql://commerceops:commerceops@localhost:5433/commerceops",
)
RDS_INSTANCE_ID = os.environ.get("RDS_INSTANCE_ID", "commerceops-dev-postgres")
DB_REPLICA_SERVICE = os.environ.get("DB_REPLICA_SERVICE", "db-replica")
RTO_BUDGET_SECONDS = float(os.environ.get("RTO_BUDGET_SECONDS", "300"))


def _log(message: str) -> None:
    print(f"  {message}", flush=True)


def _header(title: str, expectation: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)
    print(f"Expected: {expectation}\n", flush=True)


def _seed() -> tuple[int, int]:
    client = httpx.Client(base_url=APP_URL, timeout=30)
    customer = client.post(
        "/customers",
        json={"name": "HA Tester", "email": f"ha-{uuid.uuid4()}@example.com"},
    ).json()
    product = client.post(
        "/products",
        json={
            "name": f"HA Widget {uuid.uuid4().hex[:8]}",
            "price": 19.99,
            "stock_quantity": 100_000,
        },
    ).json()
    return customer["id"], product["id"]


def _psql(dsn: str, sql: str) -> str:
    """Run a one-shot SQL statement via the postgres Docker image's psql.

    Avoids adding psycopg as a loadtest dependency — the Compose stack already
    has a postgres:16 image, and the AWS drills don't need a local client.
    """
    # Parse host/port/user/db from a simple postgresql:// URL for docker exec
    # against the running Compose services when possible; fall back to a
    # one-off container that can reach host.docker.internal / localhost.
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "psql",
            dsn if dsn.startswith("postgresql") else PRIMARY_DSN,
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Direct host connection via a throwaway container when compose exec
        # against the primary DSN fails (e.g. querying the replica).
        host_dsn = dsn
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "postgres:16",
                "psql",
                host_dsn,
                "-v",
                "ON_ERROR_STOP=1",
                "-tAc",
                sql,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"psql failed ({result.returncode}): {result.stderr.strip() or result.stdout}"
        )
    return result.stdout.strip()


def _replica_psql(sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            DB_REPLICA_SERVICE,
            "psql",
            "-U",
            "commerceops",
            "-d",
            "commerceops",
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"replica psql failed: {result.stderr.strip() or result.stdout}"
        )
    return result.stdout.strip()


def _primary_psql(sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "commerceops",
            "-d",
            "commerceops",
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"primary psql failed: {result.stderr.strip() or result.stdout}"
        )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# lag — write then immediately read from the replica
# ---------------------------------------------------------------------------


def cmd_lag(args: argparse.Namespace) -> int:
    _header(
        "Replica lag / read-your-own-writes",
        "A write committed on the primary is not instantly visible on the "
        "asynchronous replica. Observed staleness should be small but > 0 under load.",
    )

    client = httpx.Client(base_url=APP_URL, timeout=30)
    # Bypass the app cache so we measure replica lag, not Redis TTL.
    name = f"lag-probe-{uuid.uuid4().hex}"
    created = client.post(
        "/products",
        json={"name": name, "price": 1.0, "stock_quantity": 1},
    )
    created.raise_for_status()
    product_id = created.json()["id"]
    _log(f"wrote product id={product_id} name={name} on primary via API")

    # Direct replica query (bypasses Redis + app) — the ground truth for lag.
    deadline = time.monotonic() + args.timeout
    first_seen_at: float | None = None
    samples = 0
    while time.monotonic() < deadline:
        samples += 1
        try:
            found = _replica_psql(
                f"SELECT id FROM products WHERE id = {int(product_id)}"
            )
        except RuntimeError as exc:
            _log(f"replica query error: {exc}")
            time.sleep(args.poll_interval)
            continue
        if found:
            first_seen_at = time.monotonic()
            break
        time.sleep(args.poll_interval)

    if first_seen_at is None:
        _log(f"FAIL: product {product_id} not visible on replica after {args.timeout}s")
        return 1

    # Approximate write timestamp as "just before we started polling".
    # Good enough to demonstrate the hazard; /health/ready exposes the
    # replica's own lag_seconds for a running system.
    lag_ms = samples * args.poll_interval * 1000
    ready = client.get("/health/ready").json()
    reported = ready.get("checks", {}).get("database_replica", {})
    _log(f"visible on replica after ~{lag_ms:.0f}ms ({samples} polls)")
    _log(f"/health/ready database_replica = {json.dumps(reported)}")
    _log(
        "Lesson: a customer reading their *own* order right after placing it "
        "cannot use this replica. That is why only product GETs are routed here."
    )
    return 0


# ---------------------------------------------------------------------------
# promote-local — RPO experiment
# ---------------------------------------------------------------------------


def cmd_promote_local(args: argparse.Namespace) -> int:
    _header(
        "Local standby promotion (RPO experiment)",
        "With *asynchronous* replication, orders confirmed (HTTP 2xx) during "
        "the window between primary loss and promotion may be missing on the "
        "promoted standby. Re-run with synchronous_standby_names='*' on the "
        "primary to observe RPO ≈ 0 — and that the primary blocks when the "
        "standby is gone.",
    )

    customer_id, product_id = _seed()
    client = httpx.Client(base_url=APP_URL, timeout=10)

    confirmed: list[int] = []
    errors = 0
    stop_at = time.monotonic() + args.duration

    _log(f"placing orders for {args.duration}s, then promoting {DB_REPLICA_SERVICE} ...")

    # Writer loop in the foreground; promote halfway through.
    promote_at = time.monotonic() + (args.duration / 2)
    promoted = False
    while time.monotonic() < stop_at:
        if not promoted and time.monotonic() >= promote_at:
            _log("promoting standby (pg_ctl promote) and stopping primary writes path...")
            # Stop the primary so new commits can't reach it; promote the standby.
            subprocess.run(
                ["docker", "compose", "stop", "db"],
                check=False,
            )
            promo = subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    DB_REPLICA_SERVICE,
                    "pg_ctl",
                    "promote",
                    "-D",
                    "/var/lib/postgresql/data",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            _log(f"promote rc={promo.returncode} stdout={promo.stdout.strip()}")
            promoted = True

        try:
            started = time.monotonic()
            resp = client.post(
                "/orders",
                json={
                    "customer_id": customer_id,
                    "items": [{"product_id": product_id, "quantity": 1}],
                },
            )
            if 200 <= resp.status_code < 300:
                confirmed.append(resp.json()["id"])
            else:
                errors += 1
            _ = time.monotonic() - started
        except httpx.HTTPError:
            errors += 1
        time.sleep(args.poll_interval)

    _log(f"HTTP-confirmed order ids: {len(confirmed)} (errors/timeouts: {errors})")

    # After promotion the former replica is writable. Count how many of the
    # confirmed ids survived on it — that gap is the RPO.
    survived = 0
    for oid in confirmed:
        try:
            found = _replica_psql(f"SELECT id FROM orders WHERE id = {int(oid)}")
            if found:
                survived += 1
        except RuntimeError:
            pass

    lost = len(confirmed) - survived
    _log(f"survived on promoted standby: {survived}/{len(confirmed)} (lost={lost})")
    if lost > 0:
        _log(
            "RPO > 0 under asynchronous replication — expected. Re-run with "
            "synchronous commit on the primary to drive lost → 0 (and watch "
            "the primary stall if the standby is stopped)."
        )
    else:
        _log(
            "RPO ≈ 0 observed (no confirmed orders lost). Either the promote "
            "window was quiet, or synchronous replication is in effect."
        )
    _log(
        "NOTE: this drill leaves Compose in a broken state (primary stopped, "
        "replica promoted). Recreate with: docker compose down -v && docker compose up -d"
    )
    return 0


# ---------------------------------------------------------------------------
# failover-aws — RTO experiment
# ---------------------------------------------------------------------------


def cmd_failover_aws(args: argparse.Namespace) -> int:
    _header(
        "AWS Multi-AZ forced failover (RTO experiment)",
        f"aws rds reboot-db-instance --force-failover should complete with "
        f"client-observed downtime well under the {RTO_BUDGET_SECONDS:.0f}s RTO "
        "budget, and every HTTP-2xx-confirmed order should still exist afterwards "
        "(RPO ≈ 0 — Multi-AZ commit is synchronous).",
    )

    customer_id, product_id = _seed()
    client = httpx.Client(base_url=APP_URL, timeout=15)

    confirmed: list[int] = []
    downtime_started: float | None = None
    downtime_ended: float | None = None
    health_flips = 0

    _log(f"triggering failover on {RDS_INSTANCE_ID} ...")
    reboot = subprocess.run(
        [
            "aws",
            "rds",
            "reboot-db-instance",
            "--db-instance-identifier",
            RDS_INSTANCE_ID,
            "--force-failover",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if reboot.returncode != 0:
        _log(f"FAIL: aws rds reboot-db-instance: {reboot.stderr.strip()}")
        return 1
    failover_started = time.monotonic()
    _log("failover requested; polling /health + POST /orders ...")

    deadline = failover_started + args.timeout
    while time.monotonic() < deadline:
        # Shallow health — ALB uses this; should stay 200 through the flap
        # once the app process itself is fine (DB errors are per-request).
        try:
            h = client.get("/health", timeout=5)
            if h.status_code != 200:
                health_flips += 1
        except httpx.HTTPError:
            health_flips += 1

        try:
            resp = client.post(
                "/orders",
                json={
                    "customer_id": customer_id,
                    "items": [{"product_id": product_id, "quantity": 1}],
                },
                timeout=15,
            )
            if 200 <= resp.status_code < 300:
                confirmed.append(resp.json()["id"])
                if downtime_started is not None and downtime_ended is None:
                    downtime_ended = time.monotonic()
            else:
                if downtime_started is None:
                    downtime_started = time.monotonic()
        except httpx.HTTPError:
            if downtime_started is None:
                downtime_started = time.monotonic()

        # Stop once we've seen recovery and a few successful post-failover orders.
        if (
            downtime_started is not None
            and downtime_ended is not None
            and len(confirmed) >= args.min_success_after
        ):
            break
        time.sleep(args.poll_interval)

    if downtime_started is None:
        rto = 0.0
        _log("no client-visible downtime observed (very short failover or lucky timing)")
    elif downtime_ended is None:
        rto = time.monotonic() - downtime_started
        _log(f"FAIL: still down after {rto:.1f}s (budget {RTO_BUDGET_SECONDS:.0f}s)")
        return 1
    else:
        rto = downtime_ended - downtime_started

    _log(f"measured RTO ≈ {rto:.1f}s (budget {RTO_BUDGET_SECONDS:.0f}s)")
    _log(f"HTTP-confirmed orders during drill: {len(confirmed)}")
    _log(f"/health non-200 observations: {health_flips}")

    # RPO check: every confirmed order id must still be readable from the
    # (now failed-over) primary via the app.
    missing = 0
    for oid in confirmed:
        try:
            r = client.get(f"/orders/{oid}", timeout=10)
            if r.status_code != 200:
                missing += 1
        except httpx.HTTPError:
            missing += 1
    _log(f"confirmed orders still readable: {len(confirmed) - missing}/{len(confirmed)}")
    if missing:
        _log("FAIL: RPO > 0 — Multi-AZ should not lose committed transactions")
        return 1
    if rto > RTO_BUDGET_SECONDS:
        _log("FAIL: measured RTO exceeds budget")
        return 1
    _log("PASS: RTO within budget, RPO ≈ 0 for confirmed orders")
    return 0


# ---------------------------------------------------------------------------
# restore-pitr — why backups ≠ HA
# ---------------------------------------------------------------------------


def cmd_restore_pitr(args: argparse.Namespace) -> int:
    _header(
        "PITR restore timing (why backups cannot meet RTO ≤ 5 min)",
        "restore-db-instance-to-point-in-time creates a *new* instance with a "
        "*new* endpoint and typically takes tens of minutes. That is the "
        "concrete proof that backups defend against DELETE/corruption, not "
        "against AZ failure.",
    )

    describe = subprocess.run(
        [
            "aws",
            "rds",
            "describe-db-instances",
            "--db-instance-identifier",
            RDS_INSTANCE_ID,
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if describe.returncode != 0:
        _log(f"FAIL: describe-db-instances: {describe.stderr.strip()}")
        return 1

    body = json.loads(describe.stdout)
    inst = body["DBInstances"][0]
    latest = inst.get("LatestRestorableTime")
    _log(f"LatestRestorableTime = {latest}")
    _log(f"MultiAZ = {inst.get('MultiAZ')}")
    _log(f"BackupRetentionPeriod = {inst.get('BackupRetentionPeriod')}")

    target_id = args.target_identifier or f"{RDS_INSTANCE_ID}-pitr-{int(time.time())}"
    # Restore to ~5 minutes ago if LatestRestorableTime is a datetime; otherwise
    # use use-latest-restorable-time.
    cmd = [
        "aws",
        "rds",
        "restore-db-instance-to-point-in-time",
        "--source-db-instance-identifier",
        RDS_INSTANCE_ID,
        "--target-db-instance-identifier",
        target_id,
        "--use-latest-restorable-time",
        "--db-instance-class",
        inst.get("DBInstanceClass", "db.t3.micro"),
        "--no-multi-az",
        "--no-publicly-accessible",
        "--output",
        "json",
    ]
    _log("planned command:")
    _log("  " + " ".join(cmd))

    if not args.confirm:
        _log(
            "Dry run only. Pass --confirm to actually start the restore and "
            "time how long the new instance takes to become available. Remember "
            "to delete the target afterwards — it is a full billed instance."
        )
        return 0

    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    _log(f"starting restore at {started_at} ...")
    restore = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if restore.returncode != 0:
        _log(f"FAIL: restore-db-instance-to-point-in-time: {restore.stderr.strip()}")
        return 1

    # Poll until available or timeout.
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        d = subprocess.run(
            [
                "aws",
                "rds",
                "describe-db-instances",
                "--db-instance-identifier",
                target_id,
                "--query",
                "DBInstances[0].[DBInstanceStatus,Endpoint.Address]",
                "--output",
                "text",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        status_line = (d.stdout or "").strip()
        _log(f"target status: {status_line}")
        parts = status_line.split()
        if parts and parts[0] == "available":
            elapsed = time.monotonic() - started
            endpoint = parts[1] if len(parts) > 1 else "?"
            _log(f"restore available in {elapsed / 60:.1f} minutes")
            _log(f"NEW endpoint = {endpoint}")
            _log(
                f"Lesson: {elapsed / 60:.1f} min >> RTO budget "
                f"{RTO_BUDGET_SECONDS / 60:.1f} min, and the app still has to "
                "be repointed at the new endpoint. Backups ≠ HA."
            )
            return 0
        time.sleep(30)

    _log(f"FAIL: target not available within {args.timeout}s")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_lag = sub.add_parser("lag", help="Measure replica lag after a product write")
    p_lag.add_argument("--timeout", type=float, default=30.0)
    p_lag.add_argument("--poll-interval", type=float, default=0.05)
    p_lag.set_defaults(func=cmd_lag)

    p_promo = sub.add_parser(
        "promote-local",
        help="Promote the Compose standby while writing orders (RPO experiment)",
    )
    p_promo.add_argument("--duration", type=float, default=20.0)
    p_promo.add_argument("--poll-interval", type=float, default=0.2)
    p_promo.set_defaults(func=cmd_promote_local)

    p_fail = sub.add_parser(
        "failover-aws",
        help="Force Multi-AZ failover and measure RTO / RPO",
    )
    p_fail.add_argument("--timeout", type=float, default=600.0)
    p_fail.add_argument("--poll-interval", type=float, default=1.0)
    p_fail.add_argument("--min-success-after", type=int, default=3)
    p_fail.set_defaults(func=cmd_failover_aws)

    p_pitr = sub.add_parser(
        "restore-pitr",
        help="Time a PITR restore (dry-run unless --confirm)",
    )
    p_pitr.add_argument("--confirm", action="store_true")
    p_pitr.add_argument("--target-identifier", default=None)
    p_pitr.add_argument("--timeout", type=float, default=3600.0)
    p_pitr.set_defaults(func=cmd_restore_pitr)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
