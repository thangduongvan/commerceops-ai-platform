def test_create_and_get_customer(client):
    response = client.post("/customers", json={"name": "Alice", "email": "alice@example.com"})
    assert response.status_code == 201
    customer = response.json()
    assert customer["name"] == "Alice"

    fetched = client.get(f"/customers/{customer['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["email"] == "alice@example.com"


def test_get_missing_customer_returns_404(client):
    response = client.get("/customers/9999")
    assert response.status_code == 404


def test_create_customer_duplicate_email_conflicts(client):
    client.post("/customers", json={"name": "Bob", "email": "bob@example.com"})
    response = client.post("/customers", json={"name": "Bob2", "email": "bob@example.com"})
    assert response.status_code == 409
