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

    context = build_ssl_context(
        settings.host,
        tls_verify=settings.tls_verify,
        tls_cert=settings.tls_cert,
        insecure_localhost=settings.insecure_localhost,
    )
    try:
        if settings.security == "ssl":
            smtp = smtplib.SMTP_SSL(
                settings.host,
                settings.smtp_port,
                timeout=settings.timeout,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(settings.host, settings.smtp_port, timeout=settings.timeout)
    except ConnectionRefusedError as exc:
        raise MailcliError(
            f"Could not connect to SMTP at {settings.host}:{settings.smtp_port}. "
            "If you use Proton Mail Bridge, confirm it is running and listening."
        ) from exc
    except TimeoutError as exc:
        raise MailcliError(
            f"Timed out connecting to SMTP at {settings.host}:{settings.smtp_port}."
        ) from exc
    except OSError as exc:
        raise MailcliError(
            f"Could not connect to SMTP at {settings.host}:{settings.smtp_port}: {exc}"
        ) from exc
    try:
        smtp.ehlo()
        if settings.security != "ssl":
            if not smtp.has_extn("starttls"):
                raise MailcliError(
                    f"The SMTP server at {settings.host}:{settings.smtp_port} does not offer STARTTLS."
                )
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(settings.user, settings.password)
        smtp.send_message(message, from_addr=settings.user, to_addrs=cleaned)
    except MailcliError:
        raise
    except ssl.SSLError as exc:
        raise MailcliError(
            f"TLS handshake with SMTP {settings.host}:{settings.smtp_port} failed. "
            "Bridge uses a self-signed certificate: set tls_verify = false for localhost, "
            "or set tls_cert to the certificate you exported from Bridge. "
            f"({exc.__class__.__name__})"
        ) from exc
    except smtplib.SMTPAuthenticationError as exc:
        raise MailcliError(
            "SMTP login failed. Check the account name and MAILCLI_PASSWORD."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailcliError("The SMTP server refused the recipient.") from exc
    except smtplib.SMTPException as exc:
        raise MailcliError(f"SMTP rejected the message. {_public(exc, settings.password)}") from exc
    except OSError as exc:
        raise MailcliError(f"The SMTP connection to {settings.host} failed.") from exc
    finally:
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            try:
                smtp.close()
            except OSError:
                pass


def _public(exc: BaseException, password: str) -> str:
    text = " ".join(str(part) for part in exc.args) if exc.args else str(exc)
    if password:
        text = text.replace(password, "***")
    text = " ".join(text.split())
    if len(text) > 180:
        text = text[:177] + "..."
    return text
