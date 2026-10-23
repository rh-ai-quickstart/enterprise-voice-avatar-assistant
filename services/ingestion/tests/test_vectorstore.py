"""The collection is created once even when two ingestion jobs start on an empty Qdrant."""

import threading
import time

import pytest
from qdrant_client.http.exceptions import UnexpectedResponse


class FakeQdrant:
    def __init__(self, exists: bool = False, create_error: int | None = None) -> None:
        self.exists = exists
        self.create_error = create_error
        self.created = 0
        self.indexes = 0

    def collection_exists(self, name: str) -> bool:
        exists = self.exists
        time.sleep(0.05)  # widen the window between the check and the creation
        return exists

    def create_collection(self, **kwargs) -> None:
        if self.create_error:
            raise UnexpectedResponse(self.create_error, "error", b"{}", None)
        if self.exists:
            raise UnexpectedResponse(409, "Conflict", b"already exists", None)
        self.exists = True
        self.created += 1

    def create_payload_index(self, **kwargs) -> None:
        self.indexes += 1


@pytest.fixture
def fake(monkeypatch):
    from app import vectorstore

    def use(qdrant: FakeQdrant) -> FakeQdrant:
        monkeypatch.setattr(vectorstore, "client", lambda: qdrant)
        return qdrant

    return use


def test_two_jobs_on_an_empty_qdrant_create_the_collection_once(fake):
    from app import vectorstore

    qdrant = fake(FakeQdrant())
    errors: list[Exception] = []

    def job() -> None:
        try:
            vectorstore.ensure_collection(1024)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=job) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert (qdrant.created, qdrant.indexes) == (1, 1)


def test_a_collection_created_meanwhile_elsewhere_is_accepted(fake):
    from app import vectorstore

    qdrant = fake(FakeQdrant(create_error=409))
    vectorstore.ensure_collection(1024)
    assert qdrant.indexes == 0


def test_other_qdrant_errors_still_fail_the_job(fake):
    from app import vectorstore

    fake(FakeQdrant(create_error=500))
    with pytest.raises(UnexpectedResponse):
        vectorstore.ensure_collection(1024)
