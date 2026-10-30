import asyncio
import logging
import threading
from pathlib import Path

import numpy as np
import pytest

from app.config import AvatarSettings, load_settings

EXPRESSIONS = ["EyesLove.exp3.json", "EyesCry.exp3.json", "SignAngry.exp3.json", "SignShock.exp3.json"]
FACEPALM = "5fbc7fac3d3649c6a8901600261a6983"
LOUD = (np.ones(16000) * 20000).astype(np.int16)


class FakeVTS:
    """Fake VTube Studio transport: answers every request, records them, can drop the connection."""

    def __init__(self, expressions=EXPRESSIONS):
        self.expressions = expressions
        self.sent = []
        self.inbox = asyncio.Queue()
        self.dead = False
        self.closed = False

    def reply_for(self, kind, data):
        if kind == "AuthenticationToken":
            return {"authenticationToken": "tok"}
        if kind == "Authentication":
            return {"authenticated": True}
        if kind == "HotkeysInCurrentModel":
            return {"availableHotkeys": [
                {"name": "Anim Shake", "hotkeyID": "aaa", "file": "Love.motion3.json", "type": "TriggerAnimation"},
                {"name": "", "hotkeyID": FACEPALM, "file": "Shock.motion3.json", "type": "TriggerAnimation"},
            ]}
        if kind == "ExpressionState":
            return {"expressions": [{"name": f, "file": f, "active": False} for f in self.expressions]}
        return {}

    async def send(self, msg):
        if self.dead:
            raise ConnectionError("closed")
        self.sent.append(msg)
        kind = msg["messageType"].removesuffix("Request")
        self.inbox.put_nowait({"messageType": kind + "Response", "requestID": msg["requestID"],
                               "data": self.reply_for(kind, msg["data"])})

    async def receive(self):
        item = await self.inbox.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    async def close(self):
        self.closed = True

    def drop(self):
        self.dead = True
        self.inbox.put_nowait(None)

    def of(self, kind):
        return [m["data"] for m in self.sent if m["messageType"] == kind + "Request"]

    def injected(self):
        """List of {param: value} dicts, one per InjectParameterData request."""
        return [{p["id"]: p["value"] for p in d["parameterValues"]} for d in self.of("InjectParameterData")]


class Rig:
    def __init__(self, tmp_path, fail_first=0, always_fail=False, expressions=EXPRESSIONS):
        from app.avatar.controller import VTSAvatar
        self.lines = []
        self.transports = []
        self.fails_left = fail_first
        self.always_fail = always_fail
        self.expressions = expressions
        self.now = 0.0
        (tmp_path / ".vts_token").write_text("tok", encoding="utf-8")  # skip the first-run Allow popup
        self.settings = AvatarSettings(
            enabled=True, url="ws://test:1", plugin_name="P", plugin_developer="D",
            token_file=tmp_path / ".vts_token", reconnect_s=0.01, fps=200,
            mouth_gain=4.0, mouth_smoothing=0.4,
            expressions={"happy": "EyesLove.exp3.json", "sulking": "EyesCry.exp3.json",
                         "annoyed": "SignAngry.exp3.json", "shocked": "SignShock.exp3.json"},
            actions={"celebrate": "Anim Shake", "facepalm": FACEPALM},
            thinking={"FaceAngleZ": 12, "FaceAngleY": 8, "EyeRightY": 0.8},
        )
        self.av = VTSAvatar(self.settings, self.lines.append, connect=self.connect, clock=lambda: self.now)

    async def connect(self, url):
        assert url == "ws://test:1"
        if self.always_fail or self.fails_left > 0:
            self.fails_left -= 1
            raise OSError("refused")
        t = FakeVTS(self.expressions)
        self.transports.append(t)
        return t

    @property
    def t(self):
        return self.transports[-1]

    async def started(self):
        await self.av.start()
        await until(lambda: "connected" in self.av.status() and "not" not in self.av.status())


async def until(pred, timeout=2.0):
    async def wait():
        while not pred():
            await asyncio.sleep(0.002)
    await asyncio.wait_for(wait(), timeout)


async def ticks(n=5):
    await asyncio.sleep(n / 200 + 0.01)


@pytest.fixture
async def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    await r.av.close()


