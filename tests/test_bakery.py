from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from bakery import Bakery, BakeryError, inspect_inquiries, next_pickup_at
from models import Customer

NOW = datetime.fromisoformat("2026-11-02T08:00:00+02:00")
PICKUP = "2026-11-04T10:00:00+02:00"


@pytest.fixture
def customer():
    return Customer(name="Alex", email="alex@example.com", consent=True)


@pytest.fixture
def bakery(tmp_path):
    service = Bakery(tmp_path / "pickup.sqlite3", now=lambda: NOW)
    service.initialize()
    return service


def save(bakery, customer, **overrides):
    values = {"product_id": "plain", "quantity": 2, "customer": customer, "pickup_at": PICKUP, "request_id": "cedar-test-01"}
    return bakery.save_pickup_inquiry(**(values | overrides))


def test_menu_preserves_cedar_crumb_catalog(bakery):
    products = bakery.search_menu("").products
    assert [(p.id, p.price_usd, p.weight_g) for p in products] == [
        ("plain", "6.00", 800), ("whole-wheat", "6.00", 750), ("rye", "7.00", 700),
        ("olive-rosemary", "8.00", 800), ("zaatar-sesame", "7.00", 800), ("walnut-cranberry", "9.00", 800)]


def test_search_is_read_only_and_case_insensitive(bakery):
    before = bakery.database.read_bytes()
    result = bakery.search_menu("SESAME")
    assert [p.id for p in result.products] == ["rye", "zaatar-sesame"]
    assert "gluten" in result.allergen_notice
    assert bakery.database.read_bytes() == before
    assert inspect_inquiries(bakery.database) == []


def test_search_needs_no_database(tmp_path):
    bakery = Bakery(tmp_path / "does-not-exist.sqlite3")
    assert len(bakery.search_menu("").products) == 6
    assert not bakery.database.exists()


@pytest.mark.parametrize("query", ["   ", "x" * 121, None])
def test_data_failure(bakery, query):
    with pytest.raises(BakeryError, match="INVALID_QUERY"):
        bakery.search_menu(query)


def test_unmatched_literal_query_is_empty(bakery):
    assert bakery.search_menu("% ' OR 1=1 --").products == []


def test_action_persists_exact_estimate_and_pending_status(bakery, customer):
    inquiry = save(bakery, customer)
    assert inquiry.product_name == "Country Sourdough"
    assert inquiry.estimated_total_usd == "12.00"
    assert inquiry.status == "pending_confirmation"
    assert "customer_email" not in inquiry.model_dump()
    assert inspect_inquiries(bakery.database)[0]["inquiry_id"] == inquiry.inquiry_id


def test_retry_is_idempotent_after_restart(bakery, customer):
    first = save(bakery, customer)
    restarted = Bakery(bakery.database, now=lambda: NOW)
    second = save(restarted, customer)
    assert first.inquiry_id == second.inquiry_id
    assert second.already_saved and not first.already_saved
    assert len(inspect_inquiries(bakery.database)) == 1


def test_retry_after_pickup_retains_original_reference(bakery, customer):
    first = save(bakery, customer)
    bakery.now = lambda: NOW + timedelta(days=10)
    assert save(bakery, customer).inquiry_id == first.inquiry_id


def test_equivalent_time_offsets_are_the_same_request(bakery, customer):
    first = save(bakery, customer)
    repeated = save(bakery, customer, pickup_at="2026-11-04T08:00:00+00:00")
    assert repeated.inquiry_id == first.inquiry_id and repeated.already_saved


@pytest.mark.parametrize("change", [{"quantity": 3}, {"product_id": "rye"}, {"pickup_at": "2026-11-05T10:00:00+02:00"}])
def test_request_reference_conflict_leaves_original_unchanged(bakery, customer, change):
    save(bakery, customer)
    before = inspect_inquiries(bakery.database)
    with pytest.raises(BakeryError, match="REQUEST_CONFLICT"):
        save(bakery, customer, **change)
    assert inspect_inquiries(bakery.database) == before


@pytest.mark.parametrize("quantity", [0, -1, 31, 1.5, True, "2"])
def test_invalid_quantities_create_no_inquiry(bakery, customer, quantity):
    with pytest.raises(BakeryError, match="INVALID_QUANTITY"):
        save(bakery, customer, quantity=quantity)
    assert inspect_inquiries(bakery.database) == []


@pytest.mark.parametrize("pickup, code", [
    ("not-a-date", "INVALID_PICKUP"),
    ("2026-11-04T10:00:00", "INVALID_PICKUP"),
    ("2026-11-02T15:00:00+02:00", "PICKUP_TOO_SOON"),
    ("2026-11-08T10:00:00+02:00", "PICKUP_CLOSED"),
    ("2026-11-04T06:59:00+02:00", "PICKUP_CLOSED"),
    ("2026-11-04T19:00:00+02:00", "PICKUP_CLOSED"),
])
def test_pickup_failures_create_no_inquiry(bakery, customer, pickup, code):
    with pytest.raises(BakeryError, match=code):
        save(bakery, customer, pickup_at=pickup)
    assert inspect_inquiries(bakery.database) == []


