# M2 Avatar (VTube Studio) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive a VTube Studio model (Akari for now) from the M1 app: mouth follows the spoken audio, the face follows the reply's emotion, actions trigger hotkeys, a thinking pose shows while waiting, and nothing about the avatar can stop or delay speech.

**Architecture:** New package `app/avatar/`: `vts.py` (WebSocket protocol client: auth, token file, request/response matching), `lipsync.py` (PCM → mouth-open envelope), `controller.py` (`VTSAvatar`: owns connection with background reconnect, expression/action/pose/mouth state, a fixed-rate injection loop), `base.py` (`Avatar` protocol + `NullAvatar`). The audio layer gets two small hooks (sink hands decoded samples out; player reports play start/end/mute). The pipeline calls the avatar at reply start, after the brain returns, and at reply end. All avatar methods called from the speech path are synchronous and non-blocking (they only record state or enqueue).

**Tech Stack:** Python 3.13 asyncio, `aiohttp` WebSocket client (already installed as an edge-tts dependency; add it to `requirements.txt` explicitly), numpy, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-31-m2-avatar-vts-design.md` (read it, including "Decisions from the builder check").

## Global Constraints

- Avatar failure (VTube Studio not running, closed, socket error, API error, any exception) never stops, delays or silences speech; the main loop never sees an avatar exception.
- No model-specific names (expression files, hotkey names/IDs, parameter IDs) in Python; they live in `config/settings.yaml` under `avatar:`.
- `config/persona.md` is never edited; the LLM JSON order (`say` before `emotion`) is unchanged.
- Token file `config/.vts_token` is git-ignored (already in `.gitignore`).
- Tests never open a real socket or a real audio device. Existing 154 tests keep passing; fakes may be updated, assertions not weakened.
- Mute → mouth 0 immediately, expression off, thinking off, no further mouth values until next playback.
- Only one mapped expression active at a time; it is deactivated explicitly (`active: false`) at reply end or before another is activated. Do not use Akari's "Remove Expressions" hotkey.
- If a sentence was replaced by safety, or the reply fell back to a brain-lag line, or the brain returned an error: no expression and no action for that reply.
- VTube Studio API message shapes below are the designer's understanding; verify against https://github.com/DenchiSoft/VTubeStudio (README) and follow the docs where they differ, noting it in the report.

### VTube Studio API reference (verify)

Envelope, every request: `{"apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0", "requestID": "<unique>", "messageType": "<Type>Request", "data": {...}}`. Responses echo `requestID`; errors come back as `messageType: "APIError"` with `data.errorID`, `data.message`.

| Request | data sent | data received |
|---|---|---|
| `AuthenticationTokenRequest` | `pluginName`, `pluginDeveloper` | `authenticationToken` (VTS shows the owner an Allow/Deny popup first; the response waits for the click) |
| `AuthenticationRequest` | `pluginName`, `pluginDeveloper`, `authenticationToken` | `authenticated` (bool), `reason` |
| `HotkeysInCurrentModelRequest` | — | `availableHotkeys`: list of `{name, hotkeyID, file, type}` |
| `ExpressionStateRequest` | `details: false` | `expressions`: list of `{file, active, ...}` |
| `ExpressionActivationRequest` | `expressionFile`, `active` | — |
| `HotkeyTriggerRequest` | `hotkeyID` (ID or name) | — |
| `InjectParameterDataRequest` | `faceFound: false`, `mode: "set"`, `parameterValues: [{id, value}]` | — |

An injected parameter stays under plugin control only while re-sent at least about once per second.

## Review Focus

1. VTube Studio closed in the middle of a reply → the reply finishes speaking and logs normally; the avatar reports disconnected and reconnects when VTS comes back (Task 3 test `test_disconnect_mid_reply_then_reconnect`, Task 4 test `test_pipeline_speaks_when_avatar_raises`).
2. Mute while the mouth is moving → a final `MouthOpen` 0 is sent and no further mouth values follow (Task 3 test `test_muted_closes_mouth_and_stops_values`).
3. A reply that raises or errors after thinking started → expression and thinking pose are still reset (Task 4 test `test_reply_done_called_even_when_handle_fails`).
4. Saved token rejected (owner reinstalled VTS or revoked the plugin) → a new token is requested and saved, not a crash loop (Task 1 test `test_rejected_token_requests_new_one`).
5. Config maps an expression or hotkey the loaded model doesn't have (owner swaps models) → logged once, skipped, the other mappings still work (Task 3 test `test_missing_mapping_logged_once_and_skipped`).

---

### Task 1: VTube Studio protocol client

**Files:**
- Create: `app/avatar/__init__.py` (empty), `app/avatar/vts.py`
- Modify: `requirements.txt` (add `aiohttp`)
- Test: `tests/test_vts.py`

**Interfaces:**
- Produces:
  - `class VTSError(Exception)` — API error or protocol failure; message includes `errorID` and `message` when present.
  - `class Transport(Protocol)`: `async def send(self, msg: dict) -> None`, `async def receive(self) -> dict` (raises `ConnectionError` when closed), `async def close(self) -> None`.
  - `async def aiohttp_connect(url: str) -> Transport` — real transport over `aiohttp.ClientSession().ws_connect`; closes its session on `close()`.
  - `class VTSClient`: `__init__(self, transport: Transport, plugin_name: str, plugin_developer: str, token_path: Path, on_notice: Callable[[str], None])`; `async def authenticate(self) -> None`; `async def request(self, message_type: str, data: dict | None = None, timeout: float = 5.0) -> dict` (returns response `data`, raises `VTSError` on `APIError` or timeout); `async def close(self) -> None`. `message_type` is passed without the `Request` suffix (e.g. `"HotkeyTrigger"`).
- A background reader task inside `VTSClient` matches responses to pending requests by `requestID`; when the transport raises `ConnectionError`, all pending requests fail with `VTSError("disconnected")` and later `request` calls raise immediately.

- [ ] **Step 1: Write failing tests** in `tests/test_vts.py` with a `FakeTransport` (an asyncio queue of scripted replies keyed by message type; records sent messages).

```python
async def test_request_envelope_and_response_data(tmp_path): ...
    # request("HotkeyTrigger", {"hotkeyID": "x"}) sends apiName "VTubeStudioPublicAPI", apiVersion "1.0",
    # messageType "HotkeyTriggerRequest", a requestID, data {"hotkeyID": "x"}; returns the reply's data dict
