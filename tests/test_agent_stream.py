from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from amiagi.interfaces.web.ws.agent_stream import AgentStream


class _FakeAdapter:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit_user_turn(self, text: str) -> None:
        self.submitted.append(text)


@pytest.mark.asyncio
async def test_agent_stream_wraps_agent_prompt() -> None:
    websocket = AsyncMock()
    stream = AgentStream(websocket, "polluks")
    adapter = _FakeAdapter()

    await stream._handle_client_message(
        json.dumps({"type": "user_prompt", "message": "Zacznij analizę"}),
        adapter,
    )

    assert adapter.submitted == ["[Sponsor -> polluks] Zacznij analizę"]
    websocket.send_text.assert_awaited()
    payload = json.loads(websocket.send_text.await_args_list[0].args[0])
    assert payload["type"] == "prompt_ack"


@pytest.mark.asyncio
async def test_agent_stream_preserves_addressed_prompt() -> None:
    websocket = AsyncMock()
    stream = AgentStream(websocket, "kastor")
    adapter = _FakeAdapter()

    prompt = "[Sponsor -> Kastor] Oceń plan"
    await stream._handle_client_message(
        json.dumps({"type": "user_prompt", "message": prompt}),
        adapter,
    )

    assert adapter.submitted == [prompt]