async def test_connect_and_status(rig):
    assert rig.av.status() == "avatar: not connected (ws://test:1)"
    await rig.started()
    assert rig.av.status() == "avatar: connected (ws://test:1)"
    assert rig.lines.count("avatar: connected") == 1
    assert "not reachable" not in " ".join(rig.lines)
    kinds = [m["messageType"] for m in rig.t.sent]
    assert "HotkeysInCurrentModelRequest" in kinds and "ExpressionStateRequest" in kinds
    assert rig.t.of("InjectParameterData") == []  # nothing active -> nothing injected


async def test_unreachable_prints_once_and_retries(tmp_path):
    r = Rig(tmp_path, fail_first=3)
    try:
        await r.started()
        assert r.lines == ["avatar: VTube Studio not reachable, retrying in the background", "avatar: connected"]
    finally:
        await r.av.close()


async def test_reply_sequence(rig):
    await rig.started()
    rig.av.thinking()
    await until(lambda: any(i.get("FaceAngleZ") == 12 for i in rig.t.injected()))
    assert any(i.get("EyeRightY") == 0.8 and i.get("FaceAngleY") == 8 for i in rig.t.injected())
    rig.av.play_started()
    n = len(rig.t.injected())
    await ticks()
    assert not any("FaceAngleZ" in i for i in rig.t.injected()[n:])
    rig.av.react("happy", "celebrate")
    await until(lambda: rig.t.of("HotkeyTrigger"))
    assert rig.t.of("ExpressionActivation") == [{"expressionFile": "EyesLove.exp3.json", "active": True}]
    assert rig.t.of("HotkeyTrigger") == [{"hotkeyID": "Anim Shake"}]
    rig.av.reply_done()
    await until(lambda: len(rig.t.of("ExpressionActivation")) == 2)
    assert rig.t.of("ExpressionActivation")[1] == {"expressionFile": "EyesLove.exp3.json", "active": False}
    inject = rig.t.of("InjectParameterData")[0]
    assert inject["faceFound"] is False and inject["mode"] == "set"


async def test_switching_expression_deactivates_previous(rig):
    await rig.started()
    rig.av.react("happy", None)
    rig.av.react("annoyed", None)
    await until(lambda: len(rig.t.of("ExpressionActivation")) == 3)
    assert rig.t.of("ExpressionActivation") == [
        {"expressionFile": "EyesLove.exp3.json", "active": True},
        {"expressionFile": "EyesLove.exp3.json", "active": False},
        {"expressionFile": "SignAngry.exp3.json", "active": True},
    ]
    assert rig.t.of("HotkeyTrigger") == []


async def test_unmapped_emotion_sends_nothing(rig):
    await rig.started()
    rig.av.react("smug", None)
    rig.av.react("confused", "dance")
    await ticks()
    assert rig.t.of("ExpressionActivation") == [] and rig.t.of("HotkeyTrigger") == []


async def test_missing_mapping_logged_once_and_skipped(tmp_path, caplog):
    r = Rig(tmp_path, expressions=[e for e in EXPRESSIONS if e != "SignAngry.exp3.json"])
    try:
        await r.started()
        with caplog.at_level(logging.INFO, logger="app.avatar.controller"):
            r.av.react("annoyed", None)
            r.av.react("annoyed", None)
            await ticks()
            assert r.t.of("ExpressionActivation") == []
            assert len([x for x in caplog.records if "SignAngry.exp3.json" in x.getMessage()]) == 1
            r.av.react("happy", "facepalm")
            await until(lambda: r.t.of("HotkeyTrigger"))
            assert r.t.of("ExpressionActivation") == [{"expressionFile": "EyesLove.exp3.json", "active": True}]
            assert r.t.of("HotkeyTrigger") == [{"hotkeyID": FACEPALM}]
    finally:
        await r.av.close()


async def test_missing_hotkey_skipped(rig):
    rig.settings.actions["wave"] = "No Such Hotkey"
    await rig.started()
    rig.av.react("happy", "wave")
    await until(lambda: rig.t.of("ExpressionActivation"))
    await ticks()
    assert rig.t.of("HotkeyTrigger") == []


