import pytest
from proton_rag import generation
from proton_rag.budget import Ledger, BudgetError

HITS = [{"synthetic": True, "text": "Cobalt Tuesday", "citation": "imap://Folders/test/1/7#abc"}]


class Response:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        pass

    def json(self):
        return self.value


class Client:
    pricing = {"prompt": "0.0000004", "completion": "0.0000016"}
    sent = []
    fail = False

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url):
        return Response({"data": [{"id": generation.MODEL, "pricing": self.pricing}]})

    async def post(self, url, **kwargs):
        self.sent.append(kwargs["json"])
        if self.fail:
            raise TimeoutError()
        return Response(
            {"usage": {"cost": 0.0001}, "choices": [{"message": {"content": "Tuesday [source]"}}]}
        )


@pytest.fixture
def mocked(monkeypatch, tmp_path):
    Client.sent = []
    Client.fail = False
    Client.pricing = {"prompt": "0.0000004", "completion": "0.0000016"}
    monkeypatch.setattr(generation.httpx, "AsyncClient", Client)
    return Ledger(tmp_path / "budget.db", initialize=True)


async def test_generation_is_explicit_bounded_and_debited(mocked):
    value = await generation.answer("When?", HITS, mocked, "synthetic-test-key")
    assert value["cost_usd"] == 0.0001 and mocked.used() > 100
    assert len(Client.sent) == 1
    body = Client.sent[0]
    assert body["max_tokens"] == 512
    assert body["provider"]["allow_fallbacks"] is False
    assert "untrusted_excerpts" in body["messages"][1]["content"]
    assert not any(k in body for k in ["tools", "plugins"])


async def test_unknown_pricing_sends_nothing(mocked):
    Client.pricing = {}
    with pytest.raises(BudgetError):
        await generation.answer("When?", HITS, mocked, "synthetic-test-key")
    assert not Client.sent and mocked.used() == 0


async def test_private_context_refused_before_network(mocked):
    with pytest.raises(BudgetError):
        await generation.answer("When?", [{**HITS[0], "synthetic": False}], mocked, "test")
    assert not Client.sent and mocked.used() == 0


async def test_retry_reserves_again_after_uncertain_failure(mocked):
    Client.fail = True
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await generation.answer("When?", HITS, mocked, "synthetic-test-key")
    assert len(Client.sent) == 2
    with mocked.connect() as db:
        assert db.execute("SELECT count(*) FROM reservations WHERE pending=1").fetchone()[0] == 2
