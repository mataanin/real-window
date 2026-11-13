from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

FOUND, NOT_FOUND, BLOCKED, LOGIN_REQUIRED, ERROR = "found", "not_found", "blocked", "login_required", "error"

# Canonical field order + labels used by the merger/report.
FIELDS: list[tuple[str, str]] = [
    ("list_price", "List / asking price"),
    ("status", "Status"),
    ("list_date", "List date"),
    ("days_on_market", "Days on market"),
    ("price_per_unit", "Price per unit"),
    ("price_per_sf", "Price per SF"),
    ("last_sold_price", "Last sold price"),
    ("last_sold_date", "Last sold date"),
    ("property_type", "Property type"),
    ("units", "Units"),
    ("unit_mix", "Unit mix"),
    ("beds", "Beds"),
    ("baths", "Baths"),
    ("building_sf", "Building SF"),
    ("lot_size", "Lot size"),
    ("year_built", "Year built"),
    ("stories", "Stories"),
    ("rents", "Rents / rent roll"),
    ("gross_income", "Gross income"),
    ("noi", "NOI"),
    ("cap_rate", "Cap rate"),
    ("occupancy", "Occupancy"),
    ("expenses", "Expenses"),
    ("taxes", "Taxes"),
    ("assessment", "Assessment"),
    ("apn", "APN / parcel"),
    ("zoning", "Zoning"),
    ("parking", "Parking"),
    ("heating_utilities", "Heating / utilities / metering"),
    ("construction", "Construction"),
    ("broker", "Listing broker / agent"),
    ("broker_contact", "Broker contact"),
    ("photo_count", "Photo count"),
    ("auction", "Auction date / terms"),
    ("estimate", "Automated value estimate"),
    ("rent_estimate", "Rent estimate"),
    ("hoa", "HOA"),
]
LIST_FIELDS = {"price_history", "sale_history", "tax_history"}


@dataclass
class SiteResult:
    site: str
    status: str = NOT_FOUND
    url: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    description: str | None = None
    price_history: list[dict] = field(default_factory=list)
    tax_history: list[dict] = field(default_factory=list)
    raw_text_snippet: str | None = None
    screenshot_path: str | None = None
    matched_address: str | None = None
    notes: list[str] = field(default_factory=list)
    login_gated_fields: list[str] = field(default_factory=list)
    elapsed_s: float | None = None

    def set(self, key: str, value: Any) -> None:
        """Set a field only if value is meaningful (never store empty/placeholder)."""
        if value is None:
            return
        if isinstance(value, str):
            v = value.strip()
            if not v or v in {"-", "--", "N/A", "n/a", "null", "None", "0"} or set(v) <= {"*", " "}:
                if v and set(v) <= {"*", " "}:
                    self.login_gated_fields.append(key)
                return
            value = v
        if isinstance(value, (list, dict)) and not value:
            return
        if key not in self.fields:
            self.fields[key] = value

    def to_dict(self) -> dict:
        return asdict(self)
