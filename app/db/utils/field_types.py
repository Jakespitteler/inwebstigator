from typing import Annotated

from pydantic import Field

URLString = Annotated[str, Field(max_length=2048, pattern=r"^https?://")]

EmailString = Annotated[
    str, Field(max_length=254, pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", examples=[""])
]
