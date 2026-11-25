"""Send one message through SMTP STARTTLS.

The From address is the configured account. Proton Mail Bridge rejects a
From that does not belong to the mailbox, so mailcli does not offer a
separate From flag.
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from mailcli.config import Settings
from mailcli.errors import MailcliError
from mailcli.oauth import xoauth2_sasl
from mailcli.tlsutil import build_ssl_context


def send_message(settings: Settings, recipients: list[str], subject: str, body: str) -> None:
    """Deliver ``body`` to ``recipients`` and return when the server accepts it."""
    cleaned = [item.strip() for item in recipients if item.strip()]
    if not cleaned:
        raise MailcliError("Name at least one recipient with --to.")
    if subject is None:
        raise MailcliError("A subject is required.")
    message = EmailMessage()
    message["From"] = settings.user
    message["To"] = ", ".join(cleaned)
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message.set_content(body if body is not None else "")

    smtp_host = settings.smtp_host or settings.host
    smtp_security = settings.smtp_security or settings.security
    context = build_ssl_context(
        smtp_host,
        tls_verify=settings.tls_verify,
        tls_cert=settings.tls_cert,
        insecure_localhost=settings.insecure_localhost,
    )
    try:
        if smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(
                smtp_host,
                settings.smtp_port,
                timeout=settings.timeout,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(smtp_host, settings.smtp_port, timeout=settings.timeout)
    except ConnectionRefusedError as exc:
        raise MailcliError(
            f"Could not connect to SMTP at {smtp_host}:{settings.smtp_port}. "
            "Check the host and port from mailcli setup."
        ) from exc
    except TimeoutError as exc:
        raise MailcliError(
            f"Timed out connecting to SMTP at {smtp_host}:{settings.smtp_port}."
        ) from exc
    except OSError as exc:
        raise MailcliError(
            f"Could not connect to SMTP at {smtp_host}:{settings.smtp_port}: {exc}"
        ) from exc
    try:
        smtp.ehlo()
        if smtp_security != "ssl":
            if not smtp.has_extn("starttls"):
                raise MailcliError(
                    f"The SMTP server at {smtp_host}:{settings.smtp_port} does not offer STARTTLS."
                )
            smtp.starttls(context=context)
            smtp.ehlo()
        if settings.auth == "xoauth2":
            if not settings.access_token:
                raise MailcliError(
                    "This account uses OAuth, but there is no access token. "
                    "Run python -m mailcli setup."
                )
            user = settings.user
            token = settings.access_token

            def mechanism(_challenge: bytes | None = None, _user: str = user, _token: str = token) -> str:
                return xoauth2_sasl(_user, _token)

            smtp.auth("XOAUTH2", mechanism)
        else:
            smtp.login(settings.user, settings.password)
        smtp.send_message(message, from_addr=settings.user, to_addrs=cleaned)
    except MailcliError:
        raise
    except ssl.SSLError as exc:
        raise MailcliError(
            f"TLS handshake with SMTP {settings.smtp_host or settings.host}:{settings.smtp_port} failed. "
            "For a local server with a self-signed certificate, set tls_verify = false "
            "only when the host is loopback, or set tls_cert to the PEM file. "
            f"({exc.__class__.__name__})"
        ) from exc
    except smtplib.SMTPAuthenticationError as exc:
        raise MailcliError(
            "SMTP login failed. Check the account name and MAILCLI_PASSWORD."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailcliError("The SMTP server refused the recipient.") from exc
    except smtplib.SMTPException as exc:
        raise MailcliError(
            "SMTP rejected the message. "
            + _public(exc, settings.password, settings.access_token)
        ) from exc
    except OSError as exc:
        raise MailcliError(f"The SMTP connection to {settings.smtp_host or settings.host} failed.") from exc
    finally:
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            try:
                smtp.close()
            except OSError:
                pass


def _public(exc: BaseException, password: str, access_token: str = "") -> str:
    text = " ".join(str(part) for part in exc.args) if exc.args else str(exc)
    for secret in (password, access_token):
        if secret:
            text = text.replace(secret, "***")
    text = " ".join(text.split())
    if len(text) > 180:
        text = text[:177] + "..."
    return text
