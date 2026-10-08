import pytest
from proton_rag import generation
from proton_rag.config import Settings


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {"usage": {"cost": 0.0001}, "choices": [{"message": {"content": "Tuesday"}}]}


class Client:
    sent = []
    fail = False

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, **kwargs):
        self.sent.append(kwargs["json"])
        if self.fail:
            raise TimeoutError()
        return Response()


@pytest.fixture
def mocked(monkeypatch):
    Client.sent = []
    Client.fail = False
    monkeypatch.setattr(generation.httpx, "AsyncClient", Client)


async def test_real_mail_generation_configurable_without_ledger(mocked):
    hits = [{"text": "Tuesday", "citation": "imap:///INBOX/1/7#abc"}]
    value = await generation.answer(
        "When?", hits, "key", Settings(model="chosen/model", max_output=1500)
    )
    assert value["answer"] == "Tuesday" and value["cost_usd"] == 0.0001
    assert Client.sent[0]["model"] == "chosen/model"
    assert Client.sent[0]["max_tokens"] == 1500
    assert "max_price" not in Client.sent[0].get("provider", {})
    assert value["citations"] == [hits[0]["citation"]]


async def test_no_evidence_makes_no_paid_request(mocked):
    value = await generation.answer("When?", [], "key")
    assert value["citations"] == [] and not Client.sent


async def test_network_failure_does_not_retry(mocked):
    Client.fail = True
    with pytest.raises(TimeoutError):
        await generation.answer("When?", [{"text": "A", "citation": "x"}], "key")
    assert len(Client.sent) == 1
