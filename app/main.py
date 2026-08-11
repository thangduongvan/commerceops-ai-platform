"""Compatibility shim — V7 runs three service entrypoints.

Prefer:
  uvicorn app.product.main:app
  uvicorn app.order.main:app
  uvicorn app.payment.main:app

This module re-exports the Order app so older docs/commands that still
point at `app.main:app` keep working against the customer/order surface
(not a full monolith). Integration tests import the per-service apps
directly.
"""

from app.order.main import app

__all__ = ["app"]