async def test_mouth_follows_audio_then_closes(rig):
    await rig.started()
    rig.av.play_started()
    rig.av.audio(LOUD, 16000)
    await until(lambda: any(i.get("MouthOpen", 0) > 0 for i in rig.t.injected()))
    rig.now = 0.5
    await ticks()
    assert rig.t.injected()[-1]["MouthOpen"] > 0.5
    rig.av.play_ended()
    await until(lambda: rig.t.injected()[-1].get("MouthOpen") == 0)
    n = len(rig.t.injected())
    await ticks()
    assert [i for i in rig.t.injected()[n:] if "MouthOpen" in i] == []


async def test_track_past_its_end_injects_zero(rig):
    await rig.started()
    rig.av.play_started()
    rig.av.audio(LOUD, 16000)
    await until(lambda: rig.t.injected())
    rig.now = 5.0
    await ticks()
    assert rig.t.injected()[-1]["MouthOpen"] == 0


async def test_muted_closes_mouth_and_stops_values(rig):
    await rig.started()
    rig.av.thinking()
    rig.av.react("happy", None)
    rig.av.play_started()
    rig.av.audio(LOUD, 16000)
    await until(lambda: any(i.get("MouthOpen", 0) > 0 for i in rig.t.injected()))
    rig.av.muted()
    await until(lambda: rig.t.injected()[-1].get("MouthOpen") == 0)
    assert "FaceAngleZ" not in rig.t.injected()[-1]
    n = len(rig.t.injected())
    await ticks()
    assert rig.t.injected()[n:] == []
    assert rig.t.of("ExpressionActivation")[-1] == {"expressionFile": "EyesLove.exp3.json", "active": False}


async def test_late_audio_after_mute_is_ignored(rig):
    await rig.started()
    rig.av.play_started()
    rig.av.muted()
    rig.av.audio(LOUD, 16000)  # playback thread raced the mute
    await ticks(10)
    assert not any(i.get("MouthOpen", 0) > 0 for i in rig.t.injected())


async def test_audio_without_play_started_is_ignored(rig):
    await rig.started()
    rig.av.audio(LOUD, 16000)
    await ticks(10)
    assert rig.t.injected() == []


async def test_play_ended_after_mute_reenables_nothing(rig):
    await rig.started()
    rig.av.thinking()
    rig.av.react("happy", None)
    rig.av.play_started()
    rig.av.audio(LOUD, 16000)
    await until(lambda: any(i.get("MouthOpen", 0) > 0 for i in rig.t.injected()))
    rig.av.muted()
    await until(lambda: rig.t.injected()[-1].get("MouthOpen") == 0)
    n_inject, n_expr = len(rig.t.injected()), len(rig.t.of("ExpressionActivation"))
    rig.av.play_ended()
    await ticks(10)
    assert rig.t.injected()[n_inject:] == []
    assert rig.t.of("ExpressionActivation")[n_expr:] == []


async def test_audio_from_other_thread(rig):
    await rig.started()
    rig.av.play_started()
    errors = []

    def run():
        try:
            rig.av.audio(LOUD, 16000)
        except Exception as e:  # pragma: no cover - the assertion reports it
            errors.append(e)

    th = threading.Thread(target=run)
    th.start()
    th.join()
    await until(lambda: any(i.get("MouthOpen", 0) > 0 for i in rig.t.injected()))
    assert errors == []


async def test_disconnect_mid_reply_then_reconnect(rig):
    await rig.started()
    rig.av.thinking()
    rig.av.react("happy", None)
    rig.av.play_started()
    rig.av.audio(LOUD, 16000)
    await until(lambda: any(i.get("MouthOpen", 0) > 0 for i in rig.t.injected()))
    first = rig.t
    rig.always_fail = True
    first.drop()
    await until(lambda: "not reachable" in " ".join(rig.lines))
    assert rig.av.status() == "avatar: not connected (ws://test:1)"
    rig.av.react("annoyed", "celebrate")
    rig.av.audio(LOUD, 16000)
    rig.av.play_ended()
    rig.av.reply_done()
    rig.av.muted()
    rig.av.thinking()
    await ticks()
    rig.always_fail = False
    await until(lambda: rig.lines[-1] == "avatar: connected")
    assert rig.lines == ["avatar: connected",
                         "avatar: VTube Studio not reachable, retrying in the background",
                         "avatar: connected"]
    assert rig.t is not first
    await until(lambda: any(i.get("FaceAngleZ") == 12 for i in rig.t.injected()))  # recorded state resumes
    assert rig.t.of("ExpressionActivation") == [] and rig.t.of("HotkeyTrigger") == []


