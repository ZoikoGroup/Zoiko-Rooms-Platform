"""Host-supplied facts for the Residential Occupancy Agreement that no other
part of the platform knows (template Schedule A exclusive/shared areas,
service address, rent due rule, renewal; Schedule B utilities and
property-specific rules). Stored per listing in Listing.agreement_details
and frozen into each agreement version's snapshot at generation time --
see services/agreement_document/facts.py.

Every field is optional: anything left blank renders as "Not specified" in
the agreement rather than an invented value."""

from pydantic import Field, field_validator

from app.schemas.common import CamelModel

# Schedule B "Utilities and recurring charges" rows, in template order.
UTILITY_KEYS: tuple[str, ...] = (
    "electricity",
    "gas_heating",
    "water_sewer",
    "internet",
    "local_taxes",
    "building_fees",
)
# Who carries the cost. INCLUDED = covered by the rent.
UTILITY_PAYERS: tuple[str, ...] = ("HOST", "RENTER", "SHARED", "INCLUDED", "NOT_APPLICABLE")


class UtilityAllocation(CamelModel):
    payer: str | None = None
    notes: str = Field(default="", max_length=200)

    @field_validator("payer")
    @classmethod
    def _known_payer(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if value not in UTILITY_PAYERS:
            raise ValueError(f"payer must be one of {', '.join(UTILITY_PAYERS)}")
        return value


class HouseRules(CamelModel):
    """Schedule B "Property-specific rules". Free text, but always subject to
    mandatory law -- the agreement prints that legal control next to each."""

    guests: str = Field(default="", max_length=500)
    pets: str = Field(default="", max_length=500)
    smoking: str = Field(default="", max_length=500)
    noise: str = Field(default="", max_length=500)
    parking_storage: str = Field(default="", max_length=500)
    shared_areas: str = Field(default="", max_length=500)


class ListingAgreementDetails(CamelModel):
    exclusive_use_areas: str = Field(default="", max_length=300)
    shared_use_areas: str = Field(default="", max_length=300)
    host_service_address: str = Field(default="", max_length=500)
    rent_due_rule: str = Field(default="", max_length=200)
    renewal_rule: str = Field(default="", max_length=300)
    utilities: dict[str, UtilityAllocation] = Field(default_factory=dict)
    house_rules: HouseRules = Field(default_factory=HouseRules)

    @field_validator("utilities")
    @classmethod
    def _known_utilities(cls, value: dict[str, UtilityAllocation]) -> dict[str, UtilityAllocation]:
        unknown = set(value) - set(UTILITY_KEYS)
        if unknown:
            raise ValueError(f"unknown utility key(s): {', '.join(sorted(unknown))}")
        return value
