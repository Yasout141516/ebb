from tests.api_support import client, make_services


def test_health_ok():
    response = client(make_services()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"database": "ok", "tutor": "ok"}


def test_health_reports_missing_groq_key():
    text = "GROQ_API_KEY is not set (get one free at console.groq.com)"
    response = client(make_services(tutor=None, config_error=text)).get("/health")
    assert response.json() == {"database": "ok", "tutor": text}


def test_cors_allows_the_web_origin_only():
    web = client(make_services())
    allowed = web.get("/health", headers={"Origin": "http://localhost:3000"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = web.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
