import logging

logger = logging.getLogger("commerceops.notification")


def send_notification(event_type: str, payload: dict) -> None:
    """V0 stand-in notification channel: a structured log line.

    Callers only depend on this function signature, so a later version
    can swap in a real channel (email, SNS, ...) without touching the
    modules that trigger notifications.

    V4: no longer called directly from app/order/service.py — it's now one
    of the fan-out handlers app/worker.py invokes for every order event it
    consumes off the queue, alongside send_email/analytics/search below.
    """
    logger.info("notification event=%s payload=%s", event_type, payload)


def send_email(event_type: str, payload: dict) -> None:
    """V4 stand-in email channel: a structured log line, same shape as
    send_notification above. A later version can swap this for a real
    provider (SES, ...) without touching app/worker.py.
    """
    logger.info("email event=%s payload=%s", event_type, payload)
