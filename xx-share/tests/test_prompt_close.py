"""Stale-prompt safety: the notification closes when a session ends.

After Accept/Decline, or when the session expires or the sender
cancels, the notification must close so a stale Accept button cannot
act on a finished session; ``_resolve`` ignores unknown or finished
session ids.
"""
import pytest

import service as service_module
from config import ShareConfig
from history import HistoryStore
from notify import ACTION_ACCEPT, ACTION_DECLINE
from protocol import DeviceInfo, UploadFile
from server import ACCEPTED, CANCELLED, DECLINED, FINISHED, Session
from service import ShareService


class FakeServer:
    def __init__(self, **kwargs):
        FakeServer.last_kwargs = kwargs

    def start(self):
        pass

    def stop(self):
        pass


class FakeDiscovery:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class FakePrompt:
    def __init__(self, _summary, _body, on_action):
        self.on_action = on_action
        self.closed = False

    def show(self):
        return True

    def close(self):
        self.closed = True


class PromptServer:
    """Minimal server double with a real session table."""

    def __init__(self):
        self.sessions = {}
        self.accepted = []
        self.declined = []

    def accept_session(self, session_id):
        session = self.sessions[session_id]
        session.state = ACCEPTED
        session.decision.set()
        self.accepted.append(session_id)
        return []

    def decline_session(self, session_id):
        self.sessions[session_id].state = DECLINED
        self.declined.append(session_id)


def _service(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "ShareServer", FakeServer)
    monkeypatch.setattr(service_module, "Discovery", FakeDiscovery)
    config = ShareConfig(tmp_path / "config")
    svc = ShareService(
        config,
        tmp_path / "cert.pem",
        tmp_path / "key.pem",
        "fp",
        schedule=lambda seconds, cb: None,
        downloads=tmp_path / "dl",
        staging=tmp_path / "staging",
        history=HistoryStore(tmp_path / "history.json"),
    )
    svc.prompt_factory = FakePrompt
    return svc


def _session(session_id="s1"):
    sender = DeviceInfo(alias="sender", fingerprint="sender-fp")
    files = {"f1": UploadFile(id="f1", file_name="hello.txt", size=5,
                              file_type="text/plain")}
    return Session(session_id=session_id, sender=sender, files=files,
                   tokens={"f1": "tok"})


def _prompted(svc, session):
    svc.server = PromptServer()
    svc.server.sessions[session.session_id] = session
    svc._on_session(session)
    prompt = svc._prompts[session.session_id]
    return svc.server, prompt


def test_accept_closes_prompt(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    session = _session()
    server, prompt = _prompted(svc, session)
    prompt.on_action(ACTION_ACCEPT)
    assert server.accepted == ["s1"]
    assert prompt.closed is True
    assert svc._prompts == {}


def test_decline_closes_prompt(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    session = _session()
    server, prompt = _prompted(svc, session)
    prompt.on_action(ACTION_DECLINE)
    assert server.declined == ["s1"]
    assert prompt.closed is True
    assert svc._prompts == {}


def test_resolve_ignores_unknown_session(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc.server = PromptServer()
    svc._resolve("nope", ACTION_ACCEPT)
    assert svc.server.accepted == []
    assert svc.server.declined == []


def test_resolve_ignores_finished_session(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    session = _session()
    server, prompt = _prompted(svc, session)
    session.state = FINISHED
    prompt.on_action(ACTION_ACCEPT)
    assert server.accepted == []
    assert prompt.closed is True
    assert svc._prompts == {}


@pytest.mark.parametrize("state", [DECLINED, CANCELLED, FINISHED])
def test_resolve_ignores_closed_states(tmp_path, monkeypatch, state):
    svc = _service(tmp_path, monkeypatch)
    session = _session()
    server, prompt = _prompted(svc, session)
    session.state = state
    prompt.on_action(ACTION_DECLINE)
    assert server.declined == []
    assert prompt.closed is True


def test_complete_closes_prompt(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    session = _session()
    _server, prompt = _prompted(svc, session)
    svc._on_complete(session)
    assert prompt.closed is True
    assert svc._prompts == {}


def test_server_cancel_hook_closes_prompt(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc.start(["127.0.0.1"])
    assert "on_cancel" in FakeServer.last_kwargs
    session = _session()
    _server, prompt = _prompted(svc, session)
    FakeServer.last_kwargs["on_cancel"](session)
    assert prompt.closed is True
    assert svc._prompts == {}
