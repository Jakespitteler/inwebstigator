from typing import Annotated

from pydantic import Field

# The rules every URL must follow, shared so other URL types can be checked against the same rules
URL_CONSTRAINTS = Field(max_length=2048, pattern=r"^https?://")

URLString = Annotated[str, URL_CONSTRAINTS]

EmailString = Annotated[
    str, Field(max_length=254, pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", examples=[""])
]
