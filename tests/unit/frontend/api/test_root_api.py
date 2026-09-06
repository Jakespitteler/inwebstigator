from fastapi.testclient import TestClient
from httpx2 import Response

from app.main import app

client = TestClient(app)


def test_read_root() -> None:
    """
    Tests the root endpoint to ensure the server is running.
    """
    response: Response = client.get(url="/")
    assert response.status_code == 200
    assert "Server is Running." in response.text
