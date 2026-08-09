from app.order.service import _line_total


def test_line_total_computes_price_times_quantity():
    assert _line_total(9.99, 3) == 29.97


def test_line_total_rounds_to_two_decimals():
    assert _line_total(0.1, 3) == 0.3
