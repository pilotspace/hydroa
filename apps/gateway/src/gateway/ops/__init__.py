"""Operator-wide (cross-tenant) surfaces behind the ops-auth boundary.

A SEPARATE authority from the tenant-facing `/admin/*` API: callers authenticate with an
mTLS client cert (validated by Envoy, forwarded as XFCC), never a tenant JWT. The one audited
exception to the tenant-scoping invariant lives here.
"""
