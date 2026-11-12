from unittest.mock import patch

import pytest

from src.app import create_app


@pytest.mark.parametrize("mode,status", [("healthy", 200), ("http500", 500), ("latency", 200)])
def test_fault_modes_are_deterministic_and_probes_stay_healthy(mode, status):
    app = create_app({"TESTING": True, "RELEASE": "candidate", "FAULT_MODE": mode})
    with app.test_client() as client, patch("src.app.time.sleep") as sleep:
        response = client.get("/")
        assert response.status_code == status
        assert response.json["release"] == "candidate"
        assert response.headers["X-App-Release"] == "candidate"
        if mode == "latency":
            sleep.assert_called_once_with(0.8)
        else:
            sleep.assert_not_called()
        for path in ("/health", "/healthz", "/readyz"):
            assert client.get(path).status_code == 200


def test_stable_configuration_is_isolated_from_faulty_candidate():
    stable = create_app({"FAULT_MODE": "healthy", "RELEASE": "stable"})
    candidate = create_app({"FAULT_MODE": "http500", "RELEASE": "candidate"})
    assert candidate.test_client().get("/").status_code == 500
    response = stable.test_client().get("/")
    assert response.status_code == 200
    assert response.json["release"] == "stable"


@pytest.mark.parametrize("config", [{"FAULT_MODE": "random"}, {"FAULT_DELAY_MS": -1}, {"FAULT_DELAY_MS": 10001}])
def test_invalid_fault_settings_fail_at_startup(config):
    with pytest.raises(ValueError):
        create_app(config)
