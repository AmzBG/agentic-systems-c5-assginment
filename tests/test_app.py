"""Verify the chat callback across real MCP transport and durable session state."""
import json
from pathlib import Path

import pytest
from agents import Agent

import app
from client import INSTRUCTIONS
from test_agent import FixedCallModel
from bakery import next_pickup_at


@pytest.fixture(autouse=True)
async def close_chat_connections():
    yield
    runtimes = list(app.RUNTIMES.values())
    app.RUNTIMES.clear()
    for runtime in runtimes:
        await runtime.close()
        assert runtime.close_error is None


@pytest.mark.parametrize("tool,extra,expected,count", [
    ("search_menu", {"query": "sesame"}, "zaatar-sesame", 0),
    ("save_pickup_inquiry", {"product_id": "plain"}, "12.00", 1),
    ("prepare_pickup_inquiry", {"query": "rosemary"}, "16.00", 1),
    ("prepare_pickup_inquiry", {"query": "croissant"}, "NO_MATCH", 0),
])
async def test_chat_round_trip(monkeypatch, tmp_path, tool, extra, expected, count):
    arguments = {"quantity": 2, "customer": {"name": "Alex", "email": "alex@example.com", "consent": True},
                 "pickup_at": next_pickup_at(), "request_id": "cedar-ui-01"} | extra
    if tool == "search_menu":
        arguments = extra
    model = FixedCallModel(tool, arguments, expected)
    monkeypatch.setattr(app, "build_agent", lambda server: Agent(name="Nour", instructions=INSTRUCTIONS, model=model, mcp_servers=[server]))
    state = {"history": [], "database": str(tmp_path / "chat.sqlite3"), "events": []}
    updates = [item async for item in app.respond("Please check this request", [], state)]
    _, chat, updated, events, rows = updates[-1]
    assert chat[-1]["content"] == "The MCP result was received."
    assert updated["history"] and len(rows) == count
    assert next(e for e in events if "tool" in e)["tool"] == tool
    assert expected in json.dumps(next(e for e in events if "result" in e))
    assert events[0]["direction"] == "Customer → Host"
    assert events[-1]["direction"] == "Host → Customer"
    assert len({e["request"] for e in events}) == 1


def test_sessions_are_isolated():
    first, second = app.new_session(), app.new_session()
    assert first["database"] != second["database"]
    first["history"].append("one")
    assert second["history"] == []


def test_interface_builds():
    interface = app.create_app()
    assert interface.config["title"] == "Cedar Crumb"


async def test_empty_message_does_not_call_provider():
    state = app.new_session()
    updates = [item async for item in app.respond(" ", [], state)]
    assert len(updates) == 1 and not updates[0][1] and not updates[0][3]


async def test_failure_preserves_history_and_hides_exception(monkeypatch, tmp_path):
    def unavailable(server):
        raise RuntimeError("private provider diagnostic")
    monkeypatch.setattr(app, "build_agent", unavailable)
    prior = [{"role": "user", "content": "Earlier request"}]
    state = {"history": prior.copy(), "database": str(tmp_path / "failed.sqlite3"), "events": []}
    updates = [item async for item in app.respond("New request", [], state)]
    assert "could not finish" in updates[-1][1][-1]["content"]
    assert "private provider diagnostic" not in str(updates[-1])
    assert state["history"] == prior and updates[-1][-1] == []


def menu_agent(monkeypatch):
    monkeypatch.setattr(app, "build_agent", lambda server: Agent(name="Nour", instructions=INSTRUCTIONS,
        model=FixedCallModel("search_menu", {"query": "sesame"}, "zaatar-sesame"), mcp_servers=[server]))


def chat_state(tmp_path, name="cache"):
    return {"history": [], "database": str(tmp_path / f"{name}.sqlite3"), "events": []}


async def ask(state, message="Show sesame breads", chat=None):
    return [update async for update in app.respond(message, chat or [], state)][-1]


