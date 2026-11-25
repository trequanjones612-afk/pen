"""TLS contexts for Proton Mail Bridge and other local mail servers.

Bridge listens on 127.0.0.1 with a certificate it generates itself. Verification
can be turned off only for loopback, or the exported Bridge certificate can be
pinned with ``tls_cert``.
"""

from __future__ import annotations

import ssl

from mailcli.errors import MailcliError

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def is_loopback(host: str) -> bool:
    """Return True when ``host`` is a loopback name or address."""
    name = host.strip().lower().strip("[]")
    return name in _LOOPBACK


def build_ssl_context(
    host: str,
    *,
    tls_verify: bool,
    tls_cert: str | None,
    insecure_localhost: bool,
) -> ssl.SSLContext:
    """Build a client context for STARTTLS.

    A certificate path is loaded as a trust anchor and verification stays on.
    ``tls_verify`` false and ``--insecure-localhost`` skip verification only
    when ``host`` is loopback. Anywhere else they are refused.
    """
    context = ssl.create_default_context()
    if tls_cert:
        try:
            context.load_verify_locations(cafile=tls_cert)
        except (ssl.SSLError, OSError) as exc:
            raise MailcliError(f"Could not load TLS certificate {tls_cert}: {exc}") from exc
        return context

    if tls_verify and not insecure_localhost:
        return context

    if not is_loopback(host):
        raise MailcliError(
            f"Refusing to disable TLS verification for {host}. "
            "tls_verify = false and --insecure-localhost are allowed only "
            "when host is 127.0.0.1, localhost, or ::1. "
            "For Proton Mail Bridge, export its certificate and set tls_cert, "
            "or point host at the loopback address."
        )
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context