def test_exactly_24_hours_notice_is_accepted(bakery, customer):
    assert save(bakery, customer, pickup_at="2026-11-03T08:00:00+02:00").status == "pending_confirmation"


def test_consent_required_before_saving(bakery, customer):
    with pytest.raises(BakeryError, match="CONSENT_REQUIRED"):
        save(bakery, customer.model_copy(update={"consent": False}))
    assert inspect_inquiries(bakery.database) == []


@pytest.mark.parametrize("details", [
    {"name": "   ", "email": "alex@example.com", "consent": True},
    {"name": "Alex", "email": "not-an-email", "consent": True},
    {"name": "Alex", "email": "alex@example.com", "consent": "yes"},
])
def test_customer_contract_rejects_invalid_details(details):
    with pytest.raises(ValidationError):
        Customer(**details)


def test_unknown_product_and_bad_reference(bakery, customer):
    with pytest.raises(BakeryError, match="PRODUCT_NOT_FOUND"):
        save(bakery, customer, product_id="croissant")
    with pytest.raises(BakeryError, match="INVALID_REQUEST_ID"):
        save(bakery, customer, request_id="bad")
    assert inspect_inquiries(bakery.database) == []


def test_orchestration_reuses_data_then_action(bakery, customer, monkeypatch):
    calls = []
    search, action = bakery.search_menu, bakery.save_pickup_inquiry
    def tracked_search(query):
        calls.append("search_menu")
        return search(query)
    def tracked_action(*args):
        calls.append("save_pickup_inquiry")
        return action(*args)
    monkeypatch.setattr(bakery, "search_menu", tracked_search)
    monkeypatch.setattr(bakery, "save_pickup_inquiry", tracked_action)
    result = bakery.prepare_pickup_inquiry("Olive & Rosemary", 3, customer, PICKUP, "cedar-flow-01")
    assert calls == result.steps == ["search_menu", "save_pickup_inquiry"]
    assert result.inquiry.product_id == "olive-rosemary"
    assert result.inquiry.estimated_total_usd == "24.00"
    assert len(inspect_inquiries(bakery.database)) == 1


@pytest.mark.parametrize("query, code", [("chocolate croissant", "NO_MATCH"), ("sourdough", "AMBIGUOUS_MATCH")])
def test_orchestration_failure_has_no_side_effect(bakery, customer, query, code):
    with pytest.raises(BakeryError, match=code):
        bakery.prepare_pickup_inquiry(query, 1, customer, PICKUP, "cedar-flow-01")
    assert inspect_inquiries(bakery.database) == []


def test_orchestration_retry_returns_original_inquiry(bakery, customer):
    first = bakery.prepare_pickup_inquiry("rosemary", 3, customer, PICKUP, "cedar-flow-01")
    retry = bakery.prepare_pickup_inquiry("rosemary", 3, customer, PICKUP, "cedar-flow-01")
    assert retry.inquiry.inquiry_id == first.inquiry.inquiry_id
    assert retry.inquiry.already_saved
    assert len(inspect_inquiries(bakery.database)) == 1


def test_concurrent_retries_create_one_inquiry(bakery, customer):
    def request(_):
        service = Bakery(bakery.database, now=lambda: NOW)
        return save(service, customer).inquiry_id
    with ThreadPoolExecutor(max_workers=8) as pool:
        references = list(pool.map(request, range(8)))
    assert len(set(references)) == 1
    assert len(inspect_inquiries(bakery.database)) == 1


def test_storage_failure_propagates_through_workflow(tmp_path, customer):
    service = Bakery(tmp_path / "missing" / "pickup.sqlite3", now=lambda: NOW)
    with pytest.raises(BakeryError, match="STORAGE_UNAVAILABLE"):
        service.prepare_pickup_inquiry("rosemary", 1, customer, PICKUP, "cedar-flow-01")


def test_catalog_failure_is_clear(tmp_path):
    with pytest.raises(BakeryError, match="CATALOG_UNAVAILABLE"):
        Bakery(tmp_path / "pickup.sqlite3", catalog=tmp_path / "missing.json")


def test_walkthrough_pickup_is_open_and_far_enough_ahead():
    for day in range(14):
        now = NOW + timedelta(days=day)
        pickup = datetime.fromisoformat(next_pickup_at(now))
        assert pickup.weekday() != 6 and pickup.hour == 10
        assert pickup.astimezone(UTC) - now.astimezone(UTC) >= timedelta(hours=24)
