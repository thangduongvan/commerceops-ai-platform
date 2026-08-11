import logging

from app.search.service import index_event


def test_index_event_logs_event(caplog):
    with caplog.at_level(logging.INFO, logger="commerceops.search"):
        index_event("OrderCreated", {"order_id": 1})

    assert any("OrderCreated" in record.message for record in caplog.records)
