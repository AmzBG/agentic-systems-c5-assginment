"""Cedar Crumb host: OpenAI Agents SDK, Gemini, and a stdio MCP client."""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agents import Agent, Model, ModelSettings, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from agents.mcp import MCPServerStdio
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAIError

from bakery import BEIRUT, inspect_inquiries, next_pickup_at
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
INSTRUCTIONS = """You are Nour, Cedar Crumb's bread guide and pickup assistant.
Be warm, precise, and concise. Use the tools for product names, prices, weights,
ingredients, allergens, policies, and saved inquiry details.
Use search_menu for information only. An empty query lists the full menu.
Use save_pickup_inquiry when the customer explicitly asks to save a known product ID.
Use prepare_pickup_inquiry when asked to find a product by name, flavor, or ingredient
and save its pickup inquiry. This workflow performs lookup and persistence together.
Ask for missing quantity, customer-provided name/email, pickup date/time, or consent.
Save only after explicit agreement to store contact details for pickup follow-up.
Use a new unique request_id of 8-64 allowed characters for a new request; reuse the
same reference when retrying identical details. Honor a supplied request reference.
Use Asia/Beirut for pickup times and pass an explicit ISO 8601 UTC offset.
Require at least 24 hours' notice; pickups run Monday-Saturday, 07:00 to before 19:00.
An inquiry has pending_confirmation status. State that it awaits staff confirmation;
do not claim confirmed stock, fulfillment, payment, email delivery, or notification.
Use the returned USD estimate rather than mental arithmetic. All breads contain gluten;
use the returned ingredient and shared-kitchen information for allergy questions.
If a query is ambiguous, ask the customer to choose; never select a loaf arbitrarily.
Explain errors and request a correction. Never describe a failed save as successful.
After a successful save, report the product, quantity, estimated total, pickup time,
inquiry reference, and pending confirmation. Acknowledge an already_saved retry.
"""


def tool_definition(tool) -> dict:
    return {"name": tool.name, "description": tool.description,
        "input_schema": getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {}),
        "output_schema": getattr(tool, "output_schema", None) or getattr(tool, "outputSchema", None)}


