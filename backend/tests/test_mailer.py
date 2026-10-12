from app.auth import mailer as mailer_mod
from app.auth.mailer import ConsoleMailer, Message, SmtpMailer, make_mailer
from app.config import Settings


def test_make_mailer_picks_console_without_smtp():
    assert isinstance(make_mailer(Settings()), ConsoleMailer)
    assert isinstance(make_mailer(Settings(smtp_host="smtp.test")), SmtpMailer)


def test_console_mailer_records_messages():
    m = ConsoleMailer()
    m.send(Message("a@b.co", "Hi", "Body"))
    assert m.sent == [Message("a@b.co", "Hi", "Body")]


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, email):
        self.calls.append(("send", email["To"], email["Subject"], email["From"]))


def test_smtp_mailer_uses_starttls_and_login(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", FakeSMTP)
    settings = Settings(smtp_host="smtp.test", smtp_port=587, smtp_user="u", smtp_password="p", smtp_from="noreply@zerde.ai")
    SmtpMailer(settings).send(Message("a@b.co", "Код", "Тело"))
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.test", 587)
    assert smtp.calls == ["starttls", ("login", "u", "p"), ("send", "a@b.co", "Код", "noreply@zerde.ai")]


def test_smtp_mailer_uses_ssl_on_465(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP_SSL", FakeSMTP)
    settings = Settings(smtp_host="smtp.test", smtp_port=465, smtp_from="noreply@zerde.ai")
    SmtpMailer(settings).send(Message("a@b.co", "S", "B"))
    assert FakeSMTP.instances[0].calls == [("send", "a@b.co", "S", "noreply@zerde.ai")]


def test_console_mailer_keeps_only_the_latest_20_messages():
    m = ConsoleMailer()
    for i in range(25):
        m.send(Message("a@b.co", f"#{i}", "Body"))
    assert [message.subject for message in m.sent] == [f"#{i}" for i in range(5, 25)]