async def test_reconnect_turns_off_mapped_expression_left_active(tmp_path):
    r = Rig(tmp_path)

    class Sticky(FakeVTS):
        def reply_for(self, kind, data):
            if kind == "ExpressionState":
                return {"expressions": [{"name": f, "file": f, "active": f == "EyesCry.exp3.json"}
                                        for f in self.expressions]}
            return super().reply_for(kind, data)

    async def connect(url):
        t = Sticky()
        r.transports.append(t)
        return t

    r.av._connect = connect
    try:
        await r.started()
        await until(lambda: r.t.of("ExpressionActivation"))
        assert r.t.of("ExpressionActivation") == [{"expressionFile": "EyesCry.exp3.json", "active": False}]
    finally:
        await r.av.close()


async def test_close_resets_and_closes(rig):
    await rig.started()
    rig.av.react("happy", None)
    await until(lambda: rig.t.of("ExpressionActivation"))
    await rig.av.close()
    assert rig.t.of("ExpressionActivation")[-1] == {"expressionFile": "EyesLove.exp3.json", "active": False}
    assert rig.t.injected()[-1] == {"MouthOpen": 0}
    assert rig.t.closed
    await rig.av.close()


async def test_close_is_safe_when_never_connected(tmp_path):
    r = Rig(tmp_path, always_fail=True)
    await r.av.start()
    await until(lambda: r.lines)
    await r.av.close()
    await r.av.close()
    assert r.lines == ["avatar: VTube Studio not reachable, retrying in the background"]


async def test_close_without_start(tmp_path):
    r = Rig(tmp_path)
    await r.av.close()
    r.av.thinking()
    r.av.audio(LOUD, 16000)
    r.av.react("happy", "celebrate")
    r.av.reply_done()


async def test_unexpected_connect_error_keeps_retrying(tmp_path):
    r = Rig(tmp_path)
    calls = []

    async def connect(url):
        calls.append(url)
        if len(calls) < 3:
            raise RuntimeError("weird")
        t = FakeVTS()
        r.transports.append(t)
        return t

    r.av._connect = connect
    try:
        await r.started()
        assert len(calls) == 3
    finally:
        await r.av.close()


async def test_self_test_not_connected(tmp_path):
    r = Rig(tmp_path)
    got = []
    await r.av.self_test(got.append)
    assert got == ["avatar: not connected"]


async def test_self_test_runs_mappings(rig, monkeypatch):
    monkeypatch.setattr("app.avatar.controller.SELF_TEST_HOLD_S", 0.01)
    await rig.started()
    got = []
    await rig.av.self_test(got.append)
    for emotion in ("happy", "sulking", "annoyed", "shocked"):
        assert any(emotion in line for line in got)
    assert [d["expressionFile"] for d in rig.t.of("ExpressionActivation") if d["active"]] == EXPRESSIONS
    assert rig.t.of("HotkeyTrigger") == [{"hotkeyID": "Anim Shake"}, {"hotkeyID": FACEPALM}]
    assert any(i.get("FaceAngleZ") == 12 for i in rig.t.injected())
    assert rig.t.of("ExpressionActivation")[-1]["active"] is False
    n = len(rig.t.injected())
    await ticks()
    assert [i for i in rig.t.injected()[n:] if "FaceAngleZ" in i] == []


async def test_null_avatar_is_inert():
    from app.avatar.base import NullAvatar
    a = NullAvatar()
    a.thinking()
    a.play_started()
    a.audio(LOUD, 16000)
    a.react("happy", "celebrate")
    a.play_ended()
    a.muted()
    a.reply_done()
    a.retry()
    await a.start()
    got = []
    await a.self_test(got.append)
    await a.close()
    assert a.status() == "avatar off"


