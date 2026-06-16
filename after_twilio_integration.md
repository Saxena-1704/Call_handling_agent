# After Twilio Integration — Status Report

## What Works
- **Outbound call via Twilio REST API** — `make_call.py` / `POST /twilio/make_call` initiates a call to an Indian number
- **TwiML Bin** — Twilio fetches TwiML from its own servers (bypasses ngrok HTTP fallback)
- **Audio pipeline** — user voice → VAD → STT → LLM → TTS → audio played back on phone
- **State machine** — IDLE → LISTENING → PROCESSING → SPEAKING cycles correctly
- **TTS playback** — edge-tts + ffmpeg → PCM → WebSocket → Twilio → phone speaker

## Fixes Applied

### 1. `make_call.py` + `POST /twilio/make_call` (new)
Standalone script and endpoint to trigger outbound calls from Twilio to the user's number.

### 2. `tts.py` — subprocess fix (Python 3.14 + Windows)
`asyncio.create_subprocess_exec()` raises `NotImplementedError` on Python 3.14/Windows. Replaced with `subprocess.Popen` via `loop.run_in_executor()`. All I/O (stdin write, stdout read) goes through thread pool.

### 3. `agent.py` — TTS stop on barge-in
Added `self.tts.stop()` to kill ffmpeg immediately during barge-in, so the TTS stream doesn't keep running after interruption.

## Remaining Issues

### Barge-in not working
- Interruption is detected (speech is processed, response generated) but current playback doesn't stop
- New response plays only after the current one finishes fully
- Possible causes:
  - `send_json` in `play()` blocks, delaying the `_stop_evt` check
  - TTS stream cleanup (`await feed_task`) keeps the generator alive too long
  - VAD `started` flag timing with Twilio audio latency

### High latency
- Noticeable delay between user speaking and agent responding
- Bottlenecks likely:
  - Twilio audio round-trip (phone → Twilio → ngrok → server)
  - WebSocket µ-law encode/decode + 8kHz ↔ 16kHz resampling
  - edge-tts synthesis (cloud-based, slower than real-time)
  - ffmpeg subprocess pipe overhead

## Next Steps
- Debug barge-in: add logging in `_on_audio_chunk` to confirm `started=True` + `SPEAKING` state
- Profile latency: measure each stage (VAD, STT, LLM, TTS, network)
- Consider faster TTS (e.g., Kokoro, Piper) or local synthesis
- Consider direct µ-law passthrough to skip resampling
