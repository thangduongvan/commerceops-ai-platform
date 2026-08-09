import logging

from app.notification.service import send_notification


def test_send_notification_logs_event(caplog):
    with caplog.at_level(logging.INFO, logger="commerceops.notification"):
        send_notification("OrderCreated", {"order_id": 1})

    assert any("OrderCreated" in record.message for record in caplog.records)
