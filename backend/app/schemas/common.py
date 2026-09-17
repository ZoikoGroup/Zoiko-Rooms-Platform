from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        # A misspelled/stale field name in a request body used to be silently
        # dropped instead of rejected -- the exact failure mode that let a
        # wrong `moveInDate` (should have been `desiredMoveIn`) through with
        # no error anywhere. "forbid" turns that into an immediate 422.
        extra="forbid",
    )