async def test_discovery_once_across_turns_and_complete_trace(monkeypatch, tmp_path):
    menu_agent(monkeypatch)
    state = chat_state(tmp_path)
    first = await ask(state)
    runtime = app.runtime_for(state)
    server = runtime.server
    assert server.discovery_requests == 1 and server.tool_calls == 1
    first_events = list(state["events"])
    assert not any(e.get("scope") == "cross-turn" for e in first_events)
    discovery = next(e for e in first_events if e.get("operation") == "tools/list result")
    assert len(discovery["definitions"]) == 3
    assert all(t["description"] and t["input_schema"] and t["output_schema"] for t in discovery["definitions"])
    model_request = next(e for e in first_events if e.get("operation") == "model/request")
    assert {t["name"] for t in model_request["definitions"]} == set(app.CATEGORIES)
    assert all(t["input_schema"] for t in model_request["definitions"])
    operations = [e.get("operation") for e in first_events]
    assert operations.index("tools/list") < operations.index("tools/list result") < operations.index("model/request")
    assert operations.index("tool selection") < operations.index("tools/call") < operations.index("tools/call result") < operations.index("tool result delivered")
    await ask(state, "Show them again", first[1])
    assert runtime is app.runtime_for(state) and server is runtime.server
    assert server.discovery_requests == 1 and server.tool_calls == 2
    later = state["events"][len(first_events):]
    assert any(e.get("scope") == "cross-turn" for e in later)
    assert not any(e.get("operation") == "tools/list" for e in later)
    assert len({e["request"] for e in state["events"]}) == 2


async def test_reset_closes_in_owner_task_and_new_session_discovers(monkeypatch, tmp_path):
    import asyncio
    from contextlib import asynccontextmanager
    import session_runtime
    original = session_runtime.connect_server
    owners = []
    @asynccontextmanager
    async def tracked(database):
        enter = asyncio.current_task()
        async with original(database) as server:
            yield server
        owners.append((enter, asyncio.current_task(), server.session))
    monkeypatch.setattr(session_runtime, "connect_server", tracked)
    menu_agent(monkeypatch)
    state = chat_state(tmp_path)
    await ask(state)
    old = app.runtime_for(state)
    _, chat, fresh, events, rows = await app.reset_session(state)
    assert old.owner.done() and old.server is None and old.close_error is None
    assert owners == [(old.owner, old.owner, None)]
    assert not chat and not events and not rows
    fresh["database"] = str(tmp_path / "fresh.sqlite3")
    await ask(fresh)
    assert app.runtime_for(fresh).server.discovery_requests == 1
    assert not any(e.get("scope") == "cross-turn" for e in fresh["events"])


async def test_session_caches_are_isolated(monkeypatch, tmp_path):
    menu_agent(monkeypatch)
    a, b = chat_state(tmp_path, "a"), chat_state(tmp_path, "b")
    await ask(a)
    await ask(b)
    first, second = app.runtime_for(a).server, app.runtime_for(b).server
    assert first is not second and first.connection_id != second.connection_id
    assert first.discovery_requests == second.discovery_requests == 1
    assert not any(e.get("scope") == "cross-turn" for e in b["events"])


async def test_tool_change_invalidates_cache_and_rediscovers(monkeypatch, tmp_path):
    from types import SimpleNamespace
    menu_agent(monkeypatch)
    state = chat_state(tmp_path)
    await ask(state)
    server = app.runtime_for(state).server
    before = len(state["events"])
    await server._notification(SimpleNamespace(method="notifications/tools/list_changed"))
    await ask(state)
    assert server.discovery_requests == 2 and server.tool_calls == 2
    events = state["events"][before:]
    assert any(e.get("operation") == "tools/list" for e in events)
    # Runner may reuse the just-refreshed definitions within this turn only.
    assert not any(e.get("scope") == "cross-turn" for e in events)