async def test_api_error_raises_vtserror(tmp_path): ...
    # reply {"messageType": "APIError", "data": {"errorID": 50, "message": "nope"}} -> VTSError containing "50" and "nope"
async def test_first_run_requests_token_saves_it_and_tells_owner(tmp_path): ...
    # no token file -> AuthenticationTokenRequest then AuthenticationRequest with that token;
    # token file now holds the token; on_notice got one line containing "Allow" (click Allow in VTube Studio)
async def test_saved_token_reused(tmp_path): ...
    # token file "abc" -> only AuthenticationRequest with "abc" is sent
async def test_rejected_token_requests_new_one(tmp_path): ...
    # saved "old" -> authenticated False -> AuthenticationTokenRequest -> AuthenticationRequest with new token -> file updated
async def test_denied_popup_raises(tmp_path): ...
    # AuthenticationTokenRequest answered with APIError -> authenticate() raises VTSError; token file not created
async def test_disconnect_fails_pending_and_later_requests(tmp_path): ...
    # transport.receive raises ConnectionError while a request is pending -> that request raises VTSError("disconnected"); next request raises too
async def test_timeout_raises(tmp_path): ...
    # no reply, timeout=0.05 -> VTSError
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_vts.py -v` — expect import errors (module missing).
- [ ] **Step 3: Implement `app/avatar/vts.py`** per Interfaces. Token file read/write as UTF-8 text, parent dir created if missing.
- [ ] **Step 4: Run** `tests/test_vts.py` — all pass; full suite `.venv/Scripts/python -m pytest -q` — all pass.
- [ ] **Step 5: Commit** `feat(avatar): VTube Studio protocol client`

---

### Task 2: Audio hooks and lip-sync envelope

**Files:**
- Create: `app/avatar/lipsync.py`
- Modify: `app/audio.py` (`SoundDeviceSink`, `Player`)
- Test: `tests/test_lipsync.py`, `tests/test_player.py`

**Interfaces:**
- Produces:
  - `def envelope(samples: np.ndarray, sample_rate: int, fps: float, gain: float, smoothing: float) -> list[float]` in `app/avatar/lipsync.py`. `samples` is int16, shape `(n,)` or `(n, channels)`. One value per frame of `1/fps` seconds (`ceil(n / (sample_rate / fps))` values), each = RMS of the frame normalized by 32768, times `gain`, clipped to `[0, 1]`, then exponential smoothing `v = smoothing * prev + (1 - smoothing) * raw` (prev starts at 0). `smoothing=0` means none.
  - `SoundDeviceSink.__init__(self, on_audio: Callable[[np.ndarray, int], None] | None = None)` — called on the playback thread right after `sd.play(...)` with `(samples, sample_rate)`; exceptions from it are caught and logged, never affect playback.
  - `class PlaybackListener(Protocol)` in `app/audio.py`: `play_started() -> None`, `play_ended() -> None`, `muted() -> None`.
  - `Player.__init__(self, sink, clock=time.perf_counter, listener: PlaybackListener | None = None)`. `listener.play_started()` right before each `sink.play`, `listener.play_ended()` after each `sink.play` returns or raises, `listener.muted()` in `set_muted(True)`. Listener exceptions are caught and logged, never affect playback.

- [ ] **Step 1: Write failing tests.**

```python
# tests/test_lipsync.py
def test_silence_is_zero():          # zeros, 16000 Hz, fps 30 -> all 0.0, len == ceil(16000 / (16000/30))
def test_loud_is_near_one():         # full-scale square wave, gain 1, smoothing 0 -> every value >= 0.95
def test_length_matches_duration():  # 1.5 s at 24000 Hz, fps 30 -> 45 values
def test_stereo_accepted():          # shape (n, 2) gives same length as mono of n
def test_smoothing_rises_gradually():# silence then loud, smoothing 0.5 -> first loud frame < second loud frame
def test_clipped_to_one():           # gain 100 on moderate signal -> max == 1.0

