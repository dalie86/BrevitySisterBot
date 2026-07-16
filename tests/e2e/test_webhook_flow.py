import io
import json

from app.web.app import create_application


class RecordingService:
    def __init__(self) -> None:
        self.payloads = []

    def accept(self, payload, now):
        self.payloads.append(payload)
        return object()


def call_wsgi(application, method, path, body=b"", secret=None):
    status = {}
    headers = {}

    def start_response(value, response_headers):
        status["value"] = value
        headers.update(response_headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    if secret is not None:
        environ["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = secret
    response_body = b"".join(application(environ, start_response))
    return status["value"], headers, response_body


def test_webhook_validates_secret_and_accepts_update() -> None:
    service = RecordingService()
    application = create_application(
        webhook_secret="secret",
        update_service=service,
        health_check=lambda: True,
    )
    payload = json.dumps({"update_id": 101}).encode("utf-8")

    forbidden = call_wsgi(application, "POST", "/telegram/webhook", payload, "wrong")
    accepted = call_wsgi(application, "POST", "/telegram/webhook", payload, "secret")

    assert forbidden[0].startswith("403")
    assert accepted[0].startswith("200")
    assert json.loads(accepted[2]) == {"ok": True}
    assert service.payloads == [{"update_id": 101}]


def test_webhook_rejects_invalid_json_without_calling_service() -> None:
    service = RecordingService()
    application = create_application(
        webhook_secret="secret",
        update_service=service,
        health_check=lambda: True,
    )

    response = call_wsgi(application, "POST", "/telegram/webhook", b"{", "secret")

    assert response[0].startswith("400")
    assert service.payloads == []


def test_health_reports_database_status() -> None:
    application = create_application(
        webhook_secret="secret",
        update_service=RecordingService(),
        health_check=lambda: True,
    )

    response = call_wsgi(application, "GET", "/health")

    assert response[0].startswith("200")
    assert json.loads(response[2]) == {"ok": True}
