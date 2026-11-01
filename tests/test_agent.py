"""Repeatable streaming SDK checks with a model test double."""
import json

import pytest
from agents import Agent, Model, set_tracing_disabled
from openai.types.responses import Response, ResponseCompletedEvent, ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from bakery import inspect_inquiries, next_pickup_at
from client import INSTRUCTIONS, connect_server, decode_output, run_turn


class FixedCallModel(Model):
    def __init__(self, tool, arguments, expected):
        self.tool, self.arguments, self.expected = tool, arguments, expected
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        raise NotImplementedError("The test exercises the streaming path.")

    async def stream_response(self, system_instructions, input, model_settings, tools, *args, **kwargs):
        self.calls += 1
        assert {t.name for t in tools} == {"search_menu", "save_pickup_inquiry", "prepare_pickup_inquiry"}
        if self.calls == 1:
            output = [ResponseFunctionToolCall(type="function_call", call_id="cedar-test-call", name=self.tool, arguments=json.dumps(self.arguments))]
        else:
            returned = [i for i in input if isinstance(i, dict) and i.get("type") == "function_call_output"]
            assert returned and self.expected in str(returned[-1]["output"])
            output = [ResponseOutputMessage(id="cedar-test-message", type="message", role="assistant", status="completed",
                content=[ResponseOutputText(type="output_text", text="The MCP result was received.", annotations=[])])]
        response = Response.model_construct(id=f"cedar-test-response-{self.calls}", output=output,
            usage=None, model="test-double", created_at=0, object="response", status="completed")
        yield ResponseCompletedEvent(type="response.completed", sequence_number=0, response=response)


@pytest.mark.parametrize("tool, extra, expected, row_count", [
    ("search_menu", {"query": "sesame"}, "zaatar-sesame", 0),
    ("save_pickup_inquiry", {"product_id": "plain"}, "12.00", 1),
    ("prepare_pickup_inquiry", {"query": "rosemary"}, "16.00", 1),
    ("prepare_pickup_inquiry", {"query": "chocolate croissant"}, "NO_MATCH", 0),
])
async def test_streaming_host_client_server_loop(tmp_path, tool, extra, expected, row_count):
    set_tracing_disabled(True)
    arguments = {"quantity": 2, "customer": {"name": "Alex", "email": "alex@example.com", "consent": True},
        "pickup_at": next_pickup_at(), "request_id": "cedar-agent-01"} | extra
    if tool == "search_menu":
        arguments = extra
    model = FixedCallModel(tool, arguments, expected)
    database = tmp_path / "agent.sqlite3"
    async with connect_server(database) as server:
        agent = Agent(name="Cedar Crumb", instructions=INSTRUCTIONS, model=model, mcp_servers=[server])
        turn = await run_turn(agent, [], "Check the requested operation.")
    assert turn.calls == [tool] and len(turn.outputs) == 1
    assert model.calls == 2 and turn.history
    assert len(inspect_inquiries(database)) == row_count
    if expected == "NO_MATCH":
        assert turn.outputs[0]["error"] == "NO_MATCH"


def test_output_normalization():
    assert decode_output("{'type': 'text', 'text': '{\"value\": 2}'}") == {"value": 2}
    assert decode_output("NO_MATCH: No product found.")["error"] == "NO_MATCH"
