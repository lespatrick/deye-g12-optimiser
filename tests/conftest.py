"""Pytest configuration and shared fixtures for deye_g12_opt tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in tests automatically."""
    yield

