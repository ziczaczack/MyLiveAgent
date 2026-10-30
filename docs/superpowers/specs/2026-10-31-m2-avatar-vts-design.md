# M2 design: the chibi moves (VTube Studio avatar)

Date: 2026-10-31
Sources: `AI Chibi 直播搭档 — 架构与方案提案.docx` (avatar module: VTube Studio WebSocket plugin API), the M1 design (`2026-10-29-m1-talking-chibi-design.md`), conversation with the owner.

## Intent

M1 speaks. M2 makes the character visibly react while it speaks, so the stream feels interactive: viewers do something, the character answers with a face and mouth that match. The owner chose "the character reacts when it replies"; viewers directly commanding the avatar (gift → fixed move, chat commands) is out of scope.

The owner has no original model yet and is looking for a free or bought Live2D model. Development uses **Akari**, the free model bundled with VTube Studio. Everything model-specific lives in config so swapping models is a config edit, not code.

## Goals

1. **Lip sync (owner chose option A):** our program drives the mouth from the audio it is playing, sent to VTube Studio as a parameter. No virtual audio cable, no extra software.
2. **Emotion → expression:** each reply's `emotion` switches the face for the duration of the reply, then returns to neutral.
3. **Action → animation:** `celebrate` and `facepalm` trigger animations.
4. **Thinking pose** while waiting for the reply to start playing.
5. **Never break the stream:** VTube Studio missing, closed, or disconnecting never stops speech; the avatar just stops moving and the program reconnects in the background.

## Non-goals

- Viewers controlling the avatar directly (gift-specific moves, chat commands). Owner said no for now.
- OBS scene control, TikTok ingest (later milestones).
- Webcam/face tracking. The character is AI-driven; idle motion comes from the model's own idle animation in VTube Studio.
- Building custom expressions for emotions the model lacks.

## Anti-goals

- Mouth moving while muted, or mouth stuck open after audio stops.
- An avatar failure delaying or silencing speech, or adding latency to the comment-to-speech path (budget stays under 3 s).
- An expression left on after the reply ends (e.g. stuck crying face).
- Model-specific names (hotkey IDs, file names) hard-coded in Python.

## Environment

- VTube Studio (Steam) installed at `D:\SteamLibrary\steamapps\common\VTube Studio`. Owner has enabled "Start API (allow plugins)" on the default port 8001, with Akari loaded.
- Akari (`akari_vts/`) provides, verified from its `akari.vtube.json`:
  - Expressions via files: `EyesLove.exp3.json`, `EyesCry.exp3.json`, `SignAngry.exp3.json`, `SignShock.exp3.json`.
  - Hotkeys: `Anim Shake` → `Love.motion3.json`; an unnamed hotkey → `Shock.motion3.json` (ID `5fbc7fac3d3649c6a8901600261a6983`); `Remove Expressions`.
  - Input parameters it reacts to: `MouthOpen`, `MouthSmile`, `MouthX`, `FaceAngleX/Y/Z`, `EyeOpenLeft/Right`, `EyeRightX/Y`, `Brows`, `TongueOut`.
- VTube Studio public API facts the builder should verify against the official docs (github.com/DenchiSoft/VTubeStudio) before relying on them:
  - First connection: `AuthenticationTokenRequest` makes VTube Studio show an allow/deny popup to the owner; the returned token must be saved (git-ignored) and reused with `AuthenticationRequest` on later connections.
  - Expressions: `ExpressionActivationRequest` with `expressionFile` and `active`.
  - Animations: plugins trigger motions only through hotkeys (`HotkeyTriggerRequest`); there is no direct "play motion" request.
  - Parameters: `InjectParameterDataRequest`. An injected default parameter is only held while it is re-sent at least about once per second; after that VTube Studio takes control back.

## How it works

New module `app/avatar/` (directory from the proposal): a VTube Studio client (`vtube_studio.py`) behind a small avatar interface, so a different renderer could replace it later. A no-op avatar is used when the avatar is disabled in config.

### Hooks into the M1 pipeline

| Moment | Avatar does |
|---|---|
| Reply starts (the existing "thinking..." print) | thinking pose on |
| First audio starts playing | thinking pose off |
| `emotion` known (end of LLM JSON) | activate that emotion's expression |
| `action` known | trigger that action's hotkey (once per reply) |
| Each audio chunk plays | mouth follows its loudness |
| Reply finished (all audio done) | deactivate expression, mouth closed |
| Mute | mouth closed immediately, expression off, thinking off |

Note on timing: in the LLM's JSON `emotion` comes after `say`, so it is known only when the whole reply has streamed. In M1 measurements, LLM generation (~1 s) usually completes before the first audio starts (TTS ~1.2 s after the first sentence), so the expression normally lands as speech begins. If it arrives later, apply it as soon as it is known. Do not reorder the JSON: `config/persona.md` is owner-written and must not be edited.

All avatar calls are fire-and-forget from the pipeline's point of view: they must not be awaited on the speech path in a way that can delay audio.

### Lip sync

The player already decodes each sentence's MP3 to PCM before playing. From those samples, compute a loudness envelope (short frames, roughly 30 fps), normalize to 0–1 for `MouthOpen`, and send values in step with playback. Stop sending when playback ends or is stopped (mute), and send a final 0. Keep re-sending often enough that VTube Studio does not take the parameter back mid-sentence. Smoothing, gain, and frame rate are the builder's call; put the tunable numbers in config.

