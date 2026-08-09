def test_product_crud_flow(client):
    create_resp = client.post(
        "/products",
        json={"name": "Widget", "description": "A widget", "price": 9.99, "stock_quantity": 10},
    )
    assert create_resp.status_code == 201
    product = create_resp.json()

    list_resp = client.get("/products")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    get_resp = client.get(f"/products/{product['id']}")
    assert get_resp.status_code == 200

    update_resp = client.put(f"/products/{product['id']}", json={"price": 12.5})
    assert update_resp.status_code == 200
    assert update_resp.json()["price"] == 12.5


def test_get_missing_product_returns_404(client):
    response = client.get("/products/12345")
    assert response.status_code == 404