class ObservableMCPServer(MCPServerStdio):
    """Observe actual transport requests and distinguish cache hits from discovery."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, message_handler=self._notification, **kwargs)
        self.emit = lambda **event: None
        self.connection_id = uuid4().hex[:12]
        self.discovery_requests = 0
        self.tool_calls = 0
        self.turn = 0
        self.discovered_turn = None

    async def _notification(self, message):
        method = getattr(message, "method", None) or getattr(getattr(message, "root", None), "method", None)
        if method == "notifications/tools/list_changed":
            self.invalidate_tools_cache()
            self.emit(direction="MCP Server → MCP Client", operation=method, cache="invalidated")

    async def _list_tools_page(self, session, cursor=None):
        self.discovery_requests += 1
        self.emit(direction="MCP Client → MCP Server", operation="tools/list", transport="stdio", cursor=cursor)
        result = await super()._list_tools_page(session, cursor)
        self.discovered_turn = self.turn
        self.emit(direction="MCP Server → MCP Client", operation="tools/list result",
            definitions=[tool_definition(t) for t in result.tools])
        return result

    async def list_tools(self, *args, **kwargs):
        before = self.discovery_requests
        tools = await super().list_tools(*args, **kwargs)
        if {t.name for t in tools} != {"search_menu", "save_pickup_inquiry", "prepare_pickup_inquiry"}:
            raise RuntimeError("Unexpected MCP tool set; restart after reviewing the server configuration.")
        if self.discovery_requests == before:
            cross_turn = self.discovered_turn is not None and self.discovered_turn < self.turn
            self.emit(direction="MCP Client → Host", operation="cache/reuse",
                scope="cross-turn" if cross_turn else "within-turn",
                cache="Cached tool definitions reused" if cross_turn else "Tool definitions reused within current turn",
                tools=[t.name for t in tools], connection=self.connection_id)
        return tools

    async def call_tool(self, tool_name, arguments, meta=None):
        self.emit(direction="Agent → MCP Client", operation="tool selection", tool=tool_name, arguments=arguments)
        # SDK-side rejections must not be presented as server requests.
        self._validate_required_parameters(tool_name=tool_name, arguments=arguments)
        self.emit(direction="MCP Client → MCP Server", operation="tools/call", tool=tool_name, arguments=arguments)
        self.tool_calls += 1
        result = await super().call_tool(tool_name, arguments, meta)
        payload = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
        if payload is None:
            payload = decode_output(next((c.text for c in result.content if c.type == "text"), ""))
        self.emit(direction="MCP Server → MCP Client", operation="tools/call result", result=payload,
            is_error=bool(getattr(result, "is_error", False) or getattr(result, "isError", False)))
        return result


class ObservedModel(Model):
    """Record the exact function schemas supplied to each model request."""
    def __init__(self, delegate, emit):
        self.delegate, self.emit = delegate, emit

    def _request(self, args, kwargs):
        tools = kwargs.get("tools", args[3] if len(args) > 3 else [])
        self.emit(direction="Host → Model", operation="model/request",
            definitions=[{"name": t.name, "description": getattr(t, "description", None),
                "input_schema": getattr(t, "params_json_schema", None)} for t in tools])

    async def get_response(self, *args, **kwargs):
        self._request(args, kwargs)
        return await self.delegate.get_response(*args, **kwargs)

    async def stream_response(self, *args, **kwargs):
        self._request(args, kwargs)
        async for event in self.delegate.stream_response(*args, **kwargs):
            yield event


def connect_server(database: Path | str | None = None) -> ObservableMCPServer:
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    if database is not None:
        env["BAKERY_DB"] = str(Path(database).resolve())
    return ObservableMCPServer(name="Cedar Crumb MCP client",
        params={"command": sys.executable, "args": [str(ROOT / "server.py")], "cwd": str(ROOT), "env": env},
        cache_tools_list=True, client_session_timeout_seconds=30)


def build_agent(server: MCPServerStdio) -> Agent:
    load_dotenv(ROOT / ".env", override=False)
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY in .env. The offline tests and MCP smoke check do not require a key.")
    set_tracing_disabled(True)
    provider = AsyncOpenAI(api_key=key, base_url="https://generativelanguage.googleapis.com/v1beta/openai/", timeout=60, max_retries=1)
    model = OpenAIChatCompletionsModel(model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"), openai_client=provider)
    current = datetime.now(BEIRUT).isoformat()
    return Agent(name="Nour | Cedar Crumb", instructions=INSTRUCTIONS + f"\nCurrent Beirut date/time: {current}",
        model=model, model_settings=ModelSettings(parallel_tool_calls=False), mcp_servers=[server])


def decode_output(raw: Any) -> dict:
    """Normalize the SDK's text-content wrapper for display and local checks."""
    value = raw
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            pass
    if isinstance(value, dict) and value.get("type") == "text":
        value = value["text"]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"error": value.split(":", 1)[0], "message": value}
    return value if isinstance(value, dict) else {"content": value}


@dataclass
class Turn:
    history: list
    calls: list[str]
    outputs: list[dict]
    answer: str


async def run_turn(agent: Agent, history: list, prompt: str) -> Turn:
    print(f"\nCUSTOMER > {prompt}", flush=True)
    result = Runner.run_streamed(agent, history + [{"role": "user", "content": prompt}], max_turns=8)
    calls, outputs = [], []
    async for event in result.stream_events():
        if event.type != "run_item_stream_event":
            continue
        if event.name == "tool_called":
            raw = event.item.raw_item
            calls.append(getattr(raw, "name", "unknown"))
            print(f"AGENT -> MCP CLIENT -> SERVER: {calls[-1]} {getattr(raw, 'arguments', '')}", flush=True)
        elif event.name == "tool_output":
            data = decode_output(event.item.output)
            outputs.append(data)
            print("SERVER -> MCP CLIENT -> AGENT:", json.dumps(data, ensure_ascii=False), flush=True)
    print(f"NOUR > {result.final_output}", flush=True)
    return Turn(result.to_input_list(), calls, outputs, str(result.final_output))