### Emotion and action mapping (config)

`config/settings.yaml` gains an `avatar:` section, keyed by name so models can be swapped:

- `enabled`, `url` (default `ws://localhost:8001`), plugin name, token file path.
- `expressions`: emotion → expression file name. For Akari: `happy: EyesLove.exp3.json`, `sulking: EyesCry.exp3.json`, `annoyed: SignAngry.exp3.json`, `shocked: SignShock.exp3.json`. `smug`, `confused`, `neutral`: no entry (neutral face). Owner agreed to leave these as the plain face for now.
- `actions`: action → hotkey name or ID. For Akari: `celebrate: Anim Shake` (plays Love), `facepalm: 5fbc7fac3d3649c6a8901600261a6983` (the unnamed Shock hotkey). Allow name or ID.
- `thinking`: a parameter pose (e.g. head tilt via `FaceAngleZ`, eyes up via `EyeRightY`), values in config. The exact pose is the designer's guess; the owner hasn't seen it yet and will tune it.
- Lip-sync tuning numbers.

A mapping entry that doesn't exist in the loaded model is logged once and skipped, not an error.

### Connection and failure behaviour

- On start, try to connect and authenticate. If VTube Studio isn't running, print one line (e.g. `avatar: VTube Studio not reachable, retrying in the background`) and continue; speech works without it.
- First run: tell the owner in the console to click "Allow" in VTube Studio. Save the token; reuse it next time. If the saved token is rejected, request a new one.
- On disconnect: reconnect with backoff (a few seconds), quietly. Log state changes once, not every retry.
- Supervised like other M1 modules: an avatar exception never reaches the main loop.
- On `/quit`: reset expression and mouth, close the socket.

### Console

`/stats` or the startup banner shows avatar status (connected / not connected). One new command, `/avatar`, prints status and re-runs a quick self-test (thinking pose, each mapped expression for about a second, each action) so the owner can check a newly loaded model. (Designer's call; small and useful when swapping models.)

## Testing

Automated (no VTube Studio, no network): a fake VTS server or fake client to check the message sequence for a reply (thinking on → off at audio start → expression on → action → expression off), mute resets everything, the envelope computation (silence → 0, loud → near 1, length matches audio duration), missing mapping entries are skipped, and the pipeline still speaks and logs when the avatar raises or the connection is down. Existing 154 tests keep passing.

Manual (owner): with Akari loaded, run M1 and confirm: mouth moves with speech and closes at the end, the four mapped emotions show, celebrate/facepalm play, thinking pose appears before speech, mute closes the mouth, closing VTube Studio mid-session doesn't stop speech and reopening it reconnects.

## Decisions from the builder check

Settled by the designer (marked as the designer's call, not the owner's), so the builder doesn't guess:

1. **Audio hook (designer's call).** Decoding happens inside `SoundDeviceSink`, so the sink gains an optional per-playback hook: when it starts playing a buffer it hands the decoded samples and sample rate to a callback (or the sink computes the envelope itself and reports levels). Mouth values are paced by a wall-clock timer started at `sd.play`; no true playback-position sync. Drift up to about 100 ms is acceptable. Changing the `AudioSink` protocol and its test fakes is allowed; the existing tests must still pass (update fakes as needed, don't weaken assertions).
2. **Player/pipeline signals (designer's call).** `Player` may take an optional listener for "playback started", "playback finished" and "muted". Mute already flows through `MuteController` → `Player.set_muted`, so the avatar reset on mute hangs off that path. Per-reply events (thinking on, emotion, action, reply done) come from `Pipeline.handle`.
3. **When emotion applies (designer's call).** Apply emotion and action right after `Brain.reply` returns; no new streaming callback in `llm.py`. If any sentence was replaced by a safety deflection, or the reply fell back to a brain-lag line, use the neutral face and no action (the emotion belonged to text that wasn't spoken).
4. **Overlap: answered already.** The M1 worker handles one event at a time and `Pipeline.handle` waits for all audio before returning, and big gifts jump the queue without cutting off audio. So there is never more than one reply on screen; a single avatar state is enough. Reset to neutral at the end of each reply.
5. **Expressions (designer's call).** Track the currently active expression file; deactivate it explicitly (`active: false`) before activating another and at reply end. Don't use Akari's "Remove Expressions" hotkey (model-specific). Only one mapped expression is active at a time.
6. **Injected parameters (builder verifies API details).** Inject thinking pose and `MouthOpen` with mode "set", re-sent at the lip-sync frame rate while active. Release by stopping injection (after one final neutral value for `MouthOpen`), so VTube Studio's idle animation takes back over. If the official docs contradict this, follow the docs and note it.
7. **URL and token (answered).** `url` and token-file path are in config (default `ws://localhost:8001`, token under a git-ignored path such as `config/.vts_token`). Non-localhost hosts aren't expected but need no special handling.

## Left to the builder

WebSocket library (a VTS-specific client like `pyvts` or plain `websockets`), envelope algorithm and timing method, exact module/class names inside `app/avatar/`, reconnect timings, how the pipeline signals the avatar (direct calls vs. events), the thinking pose's default values.
