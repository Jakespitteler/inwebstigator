import re
from typing import Annotated

from pydantic import AfterValidator, Field

from app.backend.links import add_missing_scheme


def check_is_regular_expression(pattern: str) -> str:
    """Checks a pattern is a valid regular expression, e.g. an ignore rule for a critical page.

    Args:
        pattern: The pattern to check.

    Returns:
        The pattern, unchanged.

    Raises:
        ValueError: If the pattern is not a valid regular expression.
    """
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f"{pattern!r} is not a valid regular expression: {error}") from error
    return pattern


URL_CONSTRAINTS = Field(max_length=2048, pattern=r"^https?://")
NewURLString = Annotated[str, AfterValidator(add_missing_scheme), URL_CONSTRAINTS]
URLString = Annotated[str, URL_CONSTRAINTS]

EmailString = Annotated[
    str, Field(max_length=254, pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", examples=[""])
]

RegexString = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(check_is_regular_expression)]