def walkthrough_steps(pickup_at: str) -> list[tuple[str, str, str | None, int]]:
    customer = f"My name is Alex, email alex@example.com. Pickup: {pickup_at}. I agree to save my contact details for pickup follow-up."
    return [
        ("Which sourdough loaves contain sesame? Only show menu information; do not save a request.", "search_menu", None, 0),
        (f"Save a pickup inquiry for 2 loaves of product ID plain. {customer} Request reference cedar-action-01.", "save_pickup_inquiry", None, 1),
        (f"Find the Olive & Rosemary loaf and save a pickup inquiry for 3 loaves. {customer} Request reference cedar-workflow-01.", "prepare_pickup_inquiry", None, 2),
        (f"Find a chocolate croissant and save a pickup inquiry for 1. {customer} Request reference cedar-missing-01.", "prepare_pickup_inquiry", "NO_MATCH", 2),
        (f"Save an inquiry for 2 loaves of product ID plain again. This retries exactly the same request. {customer} Reuse request reference cedar-action-01.", "save_pickup_inquiry", None, 2),
    ]


async def main(args: argparse.Namespace) -> None:
    load_dotenv(ROOT / ".env", override=False)
    database = (args.database or ROOT / "data" / "pickup.sqlite3").resolve()
    if args.demo and database.exists() and inspect_inquiries(database):
        raise RuntimeError("Choose a fresh --database filename for the walkthrough so its row-count checks are reproducible.")
    async with connect_server(database) as server:
        tools = await server.list_tools()
        names = {tool.name for tool in tools}
        if names != {"search_menu", "save_pickup_inquiry", "prepare_pickup_inquiry"}:
            raise RuntimeError(f"Unexpected MCP tool set: {names}")
        print("MCP tools/list:", ", ".join(sorted(names)), flush=True)
        agent = build_agent(server)
        history: list = []
        if args.demo:
            first_id = None
            for index, (prompt, expected, error, expected_rows) in enumerate(walkthrough_steps(next_pickup_at())):
                turn = await run_turn(agent, history, prompt)
                history = turn.history
                if turn.calls != [expected] or len(turn.outputs) != 1:
                    raise RuntimeError(f"Step {index + 1}: expected {expected}, got {turn.calls}.")
                output = turn.outputs[0]
                if error and output.get("error") != error:
                    raise RuntimeError(f"Step {index + 1}: expected {error}, got {output}.")
                if not error and "error" in output:
                    raise RuntimeError(f"Step {index + 1} failed: {output}.")
                rows = inspect_inquiries(database)
                if len(rows) != expected_rows:
                    raise RuntimeError(f"Step {index + 1}: expected {expected_rows} saved inquiries, got {len(rows)}.")
                if index == 1:
                    first_id = output["inquiry_id"]
                    assert output["estimated_total_usd"] == "12.00"
                if index == 2:
                    assert output["steps"] == ["search_menu", "save_pickup_inquiry"]
                    assert output["inquiry"]["estimated_total_usd"] == "24.00"
                if index == 4:
                    assert output["inquiry_id"] == first_id and output["already_saved"]
                print(f"CHECK: correct tool; {len(rows)} saved inquiries.", flush=True)
            print("\nWalkthrough passed: three tools, a clear failure, persistent requests, and a safe retry.", flush=True)
        elif args.prompt:
            await run_turn(agent, history, args.prompt)
        else:
            print("Cedar Crumb | Good bread. Time well spent.\nType quit to exit.", flush=True)
            while True:
                try:
                    prompt = await asyncio.to_thread(input, "\nYou: ")
                except EOFError:
                    break
                if prompt.strip().casefold() in {"quit", "exit"}:
                    break
                if prompt.strip():
                    history = (await run_turn(agent, history, prompt)).history


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="Run a checked live walkthrough with a fresh database.")
    mode.add_argument("--prompt")
    parser.add_argument("--database", type=Path)
    try:
        asyncio.run(main(parser.parse_args()))
    except (RuntimeError, OSError, OpenAIError, AssertionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
