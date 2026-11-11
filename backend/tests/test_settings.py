"""Startup checks on the settings: the example file's placeholders never run."""

import pytest
from pydantic import ValidationError

from app.config import Settings

GOOD = {
    "telemetry_api_key": "a-real-random-key-0123456789",
    "database_url": "postgresql://postgres:s3cret-random@db:5432/robotfleet",
    "_env_file": None,
}


def test_real_values_are_accepted():
    assert Settings(**GOOD).telemetry_api_key == GOOD["telemetry_api_key"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("telemetry_api_key", "change-me-random-api-key"),
        ("database_url", "postgresql://postgres:change-me-strong-password@db:5432/robotfleet"),
        ("bootstrap_admin_password", "change-me-admin-password"),
        ("jwt_secret", "change-me-jwt-secret-that-is-long-enough"),
    ],
)
def test_a_placeholder_from_the_example_refuses_to_start(field, value):
    with pytest.raises(ValidationError, match="make_env.py"):
        Settings(**{**GOOD, field: value})


def test_placeholders_are_refused_outside_production_too():
    with pytest.raises(ValidationError):
        Settings(**{**GOOD, "app_env": "development", "telemetry_api_key": "change-me-x"})
