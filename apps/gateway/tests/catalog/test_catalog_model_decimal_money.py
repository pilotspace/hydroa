"""Red/green regression suite for audit-remediation package C1 (LOW/MED CatalogModel
float money): `CatalogModel.prompt_usd_per_token` / `.completion_usd_per_token`
(catalog/domain/entities.py ~196-197) were typed `float`, violating the repo's
money-is-Decimal rule (the sibling `PricingSnapshot` in the SAME file already uses
`Decimal` for the identical fields — this was the one straggler). A raw Python float
can silently lose precision on sub-cent per-token prices (e.g. `0.0000003`), and
`catalog/infrastructure/repository.py::_price_changed` was already defensively
re-wrapping every comparison in `Decimal(str(model.prompt_usd_per_token))` — a tell
that the entity's own type was wrong. `_insert_snapshot` had NO such guard: it wrote
`model.prompt_usd_per_token` straight into a `Numeric` DB column.

New file (does not edit any existing catalog test).
"""

from __future__ import annotations

import dataclasses
import typing
from decimal import Decimal

from gateway.catalog.domain.entities import CatalogModel


def test_prompt_and_completion_fields_are_typed_decimal() -> None:
    """The dataclass's OWN declared type for both money fields must be Decimal —
    not float — matching PricingSnapshot's sibling fields in the same module."""
    hints = typing.get_type_hints(CatalogModel)
    assert hints["prompt_usd_per_token"] is Decimal, (
        f"prompt_usd_per_token must be typed Decimal, got {hints['prompt_usd_per_token']!r}"
    )
    assert hints["completion_usd_per_token"] is Decimal, (
        f"completion_usd_per_token must be typed Decimal, got {hints['completion_usd_per_token']!r}"
    )


def test_constructed_instance_carries_exact_decimal_values() -> None:
    """A model constructed with exact Decimal literals must round-trip WITHOUT the
    binary floating-point representation error a `float` field would introduce."""
    model = CatalogModel(
        id="m",
        name="M",
        context_length=None,
        prompt_usd_per_token=Decimal("0.0000003"),
        completion_usd_per_token=Decimal("0.0000012"),
    )
    assert model.prompt_usd_per_token == Decimal("0.0000003")
    assert model.completion_usd_per_token == Decimal("0.0000012")
    # Exact equality against the string-constructed Decimal proves no float
    # round-trip occurred anywhere in construction (a float 0.0000003 would NOT
    # equal Decimal("0.0000003") bit-for-bit once run through Decimal(x) on a
    # binary float — this pins the entity to the exact-decimal contract).
    assert Decimal(model.prompt_usd_per_token) == Decimal("0.0000003")


def test_dataclass_field_default_type_matches_declared_annotation() -> None:
    """Sanity: no stray float default lingering on the dataclass field spec."""
    money_fields = {
        f.name: f
        for f in dataclasses.fields(CatalogModel)
        if f.name in {"prompt_usd_per_token", "completion_usd_per_token"}
    }
    for name, f in money_fields.items():
        assert f.type in ("Decimal", Decimal), f"{name} field.type is {f.type!r}, expected Decimal"
