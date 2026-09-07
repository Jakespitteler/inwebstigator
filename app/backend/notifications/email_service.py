from pydantic import SecretStr

from app.db.models.website_models import WebsiteState


def generate_scan_report_body(website_state: WebsiteState) -> str: ...
def send_email(email: str, app_password: SecretStr, recipient_email: str, subject: str, body: str) -> None: ...
