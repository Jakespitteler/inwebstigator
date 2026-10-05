from typing import Annotated

from pydantic import AfterValidator, Field

from app.backend.utils.links import add_missing_scheme

URL_CONSTRAINTS = Field(max_length=2048, pattern=r"^https?://")
NewURLString = Annotated[str, AfterValidator(add_missing_scheme), URL_CONSTRAINTS]
URLString = Annotated[str, URL_CONSTRAINTS]

EmailString = Annotated[
    str, Field(max_length=254, pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", examples=[""])
]
