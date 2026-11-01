"""Menu retrieval and durable pickup inquiry operations."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from models import Customer, MenuResult, PickupInquiry, PreparedInquiry, Product

ROOT = Path(__file__).resolve().parent
BEIRUT = ZoneInfo("Asia/Beirut")


class BakeryError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class Bakery:
    def __init__(self, database: Path | str, *, catalog: Path | str = ROOT / "data" / "bakery.json", now: Callable[[], datetime] | None = None):
        self.database = Path(database).resolve()
        self.catalog = Path(catalog)
        self.now = now or (lambda: datetime.now(UTC))
        self.data, self.products = self._read_catalog()

    def _read_catalog(self):
        try:
            data = json.loads(self.catalog.read_text(encoding="utf-8"))
            products = [Product(**{**p, "price_usd": f"{Decimal(str(p['price_usd'])):.2f}"}) for p in data["products"]]
            return data, products
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BakeryError("CATALOG_UNAVAILABLE", "The bakery menu could not be loaded.") from exc

    def initialize(self) -> None:
        """Prepare storage at server startup, outside the Data operation."""
        try:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection, connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS pickup_inquiries (
                    request_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    customer_email TEXT NOT NULL,
                    result_json TEXT NOT NULL)""")
        except (OSError, sqlite3.Error) as exc:
            raise BakeryError("STORAGE_UNAVAILABLE", "Pickup inquiry storage could not be initialized.") from exc

    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        target = self.database.as_uri() + "?mode=ro" if readonly else str(self.database)
        connection = sqlite3.connect(target, uri=readonly, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def search_menu(self, query: str) -> MenuResult:
        """Retrieve documented menu facts without touching inquiry storage."""
        if not isinstance(query, str) or len(query) > 120 or (query and not query.strip()):
            raise BakeryError("INVALID_QUERY", "Use a product or ingredient query of up to 120 characters, or an empty string for the full menu.")
        query = query.strip()
        data, catalog_products = self._read_catalog()
        products = [p for p in catalog_products if not query or query.casefold() in " ".join(
            [p.id, p.name, p.description, p.ingredients, *p.allergens]
        ).casefold()]
        policies = data["policies"]
        return MenuResult(query=query, products=products, hours=policies["hours"], pickup_policy=policies["ordering"], allergen_notice=policies["allergens"])

    def _product(self, product_id: str) -> Product:
        _, products = self._read_catalog()
        for product in products:
            if product.id == product_id:
                return product
        raise BakeryError("PRODUCT_NOT_FOUND", "Unknown product ID. Look up the menu before saving a request.")

    @staticmethod
    def _pickup(pickup_at: str) -> datetime:
        try:
            result = datetime.fromisoformat(pickup_at)
        except (ValueError, TypeError) as exc:
            raise BakeryError("INVALID_PICKUP", "Use an ISO 8601 pickup date/time with a UTC offset.") from exc
        if result.tzinfo is None or result.utcoffset() is None:
            raise BakeryError("INVALID_PICKUP", "Include the pickup date/time's UTC offset.")
        return result.astimezone(BEIRUT)

    def save_pickup_inquiry(self, product_id: str, quantity: int, customer: Customer, pickup_at: str, request_id: str) -> PickupInquiry:
        """Persist one validated pickup inquiry; identical retries return its ID."""
        product = self._product(product_id)
        if type(quantity) is not int or not 1 <= quantity <= 30:
            raise BakeryError("INVALID_QUANTITY", "Request 1-30 whole loaves; larger quantities require a staff quote.")
        if not isinstance(customer, Customer):
            raise BakeryError("INVALID_CUSTOMER", "Supply a customer name, email, and consent.")
        if not customer.consent:
            raise BakeryError("CONSENT_REQUIRED", "Customer consent is required before saving contact details.")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
            raise BakeryError("INVALID_REQUEST_ID", "Use a request reference of 8-64 letters, digits, underscores or hyphens.")
        pickup = self._pickup(pickup_at)
        canonical = {"product_id": product_id, "quantity": quantity, "customer": customer.model_dump(), "pickup_at": pickup.astimezone(UTC).isoformat()}
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT fingerprint, result_json FROM pickup_inquiries WHERE request_id=?", (request_id,)).fetchone()
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise BakeryError("REQUEST_CONFLICT", "This request reference belongs to different details. Use a new reference for a new inquiry.")
                    return PickupInquiry.model_validate_json(existing["result_json"]).model_copy(update={"already_saved": True})
                if pickup.astimezone(UTC) - self.now().astimezone(UTC) < timedelta(hours=24):
                    raise BakeryError("PICKUP_TOO_SOON", "Pickup inquiries require at least 24 hours' notice.")
                if pickup.weekday() == 6 or not time(7) <= pickup.time() < time(19):
                    raise BakeryError("PICKUP_CLOSED", "Choose Monday-Saturday, 07:00 to before 19:00 Beirut time.")
                inquiry = PickupInquiry(inquiry_id="CC-" + uuid4().hex, request_id=request_id,
                    product_id=product.id, product_name=product.name, quantity=quantity,
                    unit_price_usd=product.price_usd, estimated_total_usd=f"{Decimal(product.price_usd) * quantity:.2f}",
                    customer_name=customer.name, pickup_at=pickup.isoformat())
                connection.execute("INSERT INTO pickup_inquiries VALUES (?, ?, ?, ?)", (request_id, fingerprint, customer.email, inquiry.model_dump_json()))
                return inquiry
        except sqlite3.Error as exc:
            raise BakeryError("STORAGE_UNAVAILABLE", "The pickup inquiry could not be saved. Check database access and retry using the same reference.") from exc

    def prepare_pickup_inquiry(self, query: str, quantity: int, customer: Customer, pickup_at: str, request_id: str) -> PreparedInquiry:
        """Coordinate menu lookup followed by the same Action operation."""
        menu = self.search_menu(query)
        if not menu.products:
            raise BakeryError("NO_MATCH", "No menu product matches this request. No inquiry was saved.")
        if len(menu.products) > 1:
            choices = ", ".join(p.name for p in menu.products)
            raise BakeryError("AMBIGUOUS_MATCH", f"Choose one product before saving: {choices}.")
        inquiry = self.save_pickup_inquiry(menu.products[0].id, quantity, customer, pickup_at, request_id)
        return PreparedInquiry(query=menu.query, steps=["search_menu", "save_pickup_inquiry"], inquiry=inquiry)


def next_pickup_at(now: datetime | None = None) -> str:
    """Return a practical future pickup time for the CLI walkthrough."""
    candidate = ((now or datetime.now(UTC)).astimezone(BEIRUT) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    while candidate.weekday() == 6:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def inspect_inquiries(database: Path | str) -> list[dict]:
    """Local read-only inspection; not exposed as an MCP tool."""
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return [json.loads(row[0]) for row in connection.execute("SELECT result_json FROM pickup_inquiries ORDER BY request_id")]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inspect saved pickup inquiries.")
    parser.add_argument("database", nargs="?", default=str(ROOT / "data" / "pickup.sqlite3"))
    args = parser.parse_args()
    print(json.dumps(inspect_inquiries(args.database), indent=2))