# tests/test_player.py (add)
async def test_listener_sees_start_end_per_chunk():   # two chunks -> started, ended, started, ended
async def test_listener_muted_on_mute():              # set_muted(True) -> muted() called once
async def test_listener_errors_do_not_stop_playback():# listener raising in every method -> both chunks still played
```

- [ ] **Step 2: Run** both test files — new tests fail.
- [ ] **Step 3: Implement** `envelope` and the two hooks per Interfaces.
- [ ] **Step 4: Run** the two files, then the full suite — all pass.
- [ ] **Step 5: Commit** `feat(avatar): lip-sync envelope and playback hooks`

---

### Task 3: Avatar controller

**Files:**
- Create: `app/avatar/base.py`, `app/avatar/controller.py`
- Modify: `app/config.py` (add `AvatarSettings`, optional `Settings.avatar`), `config/settings.yaml` (add `avatar:` section)
- Test: `tests/test_avatar.py`

**Interfaces:**
- Consumes: `VTSClient`, `VTSError`, `Transport`, `aiohttp_connect` (Task 1); `envelope` (Task 2); `PlaybackListener` method names (Task 2).
- Produces:
  - `AvatarSettings` dataclass in `app/config.py`: `enabled: bool`, `url: str`, `plugin_name: str`, `plugin_developer: str`, `token_file: Path`, `reconnect_s: float`, `fps: float`, `mouth_gain: float`, `mouth_smoothing: float`, `expressions: dict[str, str]`, `actions: dict[str, str]`, `thinking: dict[str, float]`. `Settings.avatar: AvatarSettings` loaded from `raw.get("avatar")`; a missing section gives `enabled=False` with empty maps and the defaults below.
  - `class Avatar(Protocol)` in `app/avatar/base.py` — every method except the `async` ones is synchronous, non-blocking and never raises:
    `thinking()`, `play_started()`, `play_ended()`, `muted()`, `audio(samples: np.ndarray, sample_rate: int)` (thread-safe: may be called from the playback thread), `react(emotion: str, action: str | None)`, `reply_done()`, `status() -> str`, `async start()`, `async close()`, `async self_test(out: Callable[[str], None])`.
  - `class NullAvatar` in `base.py`: all no-ops; `status()` returns `"avatar off"`.
  - `class VTSAvatar` in `app/avatar/controller.py`: `__init__(self, settings: AvatarSettings, out: Callable[[str], None], connect: Callable[[str], Awaitable[Transport]] = aiohttp_connect, clock: Callable[[], float] = time.monotonic)`.
- Behaviour (the tests pin it):
  - `start()` launches a background connection task and returns at once. It connects, authenticates, fetches `HotkeysInCurrentModel` and `ExpressionState` to learn what the model has, then runs. On any failure: print one line via `out` on the first failure and on each change of state (`avatar: connected` / `avatar: VTube Studio not reachable, retrying in the background`), wait `reconnect_s`, retry. Never prints per retry.
  - `thinking()` → thinking pose on. `play_started()` → thinking pose off. `audio(...)` → compute envelope, start mouth track at `clock()`. `play_ended()` → mouth track cleared, one `MouthOpen` 0. `react(emotion, action)` → deactivate current expression if any, activate `expressions[emotion]` if mapped and present; trigger `actions[action]` once if mapped and present. `reply_done()` → deactivate current expression, thinking off, mouth 0. `muted()` → same as `reply_done()`.
  - Injection loop at `fps` while connected: one `InjectParameterData` per tick containing `MouthOpen` (current envelope value by elapsed time, if a track is active) and the thinking parameters (if thinking is on). Nothing is injected when neither is active, so VTS's idle animation takes over.
  - Mapping entries absent from the model: logged once (via `logging`) and skipped.
  - While disconnected: state changes are recorded but no requests are sent; on reconnect, an active expression is not re-applied (the reply will have moved on) — just reset.
  - `close()` resets (expression off, mouth 0) if connected, cancels tasks, closes the client. Safe to call twice and when never connected.
  - `self_test(out)`: if not connected, prints `avatar: not connected` and returns; else thinking pose 1 s, each mapped expression 1 s (printing its emotion name), each mapped action, then reset.
  - `status()`: `"avatar: connected (<url>)"` or `"avatar: not connected (<url>)"`.

- `config/settings.yaml` gains (values are the spec's mappings; numbers are the designer's starting guesses, owner tunes):

```yaml
avatar:
  enabled: true
  url: ws://localhost:8001
  plugin_name: MyLiveAgent
  plugin_developer: Lok
  token_file: config/.vts_token
  reconnect_s: 5
  fps: 30
  mouth_gain: 4.0
  mouth_smoothing: 0.4
  expressions:          # emotion -> expression file in the loaded model
    happy: EyesLove.exp3.json
    sulking: EyesCry.exp3.json
    annoyed: SignAngry.exp3.json
    shocked: SignShock.exp3.json
  actions:              # action -> hotkey name or ID
    celebrate: Anim Shake
    facepalm: 5fbc7fac3d3649c6a8901600261a6983
  thinking:             # injected parameter -> value while waiting
    FaceAngleZ: 12
    FaceAngleY: 8
    EyeRightY: 0.8
