"""V7: Order must not import Product models (database-per-service)."""

import ast
from pathlib import Path


def test_order_service_does_not_import_product_models():
    path = Path(__file__).resolve().parents[2] / "app" / "order" / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("app.product"), (
                f"order.service must not import {node.module}"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.product"), (
                    f"order.service must not import {alias.name}"
                )


def test_order_service_uses_http_clients():
    source = (
        Path(__file__).resolve().parents[2] / "app" / "order" / "service.py"
    ).read_text(encoding="utf-8")
    assert "product_client" in source
    assert "payment_client" in source
