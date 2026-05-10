"""Unit tests for the Flask demo application."""
from unittest.mock import patch

# Patch Flask.run before importing to prevent server start during import
with patch("flask.Flask.run"):
    from app import app


import pytest


@pytest.fixture
def client():
    """Create a Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestHomeRoute:
    """Tests for the '/' route."""

    def test_home_returns_200(self, client):
        """Home route should return status 200."""
        response = client.get("/")
        assert response.status_code == 200

    def test_home_returns_expected_body(self, client):
        """Home route should return the expected greeting message."""
        response = client.get("/")
        assert b"Hello Auckland k8s Demo" in response.data

    def test_home_returns_string_content_type(self, client):
        """Home route should return text/html content type."""
        response = client.get("/")
        assert "text/html" in response.content_type


class TestHealthRoute:
    """Tests for the '/health' route."""

    def test_health_returns_200(self, client):
        """Health route should return status 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok(self, client):
        """Health route should return 'ok'."""
        response = client.get("/health")
        assert response.data == b"ok"


class TestErrorRoute:
    """Tests for the '/error' route."""

    @patch("app.random.random", return_value=0.5)
    def test_error_returns_ok_when_random_high(self, mock_random, client):
        """Error route should return 200 when random >= 0.3."""
        response = client.get("/error")
        assert response.status_code == 200
        assert response.data == b"ok"

    @patch("app.random.random", return_value=0.1)
    def test_error_returns_500_when_random_low(self, mock_random, client):
        """Error route should return 500 when random < 0.3."""
        response = client.get("/error")
        assert response.status_code == 500
        assert response.data == b"error"

    @patch("app.random.random", return_value=0.29)
    def test_error_boundary_just_below_threshold(self, mock_random, client):
        """Error route should return 500 when random is just below 0.3."""
        response = client.get("/error")
        assert response.status_code == 500

    @patch("app.random.random", return_value=0.3)
    def test_error_boundary_at_threshold(self, mock_random, client):
        """Error route should return 200 when random equals 0.3."""
        response = client.get("/error")
        assert response.status_code == 200


class TestAppConfiguration:
    """Tests for Flask app configuration."""

    def test_app_is_flask_instance(self):
        """App should be a Flask application instance."""
        assert app.name == "app"

    def test_app_has_home_route(self):
        """App should have a '/' route registered."""
        rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert "/" in rules

    def test_app_has_health_route(self):
        """App should have a '/health' route registered."""
        rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert "/health" in rules

    def test_app_has_error_route(self):
        """App should have a '/error' route registered."""
        rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert "/error" in rules
