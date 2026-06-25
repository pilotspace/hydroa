"""Agent OAuth — headless device-authorization grant (RFC 8628) credential store.

Issues a THIRD credential class (the *agent access token*), distinct from the human
session JWT (tenants/auth) and the tenant API key (keys/). Secrets are SHA-256-hashed
at rest exactly like API keys; tokens are opaque, revocable, and expiry-enforced.
"""