```

- [ ] **Step 1: Write failing tests** in `tests/test_avatar.py` using a fake transport that answers auth, hotkey list (`Anim Shake`, the facepalm ID), expression state (the four files) and records every request; fast `fps` (e.g. 200) and a controllable clock.

```python
async def test_connect_and_status(): ...                 # start(); eventually status() says "connected"; out got "avatar: connected" once
async def test_unreachable_prints_once_and_retries(): ...# connect raises OSError 3 times then works -> one "not reachable" line, then "connected"
async def test_reply_sequence(): ...
    # thinking() -> InjectParameterData with FaceAngleZ 12; play_started() -> later injections lack FaceAngleZ;
    # react("happy", "celebrate") -> ExpressionActivation EyesLove.exp3.json active True, HotkeyTrigger "Anim Shake";
    # reply_done() -> ExpressionActivation EyesLove.exp3.json active False
async def test_switching_expression_deactivates_previous(): ...  # react happy then react annoyed -> EyesLove off before SignAngry on
async def test_unmapped_emotion_sends_nothing(): ...     # react("smug", None) -> no ExpressionActivation, no HotkeyTrigger
async def test_missing_mapping_logged_once_and_skipped(caplog): ...
    # model lacks SignAngry.exp3.json -> react("annoyed") twice: no request, one log record; react("happy") still works
async def test_mouth_follows_audio_then_closes(): ...    # audio(loud samples) -> MouthOpen values > 0 injected; play_ended() -> MouthOpen 0, then no MouthOpen
async def test_muted_closes_mouth_and_stops_values(): ...# during a track, muted() -> MouthOpen 0, expression off, no further MouthOpen
async def test_audio_from_other_thread(): ...            # audio() called via threading.Thread -> mouth values injected, no exception
async def test_disconnect_mid_reply_then_reconnect(): ...
    # transport closes during a track -> methods keep working without raising; out gets "not reachable"; reconnect -> "connected"
