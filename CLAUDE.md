# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

M1 thin slice runs from the console (typed events, no TikTok or OBS yet). M2 avatar drives a VTube Studio model (mouth, expressions, actions, thinking pose) from the reply pipeline; if VTube Studio is off or closes, speech carries on. VTube Studio's "Start API (allow plugins)" must be on (port 8001); the plugin token is stored in git-ignored `config/.vts_token`. The Chinese planning documents are the source of truth for scope and design:

- `AI Chibi 直播搭档 — 架构与方案提案.docx`: architecture, tech choices, latency/cost budgets, risks, planned directory layout.
- `短视频涨粉计划.docx`: a 14-day TikTok short-video plan. Several videos depend on dev milestones (e.g. Day 5 needs M1 brain + TTS; Day 8 needs the safety layer; Day 12 needs gift routing), so milestone order matters.

To read them: `python -I` with `zipfile` on `word/document.xml`. Write the output to a UTF-8 file; the Windows console is cp1252 and `-I` ignores `PYTHONIOENCODING`.

## Commands

- Setup: `python -m venv .venv`, then `.venv/Scripts/python -m pip install -r requirements.txt`, then copy `.env.example` to `.env` and set `DEEPSEEK_API_KEY`.
- Run: `.venv/Scripts/python -m app` (console `/avatar` prints avatar status and plays a quick expression test; `/yt <live link>` reads a YouTube live chat via the unofficial `pytchat`, read only, `/yt off` stops it)
- All tests: `.venv/Scripts/python -m pytest -q`
- One test: `.venv/Scripts/python -m pytest tests/test_safety.py::test_no_scunthorpe -v`

## What is being built

An original Live2D chibi character that co-hosts TikTok LIVE. It reads comments and gifts in real time, speaks replies with emotion-driven expressions and later plays simple games. Phase 1 is a "human + AI" duo stream. The AI hosting alone comes later.

Phase-1 success criteria: comment-to-speech latency under 3 s end to end, 2 hours of streaming with no crash and no policy-violating speech, and LLM + TTS cost per hour measured.

## Planned architecture

There is one Python 3.11 asyncio process. Modules talk through an internal event bus/queues. **If any module fails, the main loop keeps running and the character drops into an idle "daydream" state.** The stream must never go black or silent-crash.

Main pipeline:
`ingest → router → safety(in) → brain → safety(out) → tts → avatar → OBS → TikTok`. Viewer memory feeds the brain. An emergency hotkey mutes output and switches to the standby scene.

- **Ingest** (`app/ingest/`): the `TikTokLive` library is unofficial and needs only a username. Wrap it behind `base.py` as a swappable adapter with auto-reconnect. Include `mock.py` for development. Gift combos fire repeated events, so settle a gift only on the combo-end event, or it gets thanked twice. Read only; never auto-comment or auto-follow (account risk).
- **Router** (`app/router.py`): a priority queue. Order: big gifts (interrupt) > small gifts and follows > comments that address the character or ask questions > other comments (sampled; stale ones dropped) > idle self-talk after N seconds of quiet. Rate-limit per viewer.
- **Brain** (`app/brain/llm.py`): DeepSeek `deepseek-flash` through the **OpenAI SDK** (`base_url=https://api.deepseek.com`). Switching to local Ollama/Qwen should mean changing config only. Streaming on, thinking/reasoning off, replies of 1–2 sentences. Prompt order matters for cache pricing (cache hits are about 50× cheaper): fixed persona prefix (`config/persona.md`) first, then the last 10–20 turns, then the current event. Output JSON: `{"say": "...", "emotion": "happy", "action": null}`.
- **Memory** (`app/brain/memory.py`): SQLite holding only viewer nicknames and running jokes. No contact info or other personal data.
- **Safety** (`app/safety.py`): this layer is required. Two lists: `config/blocklist.txt` (profanity/slurs; dropped on input, replaced on output) and `config/topics.yaml` (sensitive-topic categories). Viewer messages on a sensitive topic are passed to the model, tagged with the category, so the persona can dodge in character. On output, a topic hit is replaced with a fixed deflection line only if the input wasn't tagged with that same category. Self-harm is never replaced on output; it's flagged to the owner on input. Viewer text is always wrapped as untrusted, non-instruction input. Full rules are in `docs/superpowers/specs/2026-10-29-m1-talking-chibi-design.md`.
- **TTS** (`app/tts/`): `edge-tts` during development only, because its commercial terms are unclear. Production candidates are GPT-SoVITS/CosyVoice (local) or Fish Audio/ElevenLabs (cloud). Synthesize sentence by sentence as the LLM streams.
- **Avatar** (`app/avatar/vtube_studio.py`): VTube Studio WebSocket plugin API. Mouth follows audio volume, expression follows `emotion`, and gifts trigger actions. Play a "thinking" animation while waiting for generation.
- **Game** (phase 2): only games where bots don't break the ToS, such as Minecraft via Mineflayer, text adventures, quizzes and viewer votes. Never competitive online games. Game state is fed to the brain as text.

Config lives in `config/settings.yaml`: model name, latency limits, and a per-stream token cap that switches to shorter or gift-only replies when exceeded. `DEEPSEEK_API_KEY` goes in `.env`, which is git-ignored.

## Latency budget

Measured 2026-11-03: 20 min of mock viewers (one event every 2–6 s), real DeepSeek and edge-tts, silent sink that waits for each clip's real length. 103 replies, no errors. Per-reply rows are in `logs/session-*.jsonl`; `/stats` prints p50/p90.

| Stage | Target | Measured p50 / p90 |
| --- | --- | --- |
| Ingest | 0.5 s | not measured (mock events start at arrival) |
| Router queue wait | 0.1 s | 11.1 s / 24.7 s |
| LLM first sentence | 1.2 s | 1.0 s / 1.3 s |
| TTS first audio | 0.8 s | 1.3 s / 1.9 s |
| Playback and mouth movement | 0.2 s | 0.0 s |
| **One reply, without queueing** | **≈2.8 s** | **2.3 s / 3.0 s** |
| **Comment to speech, with queueing** | | **13.3 s / 26.8 s** |

The pipeline meets the budget. Viewers still wait because each reply speaks for about 9 s (p50 98 characters) while events arrive every 2–6 s, so the character talks 100% of the time and the queue backs up.

Cost: $0.12/h at peak price, with the character talking nonstop. The DeepSeek cache hit rate was 91% for the first 16 turns and then fell to 58%, because dropping one old turn per reply changed the prefix after the persona. History now trims in batches (16 to 24 turns). A 10-minute rerun (47 replies) kept 91% after turn 16 and cost $0.02/h at off-peak price.

## First milestone (M1)

Mock comments → DeepSeek → safety filter → edge-tts, heard locally. No TikTok, Live2D or OBS needed yet.

## Open decisions (the owner's call; don't assume)

The character's name (viewers suggest it in comments; `character_name` stays a placeholder until then) and whether TTS runs locally or in the cloud.

Decided: the stream and short videos are in Manglish; the personality is the one in `config/persona.md` (no viewer vote). The video plan is `docs/tiktok-14-day-plan.md`.
