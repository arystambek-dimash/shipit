"""Outgoing email: SMTP when configured, otherwise the console (development)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from ..config import Settings

log = logging.getLogger("zerde.mail")


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    def send(self, message: Message) -> None: ...


class ConsoleMailer:
    """Development mailer: logs messages and keeps the most recent ones in memory (tests read them)."""

    KEEP = 20

    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)
        del self.sent[: -self.KEEP]
        log.warning("Email to %s | %s\n%s", message.to, message.subject, message.body)


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: Message) -> None:
        s = self.settings
        email = EmailMessage()
        email["From"] = s.smtp_from
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)
        context = ssl.create_default_context()
        if s.smtp_port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=context)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
        with server:
            if s.smtp_port != 465 and s.smtp_starttls:
                server.starttls(context=context)
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(email)


def make_mailer(settings: Settings) -> Mailer:
    return SmtpMailer(settings) if settings.smtp_host else ConsoleMailer()