def test_settings_avatar_section_loaded():
    s = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    a = s.avatar
    assert a.enabled is True and a.url == "ws://localhost:8001"
    assert a.expressions["happy"] == "EyesLove.exp3.json"
    assert a.actions == {"celebrate": "Anim Shake", "facepalm": FACEPALM}
    assert a.thinking == {"FaceAngleZ": 12.0, "FaceAngleY": 8.0, "EyeRightY": 0.8}
    assert a.token_file == Path("config/.vts_token")
    assert a.fps == 30 and a.reconnect_s == 5 and a.mouth_gain == 4.0 and a.mouth_smoothing == 0.4
    assert a.plugin_name == "MyLiveAgent" and a.plugin_developer == "Lok"


def test_settings_without_avatar_section_disabled(tmp_path):
    text = Path("config/settings.yaml").read_text(encoding="utf-8")
    text = text[: text.index("\navatar:")] + "\n" if "\navatar:" in text else text
    p = tmp_path / "s.yaml"
    p.write_text(text, encoding="utf-8")
    s = load_settings(p, {"DEEPSEEK_API_KEY": "k"})
    assert s.avatar.enabled is False
    assert s.avatar.expressions == {} and s.avatar.actions == {} and s.avatar.thinking == {}
    assert s.avatar.url == "ws://localhost:8001" and s.avatar.fps == 30


