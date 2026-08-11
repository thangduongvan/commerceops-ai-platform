import logging

logger = logging.getLogger("commerceops.analytics")


def record_event(event_type: str, payload: dict) -> None:
    """V4 stand-in analytics sink: a structured log line.

    Same minimal shape as app/notification/service.py's V0 stand-in — this
    module has no models/router yet, only the fan-out handler app/worker.py
    calls for every order event. A later version can swap this for a real
    analytics pipeline without touching the producer or the worker's
    dispatch logic.
    """
    logger.info("analytics event=%s payload=%s", event_type, payload)
