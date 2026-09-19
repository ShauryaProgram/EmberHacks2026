from argparse import Namespace

from app import cli


class FakeClient:
    base_url = "http://test"

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.posts = []

    def get(self, path):
        return self.responses[path]

    def post(self, path, body=None):
        self.posts.append((path, body))
        return self.responses[path]


def test_onboard_submits_token_and_preferences(monkeypatch, capsys):
    client = FakeClient({
        "/api/sync/status": {"latest": None},
        "/api/onboarding": {"user": {"name": "Ada"}},
    })
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "secret-token")
    answers = iter(["America/Vancouver", "5,2"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    result = cli.cmd_onboard(client, Namespace(
        token=None, timezone=None, reminders=None, no_wait=True, timeout=10
    ))

    assert result == 0
    assert client.posts == [("/api/onboarding", {
        "quercus_api_token": "secret-token",
        "timezone": "America/Vancouver",
        "reminder_offsets_days": [5, 2],
    })]
    assert "Onboarded Ada" in capsys.readouterr().out


def test_doctor_explains_incomplete_setup(capsys):
    client = FakeClient({
        "/health": {"status": "ok", "openrouter_configured": False},
        "/openapi.json": {"paths": {"/%s" % index: {} for index in range(16)}},
        "/api/notifications/vapid-public-key": {"publicKey": "x" * 80},
        "/api/onboarding/status": {"onboarded": False, "profile": None},
    })

    assert cli.cmd_doctor(client, Namespace()) == 1
    output = capsys.readouterr().out
    assert "[PASS] API reachable" in output
    assert "[TODO] Quercus onboarding" in output
    assert "add OPENROUTER_API_KEY to .env" in output


def test_table_truncates_without_changing_width(capsys):
    cli.print_table([{"name": "a very long value"}], [("name", "NAME", 8)])
    lines = capsys.readouterr().out.splitlines()

    assert lines[-1] == "a ver..."
    assert all(len(line) == 8 for line in lines)
