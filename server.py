"""Cedar Crumb FastMCP server: one Data, Action, and Orchestration tool."""
import logging
import os
from pathlib import Path
from typing import Callable, TypeVar

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from bakery import Bakery, BakeryError
from models import Customer, MenuResult, PickupAt, PickupInquiry, PreparedInquiry, ProductID, Quantity, Query, RequestID

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("cedar_crumb")
T = TypeVar("T")


def create_server(database: Path | str | None = None) -> FastMCP:
    bakery = Bakery(database or os.getenv("BAKERY_DB", str(ROOT / "data" / "pickup.sqlite3")))
    bakery.initialize()
    mcp = FastMCP("Cedar Crumb")

    def invoke(name: str, operation: Callable[[], T]) -> T:
        log.info("SERVER received %s", name)
        try:
            result = operation()
        except BakeryError as exc:
            log.warning("SERVER failed %s: %s", name, exc.code)
            raise ToolError(str(exc)) from exc
        log.info("SERVER completed %s", name)
        return result

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False})
    def search_menu(query: Query = "") -> MenuResult:
        """DATA: Retrieve Cedar Crumb menu names, IDs, USD prices, loaf weights, ingredients, allergens, and pickup policies. Search is a case-insensitive literal substring; an empty query lists all products. No matches returns an empty product list. Whitespace-only or oversized input fails clearly. This lookup does not save an inquiry or check stock."""
        return invoke("search_menu", lambda: bakery.search_menu(query))

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
    def save_pickup_inquiry(product_id: ProductID, quantity: Quantity, customer: Customer, pickup_at: PickupAt, request_id: RequestID) -> PickupInquiry:
        """ACTION: Save one pickup inquiry for a known product ID. Use only after the customer explicitly requests saving and agrees to contact follow-up. Persist the supplied contact details in SQLite; return a reference, menu-price estimate, and pending_confirmation status. Require 24 hours' notice and Monday-Saturday 07:00 to before 19:00 Beirut time. Reusing a reference with identical details returns the existing inquiry; changed details cause REQUEST_CONFLICT. Unknown product, invalid quantity, missing consent, invalid/closed/too-soon pickup, and unavailable storage fail without creating a request. Staff confirmation determines fulfillment."""
        return invoke("save_pickup_inquiry", lambda: bakery.save_pickup_inquiry(product_id, quantity, customer, pickup_at, request_id))

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
    def prepare_pickup_inquiry(query: Query, quantity: Quantity, customer: Customer, pickup_at: PickupAt, request_id: RequestID) -> PreparedInquiry:
        """ORCHESTRATION: Prepare a requested pickup inquiry from a product name, flavor, or ingredient query. Call the Data lookup, require exactly one matching product, then call the same Action operation to save it. Return the executed steps and inquiry. NO_MATCH or AMBIGUOUS_MATCH creates no inquiry; clarify the product instead. Consent, pickup validation, storage errors, and retry rules are the same as the Action tool. Requires an explicit request to save."""
        return invoke("prepare_pickup_inquiry", lambda: bakery.prepare_pickup_inquiry(query, quantity, customer, pickup_at, request_id))

    return mcp


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    create_server().run(transport="stdio", show_banner=False)
