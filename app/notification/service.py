import logging

logger = logging.getLogger("commerceops.notification")


def send_notification(event_type: str, payload: dict) -> None:
    """V0 stand-in notification channel: a structured log line.

    Callers only depend on this function signature, so a later version
    can swap in a real channel (email, SNS, ...) without touching the
    modules that trigger notifications.
    """
    logger.info("notification event=%s payload=%s", event_type, payload)
