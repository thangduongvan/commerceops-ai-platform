import logging

logger = logging.getLogger("commerceops.search")


def index_event(event_type: str, payload: dict) -> None:
    """V4 stand-in search-indexing channel: a structured log line.

    Same minimal shape as app/notification/service.py's V0 stand-in. A
    later version can swap this for a real search index (e.g. OpenSearch)
    without touching the producer or the worker's dispatch logic.
    """
    logger.info("search-index event=%s payload=%s", event_type, payload)
