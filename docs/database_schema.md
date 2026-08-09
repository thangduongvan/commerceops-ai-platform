# V0 Database Schema

Single PostgreSQL database, shared by all modules (database-per-service is introduced later, at V7).

```mermaid
erDiagram
    customers ||--o{ orders : places
    orders ||--o{ order_items : contains
    products ||--o{ order_items : "referenced by"

    customers {
        int id PK
        string name
        string email
        datetime created_at
    }

    products {
        int id PK
        string name
        string description
        numeric price
        int stock_quantity
        datetime created_at
        datetime updated_at
    }

    orders {
        int id PK
        int customer_id FK
        string status
        numeric total_amount
        datetime created_at
        datetime updated_at
    }

    order_items {
        int id PK
        int order_id FK
        int product_id FK
        int quantity
        numeric unit_price
    }
```

## Notes

* `order_items.unit_price` is copied from `products.price` at order creation time, so historical orders are unaffected by later price changes.
* `orders.status` is one of `PENDING`, `PAID`, `PAYMENT_FAILED`, `CANCELLED`.
* No migration tool (e.g. Alembic) is used in V0 — `Base.metadata.create_all()` runs on app startup. This is a deliberate simplification (see [ADR-001](adr/ADR-001-modular-monolith.md)); a real migration tool becomes necessary once the schema needs to evolve without dropping data.
