import asyncio

import pytest

from app.avatar.vts import VTSAuthDenied, VTSClient, VTSError

DISCONNECT = object()


class FakeTransport:
    """Scripted VTS: replies maps a request message type to a data dict, an APIError dict, None (silence) or DISCONNECT."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.sent = []
        self.inbox = asyncio.Queue()
        self.closed = False

    async def send(self, msg):
        self.sent.append(msg)
        kind = msg["messageType"].removesuffix("Request")
        reply = self.replies.get(kind)
        if reply is DISCONNECT:
            self.inbox.put_nowait(DISCONNECT)
        elif isinstance(reply, dict) and "errorID" in reply:
            self.inbox.put_nowait({"messageType": "APIError", "requestID": msg["requestID"], "data": reply})
        elif reply is not None:
            self.inbox.put_nowait({"messageType": kind + "Response", "requestID": msg["requestID"], "data": reply})

    async def receive(self):
        item = await self.inbox.get()
        if item is DISCONNECT:
            raise ConnectionError("closed")
        return item

    async def close(self):
        self.closed = True

    def types(self):
        return [m["messageType"] for m in self.sent]


def make(tmp_path, replies, notes=None):
    t = FakeTransport(replies)
    c = VTSClient(t, "Chibi", "me", tmp_path / "cfg" / ".vts_token", (notes if notes is not None else []).append)
    return t, c


async def test_request_envelope_and_response_data(tmp_path):
    t, c = make(tmp_path, {"HotkeyTrigger": {"hotkeyID": "x"}})
    out = await c.request("HotkeyTrigger", {"hotkeyID": "x"})
    assert out == {"hotkeyID": "x"}
    m = t.sent[0]
    assert m["apiName"] == "VTubeStudioPublicAPI" and m["apiVersion"] == "1.0"
    assert m["messageType"] == "HotkeyTriggerRequest" and m["requestID"]
    assert m["data"] == {"hotkeyID": "x"}
    await c.close()


async def test_api_error_raises_vtserror(tmp_path):
    t, c = make(tmp_path, {"HotkeyTrigger": {"errorID": 50, "message": "nope"}})
    with pytest.raises(VTSError) as e:
        await c.request("HotkeyTrigger")
    assert "50" in str(e.value) and "nope" in str(e.value)
    await c.close()


async def test_first_run_requests_token_saves_it_and_tells_owner(tmp_path):
    notes = []
    t, c = make(tmp_path, {"AuthenticationToken": {"authenticationToken": "tok"},
                           "Authentication": {"authenticated": True}}, notes)
    await c.authenticate()
    assert t.types() == ["AuthenticationTokenRequest", "AuthenticationRequest"]
    assert t.sent[0]["data"] == {"pluginName": "Chibi", "pluginDeveloper": "me"}
    assert t.sent[1]["data"]["authenticationToken"] == "tok"
    assert (tmp_path / "cfg" / ".vts_token").read_text(encoding="utf-8") == "tok"
    assert len(notes) == 1 and "Allow" in notes[0]
    await c.close()


async def test_saved_token_reused(tmp_path):
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / ".vts_token").write_text("abc", encoding="utf-8")
    notes = []
    t, c = make(tmp_path, {"Authentication": {"authenticated": True}}, notes)
    await c.authenticate()
    assert t.types() == ["AuthenticationRequest"]
    assert t.sent[0]["data"]["authenticationToken"] == "abc"
    assert notes == []
    await c.close()


async def test_rejected_token_requests_new_one(tmp_path):
    (tmp_path / "cfg").mkdir()
    path = tmp_path / "cfg" / ".vts_token"
    path.write_text("old", encoding="utf-8")
    t, c = make(tmp_path, {"AuthenticationToken": {"authenticationToken": "new"}})
    answers = iter([False, True])

    orig = t.send

    async def send(msg):
        if msg["messageType"] == "AuthenticationRequest":
            t.replies["Authentication"] = {"authenticated": next(answers), "reason": "bad"}
        await orig(msg)

    t.send = send
    await c.authenticate()
    assert t.types() == ["AuthenticationRequest", "AuthenticationTokenRequest", "AuthenticationRequest"]
    assert t.sent[2]["data"]["authenticationToken"] == "new"
    assert path.read_text(encoding="utf-8") == "new"
    await c.close()


async def test_denied_popup_raises(tmp_path):
    t, c = make(tmp_path, {"AuthenticationToken": {"errorID": 50, "message": "denied"}})
    with pytest.raises(VTSAuthDenied):
        await c.authenticate()
    assert not (tmp_path / "cfg" / ".vts_token").exists()
    await c.close()


async def test_unanswered_popup_raises_denied(tmp_path, monkeypatch):
    monkeypatch.setattr("app.avatar.vts.TOKEN_WAIT", 0.05)
    t, c = make(tmp_path, {"AuthenticationToken": None})
    with pytest.raises(VTSAuthDenied):
        await c.authenticate()
    assert not (tmp_path / "cfg" / ".vts_token").exists()
    await c.close()


async def test_other_token_error_is_not_denied(tmp_path):
    t, c = make(tmp_path, {"AuthenticationToken": {"errorID": 1, "message": "busy"}})
    with pytest.raises(VTSError) as e:
        await c.authenticate()
    assert not isinstance(e.value, VTSAuthDenied)
    await c.close()


async def test_disconnect_fails_pending_and_later_requests(tmp_path):
    t, c = make(tmp_path, {"HotkeyTrigger": DISCONNECT})
    with pytest.raises(VTSError, match="disconnected"):
        await c.request("HotkeyTrigger")
    with pytest.raises(VTSError, match="disconnected"):
        await c.request("HotkeyTrigger")
    await c.close()


async def test_timeout_raises(tmp_path):
    t, c = make(tmp_path, {"HotkeyTrigger": None})
    with pytest.raises(VTSError):
        await c.request("HotkeyTrigger", timeout=0.05)
    await c.close()


async def test_non_dict_frame_skipped(tmp_path):
    t, c = make(tmp_path, {"HotkeyTrigger": {"ok": 1}})
    t.inbox.put_nowait("garbage")
    t.inbox.put_nowait({"messageType": "X", "requestID": "unknown"})
    assert await c.request("HotkeyTrigger") == {"ok": 1}
    await c.close()


async def test_unexpected_reader_error_fails_fast(tmp_path):
    t, c = make(tmp_path, {"HotkeyTrigger": None})

    async def boom():
        raise RuntimeError("boom")

    t.receive = boom
    with pytest.raises(VTSError, match="disconnected"):
        await asyncio.wait_for(c.request("HotkeyTrigger", timeout=30), 2)
    with pytest.raises(VTSError, match="disconnected"):
        await asyncio.wait_for(c.request("HotkeyTrigger", timeout=30), 2)
    await c.close()
