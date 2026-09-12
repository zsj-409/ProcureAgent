"""Configuration-driven supplier profiles and registry."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class SupplierProfile(BaseModel):
    """Static configuration for one supplier channel."""

    supplier_id: str
    display_name: str
    source_type: Literal["api", "portal"]
    base_url: str
    enabled: bool = True
    priority: int = 100

    # API suppliers append this to ``base_url``.
    search_endpoint: str | None = None

    # Portal suppliers append this to ``base_url`` and use the selectors below.
    search_path: str | None = None
    search_input_selector: str = "#search-input"
    search_button_selector: str = "#search-button"
    result_row_selector: str = "#results tbody tr"
    name_selector: str = ".name"
    price_selector: str = ".price"
    stock_selector: str = ".stock"
    delivery_selector: str = ".delivery"


class SupplierRegistry:
    """Load and filter supplier profiles without hard-coding any supplier."""

    def __init__(
        self,
        profiles: list[SupplierProfile] | None = None,
        config_path: str | Path | None = None,
    ):
        if profiles is not None:
            self._profiles = profiles
        else:
            path = Path(config_path or "config/suppliers.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            self._profiles = [SupplierProfile.model_validate(item) for item in data["suppliers"]]

    def enabled_profiles(self) -> list[SupplierProfile]:
        """Return enabled suppliers ordered by priority, then supplier_id."""

        return sorted(
            (profile for profile in self._profiles if profile.enabled),
            key=lambda profile: (profile.priority, profile.supplier_id),
        )

    def get(self, supplier_id: str) -> SupplierProfile | None:
        """Return an enabled profile by id."""

        for profile in self._profiles:
            if profile.supplier_id == supplier_id and profile.enabled:
                return profile
        return None
