"""EmailSender: config parsing, not-configured no-op, and a mocked SMTP send."""
from __future__ import annotations

from hearth.adapters.email_sender import EmailSender


class FakeRepo:
    def __init__(self, conn=None):
        self._c = conn

    def get_connection(self, kind):
        return self._c if kind == "smtp" else None


def test_not_configured_is_safe_noop():
    s = EmailSender(FakeRepo(None))
    assert s.configured() is False
    assert s.send("a@b.com", "subj", "<p>h</p>") is False


def test_send_uses_starttls_and_login(monkeypatch):
    conn = {"url": "smtp.example.com", "token": "pw",
            "options": {"port": 587, "username": "u@x.com", "from": "u@x.com",
                        "from_name": "Hearth", "tls": "starttls"}}
    seen: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=0):
            seen["host"], seen["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            seen["ehlo"] = seen.get("ehlo", 0) + 1

        def starttls(self, context=None):
            seen["starttls"] = True

        def login(self, u, p):
            seen["login"] = (u, p)

        def send_message(self, msg):
            seen["msg"] = msg

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    s = EmailSender(FakeRepo(conn))
    assert s.configured() is True
    assert s.send(["a@b.com", ""], "Subj", "<p>hi</p>", "hi") is True
    assert (seen["host"], seen["port"]) == ("smtp.example.com", 587)
    assert seen.get("starttls") is True
    assert seen["login"] == ("u@x.com", "pw")
    msg = seen["msg"]
    assert msg["To"] == "a@b.com" and msg["Subject"] == "Subj"
    assert "Hearth" in msg["From"] and "u@x.com" in msg["From"]


def test_send_failure_returns_false(monkeypatch):
    conn = {"url": "smtp.example.com", "token": "pw",
            "options": {"port": 465, "username": "u@x.com", "tls": "ssl"}}

    class Boom:
        def __init__(self, *a, **k):
            raise OSError("connection refused")

    monkeypatch.setattr("smtplib.SMTP_SSL", Boom)
    s = EmailSender(FakeRepo(conn))
    assert s.send("a@b.com", "s", "<p>h</p>") is False


def test_drops_header_injection_recipients(monkeypatch):
    """A recipient carrying CR/LF (Bcc smuggling) is rejected; only clean
    addresses reach the relay."""
    conn = {"url": "smtp.example.com", "token": "pw",
            "options": {"port": 587, "username": "u@x.com", "from": "u@x.com",
                        "from_name": "Hearth", "tls": "starttls"}}
    seen: dict = {}

    class FakeSMTP:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): ...
        def starttls(self, context=None): ...
        def login(self, u, p): ...
        def send_message(self, msg): seen["msg"] = msg

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    s = EmailSender(FakeRepo(conn))
    # one poisoned + one clean recipient
    assert s.send(["a@b.com\r\nBcc: evil@x.com", "good@b.com"], "S", "<p>h</p>") is True
    assert seen["msg"]["To"] == "good@b.com"          # injected one dropped
    assert "\r" not in seen["msg"]["To"] and "\n" not in seen["msg"]["To"]


def test_all_invalid_recipients_is_noop(monkeypatch):
    conn = {"url": "smtp.example.com", "token": "pw",
            "options": {"username": "u@x.com", "from": "u@x.com", "tls": "starttls"}}
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect")))
    s = EmailSender(FakeRepo(conn))
    assert s.send("not-an-email", "S", "<p>h</p>") is False
