"""Test infrastructure for the provider_credential_store suite.

Sets email_validator.TEST_ENVIRONMENT = True so that RFC 2606 / special-use
domains (e.g. *.test, *.example) pass Pydantic's EmailStr validation.
This mirrors the oidc_tenant_config conftest.
"""

from __future__ import annotations

import email_validator
import pytest


@pytest.fixture(autouse=True, scope="session")
def _allow_test_email_domains() -> None:  # type: ignore[return]
    """Allow RFC 2606 special-use email domains (*.test, *.example, etc.) in tests."""
    original = email_validator.TEST_ENVIRONMENT
    email_validator.TEST_ENVIRONMENT = True
    yield  # type: ignore[misc]
    email_validator.TEST_ENVIRONMENT = original