async def test_reconnect_never_claims_cross_turn_cache(monkeypatch, tmp_path):
    menu_agent(monkeypatch)
    state = chat_state(tmp_path)
    await ask(state)
    old = app.runtime_for(state)
    await old.close()
    before = len(state["events"])
    await ask(state)
    new = app.runtime_for(state)
    assert new is not old and new.server.discovery_requests == 1
    assert not any(e.get("scope") == "cross-turn" for e in state["events"][before:])


async def test_metadata_cache_does_not_cache_action_results(monkeypatch, tmp_path):
    arguments = {"product_id": "plain", "quantity": 2, "pickup_at": next_pickup_at(),
        "request_id": "cedar-cache-retry", "customer": {"name": "Alex", "email": "alex@example.com", "consent": True}}
    monkeypatch.setattr(app, "build_agent", lambda server: Agent(name="Nour", instructions=INSTRUCTIONS,
        model=FixedCallModel("save_pickup_inquiry", arguments, "12.00"), mcp_servers=[server]))
    state = chat_state(tmp_path)
    await ask(state, "Save the inquiry")
    before = len(state["events"])
    result = await ask(state, "Retry the same inquiry")
    server = app.runtime_for(state).server
    assert server.discovery_requests == 1 and server.tool_calls == 2
    first = next(e["result"] for e in state["events"] if e.get("operation") == "tools/call result")
    second = next(e["result"] for e in state["events"][before:] if e.get("operation") == "tools/call result")
    assert not first["already_saved"] and second["already_saved"]
    assert first["inquiry_id"] == second["inquiry_id"] and len(result[-1]) == 1


async def test_idle_session_closes_cleanly(monkeypatch, tmp_path):
    import asyncio
    from session_runtime import SessionRuntime
    from client import connect_server
    state = chat_state(tmp_path)
    runtime = SessionRuntime(state["database"], idle_timeout=0.05)
    def factory(server):
        return Agent(name="Nour", instructions=INSTRUCTIONS,
            model=FixedCallModel("search_menu", {"query": "sesame"}, "zaatar-sesame"), mcp_servers=[server])
    result = [event async for event in runtime.run("Sesame breads", [], factory)]
    assert any(kind == "answer" for kind, _ in result)
    await asyncio.wait_for(runtime.owner, 3)
    assert runtime.closing and runtime.server is None and runtime.close_error is None


async def test_menu_updates_are_visible_on_cached_connection(monkeypatch, tmp_path):
    import client
    root = tmp_path / "server"
    root.mkdir()
    for name in ("server.py", "bakery.py", "models.py"):
        (root / name).write_bytes((client.ROOT / name).read_bytes())
    (root / "data").mkdir()
    catalog = root / "data/bakery.json"
    catalog.write_bytes((client.ROOT / "data/bakery.json").read_bytes())
    monkeypatch.setattr(client, "ROOT", root)
    menu_agent(monkeypatch)
    state = chat_state(tmp_path)
    await ask(state)
    before = len(state["events"])
    data = json.loads(catalog.read_text())
    next(p for p in data["products"] if p["id"] == "zaatar-sesame")["price_usd"] = 11
    catalog.write_text(json.dumps(data), encoding="utf-8")
    await ask(state)
    server = app.runtime_for(state).server
    assert server.discovery_requests == 1 and server.tool_calls == 2
    payload = next(e["result"] for e in state["events"][before:] if e.get("operation") == "tools/call result")
    assert next(p for p in payload["products"] if p["id"] == "zaatar-sesame")["price_usd"] == "11.00"


def test_activity_timeline_keeps_current_request_and_cache_details():
    events = [{"request": "old", "direction": "Customer → Host"},
        {"request": "new", "direction": "MCP Client → Host", "operation": "cache/reuse", "cache": "Cached tool definitions reused"},
        {"request": "new", "direction": "Host → Model", "operation": "model/request", "definitions": [{"name": "search_menu"}]}]
    rows = app.activity_rows(events)
    assert len(rows) == 2 and rows[0][-1] == "Cached tool definitions reused"
    assert rows[1][-1] == "search_menu"
    assert app.activity_rows([]) == []
