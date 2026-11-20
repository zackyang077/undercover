from backend.db import key, records
from backend.storage import Store


def test_old_handlers_gain_email_and_current_authority_without_reseeding(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3")
    store.initialize()
    with store.connection(write=True) as connection:
        connection.execute(records.delete().where(key("migrations", "handler_sign_in")))
        for handler in store.all("handlers"):
            handler.pop("email")
            handler["can_approve_over_limit"] = True
            store.update(connection, "handlers", handler["handler_id"], handler)
        store.insert(connection, "sign_offs", "prior", {"handler_id": "HND-02"})
    store.initialize()
    dana, marcus, priya = store.all("handlers")
    assert dana["can_approve_over_limit"]
    assert not marcus["can_approve_over_limit"]
    assert not priya["can_approve_over_limit"]
    assert marcus["email"] == "marcus.oyelaran@undercover.example"
    assert store.get("sign_offs", "prior") == {"handler_id": "HND-02"}
