from fastapi.testclient import TestClient

from app.core.exceptions import NotFoundError
from app.main import create_app


def _app_with_test_routes():
    app = create_app()

    @app.get("/test/not-found")
    def raise_not_found():
        raise NotFoundError("Job not found.", details={"job_id": "123"})

    @app.get("/test/crash")
    def raise_unexpected():
        raise RuntimeError("secret internal detail")

    @app.get("/test/validate")
    def validate(limit: int):
        return {"limit": limit}

    return app


def test_app_error_uses_standard_shape():
    client = TestClient(_app_with_test_routes())
    response = client.get("/test/not-found")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Job not found.", "details": {"job_id": "123"}}
    }


def test_unexpected_error_hides_internal_details():
    client = TestClient(_app_with_test_routes(), raise_server_exceptions=False)
    response = client.get("/test/crash")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in response.text


def test_validation_error_shape():
    client = TestClient(_app_with_test_routes())
    response = client.get("/test/validate", params={"limit": "abc"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_route_returns_standard_404():
    client = TestClient(_app_with_test_routes())
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
