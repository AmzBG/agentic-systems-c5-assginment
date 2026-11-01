"""Input contracts and structured results for Cedar Crumb's MCP tools."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Query = Annotated[str, Field(max_length=120, description="Case-insensitive product, flavor, or ingredient substring. Use an empty string for the full menu.")]
ProductID = Annotated[str, Field(min_length=1, max_length=60, description="Exact product ID returned by search_menu, such as plain or olive-rosemary.")]
Quantity = Annotated[int, Field(strict=True, ge=1, le=30, description="Whole loaves, 1-30. Larger requests require a staff quote.")]
RequestID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,64}$", description="Unique request reference; reuse this reference only when retrying identical details.")]
PickupAt = Annotated[str, Field(description="Pickup date/time in ISO 8601 with a UTC offset, for example 2026-11-04T10:00:00+02:00. At least 24 hours ahead, Monday-Saturday, 07:00 to before 19:00 Beirut time.")]


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80, description="Customer-provided name.")
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", description="Customer-provided email for pickup follow-up.")
    consent: bool = Field(strict=True, description="True only after the customer agrees to save these details for pickup follow-up.")


class Product(BaseModel):
    id: str
    name: str
    weight_g: int
    price_usd: str
    description: str
    ingredients: str
    allergens: list[str]


class MenuResult(BaseModel):
    query: str
    products: list[Product]
    currency: Literal["USD"] = "USD"
    hours: str
    pickup_policy: str
    allergen_notice: str


class PickupInquiry(BaseModel):
    inquiry_id: str
    request_id: str
    product_id: str
    product_name: str
    quantity: int
    unit_price_usd: str
    estimated_total_usd: str
    currency: Literal["USD"] = "USD"
    customer_name: str
    pickup_at: str
    status: Literal["pending_confirmation"] = "pending_confirmation"
    already_saved: bool = False


class PreparedInquiry(BaseModel):
    query: str
    steps: list[str]
    inquiry: PickupInquiry
