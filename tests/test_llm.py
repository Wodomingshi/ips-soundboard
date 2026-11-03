from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ips_soundboard.llm import LLMError, ask


class Reply(BaseModel):
    answer: str


class RawClient:
    """Returns raw text replies with a stop reason, like the API."""

    def __init__(self, *replies: tuple[str, str]) -> None:
        self.replies, self.calls, self.messages = list(replies), [], self

    def create(self, **request):
        self.calls.append(request)
        text, stop = self.replies.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop)


def call(client: RawClient) -> Reply:
    return ask(client, "m", "system", [{"role": "user", "content": "hi"}], Reply)


def test_valid_reply_is_parsed_and_schema_is_sent() -> None:
    client = RawClient(('{"answer": "yes"}', "end_turn"))
    assert call(client).answer == "yes"
    assert client.calls[0]["output_config"]["format"]["schema"]["properties"]["answer"]["type"] == "string"


def test_malformed_reply_is_asked_for_once_more() -> None:
    client = RawClient(('{"answer": "ye', "end_turn"), ('{"answer": "yes"}', "end_turn"))
    assert call(client).answer == "yes" and len(client.calls) == 2


def test_malformed_twice_is_a_clear_error() -> None:
    client = RawClient(('{"answer": "ye', "end_turn"), ('{"answer": "ye', "end_turn"))
    with pytest.raises(LLMError, match="not valid twice .*stop reason 'end_turn', 14 characters"):
        call(client)


def test_cut_off_reply_is_reported_not_retried() -> None:
    client = RawClient(('{"answer": "ye', "max_tokens"))
    with pytest.raises(LLMError, match="cut off at the 16000-token output limit"):
        call(client)
    assert len(client.calls) == 1


def test_refusal_is_reported() -> None:
    with pytest.raises(LLMError, match="declined"):
        call(RawClient(("", "refusal")))
