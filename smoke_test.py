"""Real MCP client/server checks for all three tools; no model API call."""
import asyncio
import json
import tempfile
from pathlib import Path

from bakery import inspect_inquiries, next_pickup_at
from client import connect_server


def payload(result) -> dict:
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    return structured or json.loads(next(c.text for c in result.content if c.type == "text"))


def is_error(result) -> bool:
    return bool(getattr(result, "is_error", False) or getattr(result, "isError", False))


async def verify(database: Path) -> None:
    customer = {"name": "Alex", "email": "alex@example.com", "consent": True}
    pickup = next_pickup_at()
    action = {"product_id": "plain", "quantity": 2, "customer": customer, "pickup_at": pickup, "request_id": "cedar-action-01"}
    workflow = {"query": "Olive & Rosemary", "quantity": 3, "customer": customer, "pickup_at": pickup, "request_id": "cedar-workflow-01"}
    async with connect_server(database) as server:
        tools = await server.list_tools()
        assert {t.name for t in tools} == {"search_menu", "save_pickup_inquiry", "prepare_pickup_inquiry"}
        assert all(t.description and (getattr(t, "output_schema", None) or getattr(t, "outputSchema", None)) for t in tools)
        print("PASS: exactly three tools with descriptions and output schemas.")
        menu = await server.call_tool("search_menu", {"query": "sesame"})
        assert not is_error(menu) and len(payload(menu)["products"]) == 2
        assert inspect_inquiries(database) == []
        print("PASS Data: ingredient lookup; no inquiry saved.")
        saved = await server.call_tool("save_pickup_inquiry", action)
        assert not is_error(saved) and payload(saved)["estimated_total_usd"] == "12.00"
        reference = payload(saved)["inquiry_id"]
        assert len(inspect_inquiries(database)) == 1
        print("PASS Action: one saved inquiry with USD 12.00 estimate.")
        prepared = await server.call_tool("prepare_pickup_inquiry", workflow)
        assert not is_error(prepared)
        assert payload(prepared)["steps"] == ["search_menu", "save_pickup_inquiry"]
        assert payload(prepared)["inquiry"]["estimated_total_usd"] == "24.00"
        assert len(inspect_inquiries(database)) == 2
        print("PASS Orchestration: lookup -> save; USD 24.00 estimate.")
        for tool, arguments, code in [
            ("search_menu", {"query": "   "}, "INVALID_QUERY"),
            ("save_pickup_inquiry", action | {"product_id": "unknown-product", "request_id": "cedar-error-01"}, "PRODUCT_NOT_FOUND"),
            ("save_pickup_inquiry", action | {"customer": customer | {"consent": False}, "request_id": "cedar-error-02"}, "CONSENT_REQUIRED"),
            ("save_pickup_inquiry", action | {"quantity": 3}, "REQUEST_CONFLICT"),
            ("prepare_pickup_inquiry", workflow | {"query": "chocolate croissant", "request_id": "cedar-error-03"}, "NO_MATCH"),
            ("prepare_pickup_inquiry", workflow | {"query": "sourdough", "request_id": "cedar-error-04"}, "AMBIGUOUS_MATCH"),
        ]:
            result = await server.call_tool(tool, arguments)
            assert is_error(result) and code in str(result), result
            assert len(inspect_inquiries(database)) == 2
            print(f"PASS {tool}: {code}; no extra inquiry.")
        malformed = await server.call_tool("save_pickup_inquiry", action | {"quantity": True})
        assert is_error(malformed)
        repeated = await server.call_tool("save_pickup_inquiry", action)
        assert payload(repeated)["inquiry_id"] == reference and payload(repeated)["already_saved"]
        assert len(inspect_inquiries(database)) == 2
        print("PASS: typed validation and idempotent retry.")
    async with connect_server(database) as server:
        repeated = await server.call_tool("save_pickup_inquiry", action)
        assert payload(repeated)["inquiry_id"] == reference
        assert len(inspect_inquiries(database)) == 2
    print("PASS: persistence after server restart. All MCP checks passed.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="cedar-crumb-mcp-") as folder:
        asyncio.run(verify(Path(folder) / "pickup.sqlite3"))
