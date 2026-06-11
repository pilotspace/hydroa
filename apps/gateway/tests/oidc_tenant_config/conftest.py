"""Test infrastructure for oidc_tenant_config suite.

Sets email_validator.TEST_ENVIRONMENT = True so that RFC 2606 / special-use
domains (e.g. *.test, *.example) pass Pydantic's EmailStr validation.
This is required because the test suite uses emails like owner@t2.test
which are valid test addresses but rejected by the email-validator library
in production mode.

This conftest is scoped to the oidc_tenant_config directory only.
"""

import email_validator
import pytest


@pytest.fixture(autouse=True, scope="session")
def _allow_test_email_domains() -> None:  # type: ignore[return]
    """Allow RFC 2606 special-use email domains (*.test, *.example, etc.) in tests."""
    original = email_validator.TEST_ENVIRONMENT
    email_validator.TEST_ENVIRONMENT = True
    yield  # type: ignore[misc]
    email_validator.TEST_ENVIRONMENT = original