def test_settings_bad_avatar_value(tmp_path):
    from app.config import ConfigError
    text = Path("config/settings.yaml").read_text(encoding="utf-8")
    p = tmp_path / "s.yaml"
    p.write_text(text.replace("fps: 30", "fps: lots"), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(p, {"DEEPSEEK_API_KEY": "k"})


class DenyingVTS(FakeVTS):
    """The saved token is rejected and the owner clicks Deny on the popup (or ignores it)."""

    def __init__(self, deny=True, **kw):
        super().__init__(**kw)
        self.deny = deny

    def reply_for(self, kind, data):
        if kind == "Authentication":
            return {"authenticated": data.get("authenticationToken") == "fresh"}  # saved "tok" is stale
        if kind == "AuthenticationToken":
            return {"authenticationToken": "fresh"}
        return super().reply_for(kind, data)

    async def send(self, msg):
        if self.deny and msg["messageType"] == "AuthenticationTokenRequest":
            self.sent.append(msg)
            self.inbox.put_nowait({"messageType": "APIError", "requestID": msg["requestID"],
                                   "data": {"errorID": 50, "message": "User has denied API access for your plugin."}})
            return
        await super().send(msg)


def _deny_rig(tmp_path):
    r = Rig(tmp_path)
    r.deny = True

    async def connect(url):
        t = DenyingVTS(deny=r.deny)
        r.transports.append(t)
        return t

    r.av._connect = connect
    return r


def _token_requests(r):
    return sum(len(t.of("AuthenticationToken")) for t in r.transports)


DENIED_LINE = "avatar: plugin not allowed in VTube Studio; type /avatar to ask again"
ALLOW_NOTICE = "VTube Studio: click Allow in the popup to let this plugin connect."


async def test_denied_parks_without_reasking(tmp_path):
    r = _deny_rig(tmp_path)
    try:
        await r.av.start()
        await until(lambda: DENIED_LINE in r.lines)
        await asyncio.sleep(r.settings.reconnect_s * 10)
        assert _token_requests(r) == 1
        assert r.lines == [ALLOW_NOTICE, DENIED_LINE]
        assert r.av.status() == "avatar: not allowed in VTube Studio (ws://test:1)"
    finally:
        await r.av.close()


async def test_unanswered_popup_parks(tmp_path, monkeypatch):
    monkeypatch.setattr("app.avatar.vts.TOKEN_WAIT", 0.02)
    r = _deny_rig(tmp_path)

    class Silent(DenyingVTS):
        async def send(self, msg):
            if msg["messageType"] == "AuthenticationTokenRequest":
                self.sent.append(msg)  # popup shown, nobody clicks
                return
            await super().send(msg)

    async def connect(url):
        t = Silent()
        r.transports.append(t)
        return t

    r.av._connect = connect
    try:
        await r.av.start()
        await until(lambda: DENIED_LINE in r.lines)
        await asyncio.sleep(r.settings.reconnect_s * 10)
        assert _token_requests(r) == 1 and r.lines.count(DENIED_LINE) == 1
    finally:
        await r.av.close()


async def test_retry_rearms_after_deny(tmp_path):
    r = _deny_rig(tmp_path)
    try:
        await r.av.start()
        await until(lambda: DENIED_LINE in r.lines)
        r.av.retry()
        await until(lambda: _token_requests(r) == 2)
        await until(lambda: r.lines.count(DENIED_LINE) == 2)
        assert r.lines.count(ALLOW_NOTICE) == 2
        r.deny = False  # the owner clicks Allow this time
        r.av.retry()
        await until(lambda: r.lines[-1] == "avatar: connected")
        assert r.av.status() == "avatar: connected (ws://test:1)"
        assert (tmp_path / ".vts_token").read_text(encoding="utf-8") == "fresh"
    finally:
        await r.av.close()


async def test_retry_is_noop_unless_parked(rig):
    rig.av.retry()  # before start
    await rig.started()
    rig.av.retry()
    await ticks()
    assert len(rig.transports) == 1 and rig.lines == ["avatar: connected"]


async def test_allow_notice_printed_once_per_arm(tmp_path):
    r = _deny_rig(tmp_path)

    class DropsDuringPopup(DenyingVTS):
        async def send(self, msg):
            if msg["messageType"] == "AuthenticationTokenRequest":
                self.sent.append(msg)
                self.drop()  # VTube Studio closed while the popup was up
                return
            await super().send(msg)

    async def connect(url):
        t = DropsDuringPopup()
        r.transports.append(t)
        return t

    r.av._connect = connect
    try:
        await r.av.start()
        await until(lambda: _token_requests(r) >= 3)
        assert r.lines.count(ALLOW_NOTICE) == 1
    finally:
        await r.av.close()


async def test_failed_relearn_keeps_mappings(rig, monkeypatch):
    monkeypatch.setattr("app.avatar.controller.SELF_TEST_HOLD_S", 0.01)
    await rig.started()
    orig = rig.t.send

    async def send(msg):  # the model-info requests now fail with an API error
        kind = msg["messageType"].removesuffix("Request")
        if kind in ("HotkeysInCurrentModel", "ExpressionState"):
            rig.t.sent.append(msg)
            rig.t.inbox.put_nowait({"messageType": "APIError", "requestID": msg["requestID"],
                                    "data": {"errorID": 1, "message": "busy"}})
            return
        await orig(msg)

    rig.t.send = send
    await rig.av.self_test(lambda line: None)
    n_expr, n_hot = len(rig.t.of("ExpressionActivation")), len(rig.t.of("HotkeyTrigger"))
    rig.av.react("annoyed", "facepalm")
    await until(lambda: len(rig.t.of("HotkeyTrigger")) > n_hot)
    assert rig.t.of("ExpressionActivation")[n_expr:] == [{"expressionFile": "SignAngry.exp3.json", "active": True}]
    assert rig.t.of("HotkeyTrigger")[n_hot:] == [{"hotkeyID": FACEPALM}]
    assert not any("not in the loaded model" in line or "has no" in line for line in rig.lines)


async def test_missing_mapping_printed_once_and_reset_by_relearn(tmp_path, monkeypatch):
    monkeypatch.setattr("app.avatar.controller.SELF_TEST_HOLD_S", 0.01)
    r = Rig(tmp_path, expressions=[e for e in EXPRESSIONS if e != "SignAngry.exp3.json"])
    r.settings.actions["wave"] = "No Such Hotkey"
    expr_line = ("avatar: model has no expression 'SignAngry.exp3.json' (emotion annoyed); "
                 "check config or type /avatar after switching models")
    hot_line = ("avatar: model has no hotkey 'No Such Hotkey' (action wave); "
                "check config or type /avatar after switching models")
    try:
        await r.started()
        r.av.react("annoyed", "wave")
        r.av.react("annoyed", "wave")
        assert r.lines.count(expr_line) == 1 and r.lines.count(hot_line) == 1
        await r.av.self_test(lambda line: None)  # successful re-learn clears the warned set
        r.av.react("annoyed", None)
        assert r.lines.count(expr_line) == 2
    finally:
        await r.av.close()
