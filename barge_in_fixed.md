# Barge-In Fix — Root Cause & Resolution

## The Problem

Barge-in worked with the local microphone/speaker (`LocalAudioDevice`) but not with Twilio (`TwilioMediaStreamDevice`). Three independent bugs caused this.

---

## Bug 1: TTS Generator Blocked on `feed_task`

### Symptom
When barge-in triggered during a *long* TTS response, `tts.stop()` was called but playback continued until the entire response finished.

### Root Cause
In `tts.py`, `synthesize_stream()` has two concurrent loops:

1. **`feed()` coroutine** — iterates `edge_tts.Communicate.stream()` (HTTP streaming from Microsoft's servers) and writes audio chunks to ffmpeg's stdin
2. **Main reading loop** — reads decoded PCM from ffmpeg's stdout and yields it to `play()`

When `tts.stop()` was called:
- `self._stopped = True` was set
- `self._ffmpeg_proc.kill()` killed ffmpeg

But the `finally` block in `synthesize_stream()` did:

```python
finally:
    await feed_task     # ← BLOCKED here
```

`feed_task` was stuck on `async for chunk in tts.stream()` waiting for the next HTTP chunk from Microsoft (can take seconds). The generator couldn't exit until `feed_task` completed. Meanwhile, `play()` was blocked on `async for chunk in stream` waiting for the generator, so old TTS chunks kept streaming to Twilio.

### Fix (tts.py:76 + :86-87)

```python
while True:
    if self._stopped:       # ← new: stop reading ffmpeg immediately
        break
    data = await loop.run_in_executor(None, proc.stdout.read, 4096)
    ...
finally:
    if not self._stopped:   # ← new: skip the HTTP-blocking await
        await feed_task
```

When `_stopped=True`, the generator exits instantly — no waiting for `feed_task`. The orphaned `feed_task` dies silently in the background (broken pipe since ffmpeg is killed).

---

## Bug 2: Stale VAD State Across Barge-In Cycles

### Symptom
First barge-in works, subsequent ones don't. The agent stops responding to interruptions after the first cycle.

### Root Cause
After a successful barge-in, the state machine enters `PROCESSING` (STT + LLM run). During this time, Twilio keeps streaming mic audio to the server. The VAD processes this audio, and ambient noise or echo may trigger Silero VAD — setting `_triggered=True`.

When the next TTS response starts playing and the user speaks again, the VAD checks if speech started. But `_triggered` is already `True` (from the earlier noise), so the `"start"` event never fires — and neither does `started=True`. The barge-in condition `current == SPEAKING and started` is never met.

### Fix (agent.py:120)

```python
self._sm.transition(CallEvent.RESPONSE_READY)
self.vad.reset()   # ← new: clear noise-triggered state before playing
```

`vad.reset()` clears the audio buffer, the `_triggered` flag, and the internal Silero model state — guaranteeing a clean slate for each TTS playback cycle.

---

## Bug 3: TTS Outpaced VAD on Short Responses

### Symptom
Short responses (2–5 seconds) never get interrupted, even though the VAD and all other mechanisms work. Long responses (10+ seconds) interrupt correctly.

### Root Cause
The TTS pipeline (edge-tts + ffmpeg) processes audio faster than real-time for short responses. When `play()` iterates the generator, it receives all chunks nearly instantly, sends them to Twilio via `send_json`, and returns — all before the VAD detects the user's speech.

Timeline for a short response (~3 seconds of audio):

```
TTS synthesizes all audio in ~1 second
  → generator yields all chunks (~24 chunks at 128ms each)
    → play() sends all 24 chunks via WebSocket in ~100ms
      → play() returns
        → VAD finishes detecting speech start
          → barge-in fires → but nothing left to interrupt!
```

### Fix (twilio_device.py:66)

```python
await self.ws.send_json({...})
if self._stop_evt.is_set():
    break
await asyncio.sleep(len(chunk) / 16000)    # ← new: throttle to real-time
```

Each chunk is 2048 samples at 16kHz = 128ms of audio. The sleep ensures `play()` takes at least 128ms per chunk, matching the playback speed. This guarantees the VAD has enough time to detect interruption before `play()` exits.

---

## Audio Quality Impact & Fix

### Why Quality Is Reduced

The throttle `await asyncio.sleep(len(chunk) / 16000)` adds a *fixed* 128ms sleep per chunk. But the `send_json`, µ-law encoding, and base64 encoding also take some processing time (`t`). The actual per-chunk delay becomes `128ms + t`, which is slightly longer than the audio duration. Over many chunks, this desynchronization causes **audible gaps or stuttering** in playback.

### Proper Fix

Track elapsed time and only sleep if ahead of schedule:

In `twilio_device.py`:

```python
import time

async def play(self, stream):
    self._playing = True
    self._stop_evt.clear()
    chunk_duration = 0.128  # 2048 samples / 16000 Hz
    next_target = time.monotonic()
    try:
        async for chunk in stream:
            if self._stop_evt.is_set():
                break
            try:
                chunk_8k = chunk[::2]
                ulaw_bytes = _linear_to_ulaw(chunk_8k.tobytes())
                payload = base64.b64encode(ulaw_bytes).decode()
                await self.ws.send_json({
                    "event": "media",
                    "streamSid": self._stream_sid,
                    "media": {"payload": payload}
                })
                if self._stop_evt.is_set():
                    break
                next_target += chunk_duration
                now = time.monotonic()
                if now < next_target:
                    await asyncio.sleep(next_target - now)
            except Exception:
                break
    finally:
        self._playing = False
```

This subtracts the processing time from the sleep, eliminating accumulated drift and preserving glitch-free playback.

---

## Summary of All Changes

| File | Change | Why |
|------|--------|-----|
| `tts.py:76` | `if self._stopped: break` in main loop | Stop reading ffmpeg pipe immediately |
| `tts.py:86-87` | `if not self._stopped: await feed_task` | Don't block on HTTP stream when stopping |
| `agent.py:120` | `self.vad.reset()` before TTS | Clear stale VAD state each cycle |
| `twilio_device.py:64-65` | Post-send `_stop_evt` check | Faster loop exit after network send |
| `twilio_device.py:66` | `await asyncio.sleep(len(chunk) / 16000)` | Throttle to real-time for VAD to catch up |
