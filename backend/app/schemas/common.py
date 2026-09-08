from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# Shared minimum for every "set/change a password" field across admin and user
# schemas -- was previously only enforced ad hoc on password-reset, so
# registration and password-change accepted a 1-character password. 8 chars
# matches the bar that was already set (and battle-tested) on reset.
NewPassword = Annotated[str, Field(min_length=8, max_length=128)]
