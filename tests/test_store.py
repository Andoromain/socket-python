from socket_chat.store import MessageRecord, Status, Store

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


def msg(i, contact=A, outgoing=True, ts=None, status=Status.PENDING):
    return MessageRecord(
        f"00000000-0000-4000-8000-{i:012d}", contact, outgoing, f"m{i}", ts or i, status
    )


def test_contacts_upsert_keeps_known_address(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    store.upsert_contact(A, "Alice", host="10.0.0.1", port=12800, last_seen=5)
    store.upsert_contact(A, "Alice Martin")  # nom mis à jour, adresse conservée
    contact = store.contact(A)
    assert (contact.name, contact.host, contact.port, contact.last_seen) == (
        "Alice Martin",
        "10.0.0.1",
        12800,
        5,
    )
    store.upsert_contact(A, "Alice Martin", last_seen=1)
    assert store.contact(A).last_seen == 5  # jamais de retour en arrière
    assert store.contact(B) is None


def test_persistence_across_reopen(tmp_path):
    path = tmp_path / "h.sqlite3"
    store = Store(path)
    store.upsert_contact(A, "Alice")
    store.add_message(msg(1))
    store.close()
    reopened = Store(path)
    assert [m.text for m in reopened.messages(A)] == ["m1"]


def test_messages_order_duplicates_and_limit():
    store = Store()
    store.upsert_contact(A, "Alice")
    assert store.add_message(msg(2, ts=20))
    assert store.add_message(msg(1, ts=10))
    assert not store.add_message(msg(1, ts=10))  # doublon ignoré
    assert [m.text for m in store.messages(A)] == ["m1", "m2"]
    assert [m.text for m in store.messages(A, limit=1)] == ["m2"]


def test_status_only_moves_forward():
    store = Store()
    store.upsert_contact(A, "Alice")
    record = msg(1)
    store.add_message(record)
    assert store.update_status(record.id, Status.DELIVERED)
    assert not store.update_status(record.id, Status.SENT)
    assert store.update_status(record.id, Status.READ)
    assert store.message(record.id).status is Status.READ
    assert not store.update_status("inconnu", Status.READ)


def test_undelivered_and_unread():
    store = Store()
    store.upsert_contact(A, "Alice")
    store.upsert_contact(B, "Bob")
    store.add_message(msg(1, status=Status.PENDING))
    store.add_message(msg(2, status=Status.SENT))
    store.add_message(msg(3, status=Status.DELIVERED))
    store.add_message(msg(4, contact=B, outgoing=False, status=Status.DELIVERED))
    store.add_message(msg(5, contact=B, outgoing=False, status=Status.DELIVERED))
    assert [m.text for m in store.undelivered_outgoing(A)] == ["m1", "m2"]
    assert store.contacts_with_undelivered() == [A]
    assert store.total_unread() == 2
    assert len(store.mark_incoming_read(B)) == 2
    assert store.mark_incoming_read(B) == []
    assert store.total_unread() == 0


def test_contact_summaries_sorted_by_activity():
    store = Store()
    store.upsert_contact(A, "Alice", last_seen=1)
    store.upsert_contact(B, "Bob", last_seen=1)
    store.add_message(msg(1, contact=B, outgoing=False, ts=100, status=Status.DELIVERED))
    store.add_message(msg(2, contact=A, ts=50))
    summaries = store.contacts()
    assert [s.contact.name for s in summaries] == ["Bob", "Alice"]
    assert summaries[0].unread == 1 and summaries[0].last_message.text == "m1"
    assert summaries[1].unread == 0 and summaries[1].last_message.outgoing
    store.delete_contact(B)
    assert [s.contact.name for s in store.contacts()] == ["Alice"]
