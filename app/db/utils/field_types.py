from typing import Annotated

from pydantic import Field

URLString = Annotated[str, Field(max_length=2048, pattern=r"^https?://")]
