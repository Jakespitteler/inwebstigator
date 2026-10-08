import uuid

from app.core.errors import NotFoundError, WebConnectionError, WebsiteUnavailableError


def test_not_found_error_names_the_missing_record_by_its_id() -> None:
    """Tests a record looked up by its ID is named by that ID when it is not found."""
    record_id: uuid.UUID = uuid.uuid4()

    error = NotFoundError(id=record_id)

    assert error.id == record_id
    assert str(record_id) in str(error)


def test_not_found_error_names_the_missing_record_by_what_it_was_looked_up_by() -> None:
    """Tests a record looked up by other details (e.g. its URL) is named by those details when it is not found."""
    error = NotFoundError(attributes={"url": "https://example.com/"})

    assert error.attributes == {"url": "https://example.com/"}
    assert "https://example.com/" in str(error)


def test_not_found_error_without_details_still_says_not_found() -> None:
    """Tests a record not found without an ID or other details still gives a readable message."""
    assert str(NotFoundError()) == "not found."


def test_a_website_whose_home_page_cannot_load_counts_as_unreachable() -> None:
    """Tests a website whose home page cannot be loaded is treated like a connection failure (unreachable), and its
    message names the home page."""
    error = WebsiteUnavailableError("https://example.com/")

    assert isinstance(error, WebConnectionError)
    assert error.url == "https://example.com/"
    assert str(error) == "The home page https://example.com/ could not be loaded, so the website could not be scanned."
