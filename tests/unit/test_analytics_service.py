import logging

from app.analytics.service import record_event


def test_record_event_logs_event(caplog):
    with caplog.at_level(logging.INFO, logger="commerceops.analytics"):
        record_event("OrderCreated", {"order_id": 1})

    assert any("OrderCreated" in record.message for record in caplog.records)