async def test_close_is_safe_when_never_connected(): ... # connect always fails; close() twice -> no exception
async def test_null_avatar_is_inert(): ...               # every NullAvatar method callable; status() == "avatar off"
def test_settings_avatar_section_loaded(): ...           # load_settings(config/settings.yaml) -> avatar.expressions["happy"] == "EyesLove.exp3.json"
def test_settings_without_avatar_section_disabled(tmp_path): ...  # yaml without avatar: -> settings.avatar.enabled is False
```

- [ ] **Step 2: Run** `tests/test_avatar.py` — fail.
- [ ] **Step 3: Implement** `base.py`, `controller.py`, `AvatarSettings` and the yaml section.
- [ ] **Step 4: Run** the file, then the full suite — all pass.
- [ ] **Step 5: Commit** `feat(avatar): VTube Studio avatar controller`

---

### Task 4: Wire the avatar into the app

**Files:**
- Modify: `app/pipeline.py`, `app/main.py`, `app/ingest/console.py` (`COMMANDS` += `"avatar"`), `tests/conftest.py`, `CLAUDE.md` (Project state: M2 avatar drives VTube Studio; Commands: `/avatar`; VTube Studio's "Start API (allow plugins)" must be on, port 8001; token in git-ignored `config/.vts_token`). Keep CLAUDE.md's short, factual style.
- Test: `tests/test_pipeline.py`, `tests/test_mute.py` (or wherever `run()` tests live)

**Interfaces:**
- Consumes: `Avatar`, `NullAvatar`, `VTSAvatar` (Task 3); `PlaybackListener`, `SoundDeviceSink(on_audio=...)`, `Player(listener=...)` (Task 2).
- Produces:
  - `Pipeline.__init__(..., avatar: Avatar | None = None)` (default `NullAvatar()`), keyword argument after `clock`.
  - `def make_avatar(settings: Settings, out) -> Avatar` in `app/main.py`: `VTSAvatar(settings.avatar, out)` if `settings.avatar.enabled` else `NullAvatar()`.
- Pipeline behaviour:
  - `avatar.thinking()` immediately before `brain.reply(...)` (after the budget checks, so skipped replies don't pose).
  - After `brain.reply` returns: `avatar.react(result.emotion, result.action)` only if `result.error is None`, `rec.safety` is empty and at least one model sentence was spoken (no brain-lag fallback). Otherwise no `react` call.
  - `avatar.reply_done()` in a `finally` covering everything from `thinking()` to the end of `handle`.
- `main._run` behaviour:
  - `avatar = make_avatar(settings, print)`; `SoundDeviceSink(on_audio=avatar.audio)` when no sink is injected; `Player(sink, listener=avatar)`; `Pipeline(..., avatar=avatar)`; `await avatar.start()` before the worker starts. No extra banner line: the controller's own "connected" / "not reachable" line is enough (designer's call).
  - `/avatar` command → `print(avatar.status())` then run `avatar.self_test(print)` as a task (don't block input).
  - `HELP` gains `  /avatar           avatar status and a quick expression test`.
  - Shutdown: `await avatar.close()` before `player.close()`, inside its own try/except so a failure can't skip the rest.
- `tests/conftest.py`: autouse fixture that monkeypatches `app.main.make_avatar` to return `NullAvatar()`, so no test ever connects to a VTube Studio that happens to be running on the dev machine.

- [ ] **Step 1: Write failing tests.**

```python
# tests/test_pipeline.py (add) — a RecordingAvatar fake that appends method names
async def test_avatar_sequence_happy_path(settings): ...
    # result emotion "happy", action "celebrate" -> calls in order: thinking, react("happy","celebrate"), reply_done
async def test_no_react_when_sentence_blocked(settings): ...     # a sentence fails output safety -> no react; reply_done still called
async def test_no_react_on_brain_lag(settings): ...              # brain yields no sentences -> no react
async def test_no_react_on_brain_error(settings): ...            # result.error set -> no react
async def test_no_pose_when_skipped_over_budget(settings): ...   # over cap, comment event -> no thinking, no reply_done
async def test_reply_done_called_even_when_handle_fails(settings): ...  # metrics.add raises a non-OSError -> handle raises, reply_done was called
async def test_pipeline_speaks_when_avatar_raises(settings): ... # avatar whose every method raises -> handle still speaks and returns a record
                                                                  # (pipeline wraps avatar calls; a raising avatar must not break a reply)

# run() tests (add)
async def test_avatar_command_prints_status(...): ...   # make_avatar patched to a fake; "/avatar" -> its status() line printed, self_test awaited
async def test_avatar_closed_on_quit(...): ...          # fake avatar's close() called once on /quit
```

- [ ] **Step 2: Run** those tests — fail.
- [ ] **Step 3: Implement** per Interfaces. Wrap each pipeline avatar call so an exception is logged and ignored (the protocol says they don't raise, but the pipeline must not trust that).
- [ ] **Step 4: Run** the full suite `.venv/Scripts/python -m pytest -q` — all pass. Update `CLAUDE.md`.
- [ ] **Step 5: Commit** `feat(avatar): drive VTube Studio from the reply pipeline`

---

Manual acceptance (owner, after the branch is done): spec's "Manual" list — with Akari loaded run `.venv/Scripts/python -m app`, click Allow on the first-run popup, then check mouth, four expressions, celebrate/facepalm, thinking pose, mute, closing and reopening VTube Studio mid-session, and `/avatar`.
