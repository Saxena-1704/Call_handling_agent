# Voice Agent — Call Handling System

A modular, async-first voice agent with **Twilio telephony**, **Cartesia STT/TTS**, **Silero VAD**, **Groq LLM**, and **barge-in** support.

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A [Twilio](https://twilio.com) account with a purchased phone number
- [ngrok](https://ngrok.com) account + CLI installed
- API keys: **Groq** (from [console.groq.com](https://console.groq.com)), **Cartesia** (from [cartesia.ai](https://cartesia.ai))

### 2. Environment Variables

Create a `.env` file in the project root:

```
GROQ_API_KEY=gsk_...
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
MY_PHONE_NUMBER=+91xxxxxxxxxx
CARTESIA_API_KEY=sk_car_...
TWILIO_WS_URL=wss://placeholder.ngrok-free.app/twilio/media-stream
```

| Variable | Where to get it |
|----------|----------------|
| `GROQ_API_KEY` | [Groq Console](https://console.groq.com/keys) |
| `TWILIO_ACCOUNT_SID` | [Twilio Console](https://console.twilio.com) dashboard |
| `TWILIO_AUTH_TOKEN` | Twilio Console dashboard (same page) |
| `TWILIO_PHONE_NUMBER` | Your purchased Twilio number (e.g. `+12025551234`) |
| `MY_PHONE_NUMBER` | Your personal phone number that will receive the call |
| `CARTESIA_API_KEY` | [Cartesia Dashboard](https://cartesia.ai/account) |

### 3. Install Dependencies

```
pip install -r requirements.txt
```

### 4. Start the Server

```
python twilio_server.py
```

You should see: `Uvicorn running on http://0.0.0.0:8765`

### 5. Expose with ngrok

Open a **second terminal** and run:

```
ngrok http 8765
```

Look for the line:

```
Forwarding  wss://abaf-49-43-161-214.ngrok-free.app -> http://localhost:8765
```

Copy the `wss://` URL (e.g. `wss://abaf-49-43-161-214.ngrok-free.app`).

### 6. Update `TWILIO_WS_URL`

Edit `.env` and set the WebSocket URL to your ngrok address:

```
TWILIO_WS_URL=wss://abaf-49-43-161-214.ngrok-free.app/twilio/media-stream
```

### 7. Create a TwiML Bin

A TwiML Bin is a static XML snippet hosted by Twilio. It tells Twilio where to stream the audio.

1. Go to [Twilio Console → TwiML Bins](https://console.twilio.com/us1/develop/twiml-bins) (or search "TwiML Bins" in the console)
2. Click **Create new TwiML Bin**
3. Give it a name like `call-handling-agent`
4. Paste the following XML, **replacing the URL** with your own ngrok WebSocket URL:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://abaf-49-43-161-214.ngrok-free.app/twilio/media-stream" />
  </Connect>
</Response>
```

5. Click **Create**
6. Copy the generated TwiML Bin URL — it looks like:
   ```
   https://handler.twilio.com/twiml/EH5ba5302db6cf0428bda8b7235d3b66b8
   ```

### 8. Paste the TwiML Bin URL into `make_call.py`

Open `make_call.py` and replace the hardcoded URL on **line 18** with your TwiML Bin URL:

```python
# Before (line 18):
    url="https://handler.twilio.com/twiml/EH5ba5302db6cf0428bda8b7235d3b66b8",

# After:
    url="https://handler.twilio.com/twiml/EH<your-unique-id>",
```

This tells Twilio which TwiML Bin to use when making the outbound call.

### 9. Make a Call

Open a **third terminal** and run:

```
python make_call.py
```

Your phone will ring. Answer it and start speaking. The agent will listen, respond, and support barge-in (you can interrupt it).

---

## Architecture

```
                      ┌──────────────────────────────────────────────┐
                      │              Twilio Server                   │
                      │  (FastAPI, port 8765)                        │
                      │                                              │
  Twilio Voice ─────▶│  POST /incoming_call  (TwiML)              │
  (PSTN / SIP)       │  POST /make_call      (outbound REST)      │
                      │  WS  /media-stream    (µ-law ↔ PCM audio)  │
                      └──────────────┬───────────────────────────────┘
                                     │ TwilioMediaStreamDevice
                                     ▼
┌───────────────────────────────────────────────────────────────────┐
│                    VoiceAgentController (agent.py)                 │
│                                                                   │
│  AudioDevice ──▶ VAD ──▶ [segment queue] ──▶ CartesiaSTT         │
│      ▲                                          │                 │
│      │                                    ConversationManager     │
│      │                                          │                 │
│      │                                         LLM (Groq)         │
│      │                                          │                 │
│      │                                    CartesiaTTS             │
│      │                                          │                 │
│      └────────────── play() ◀───────────────────┘                 │
│                                                                   │
│  StateMachine: IDLE → LISTENING → PROCESSING → SPEAKING → ...    │
│  Barge-in: SPEAKING → INTERRUPTED on VAD speech start            │
└───────────────────────────────────────────────────────────────────┘
```

---

## File-by-File Purpose

| File | What it does |
|------|-------------|
| `agent.py` | **The conductor.** Owns all components, runs the state machine, processes speech segments from an async queue, handles barge-in. Accepts any `AudioDevice` implementation. |
| `agent_prompt.py` | System prompt for the LLM. |
| `audio_device.py` | **Audio I/O abstraction.** `AudioDevice` (ABC) with `start()`, `play()`, `stop_playback()`, `close()`. `LocalAudioDevice` implements it via `sounddevice` for laptop mic/speaker. |
| `twilio_device.py` | **Twilio audio device.** Implements `AudioDevice` over Twilio WebSocket media streams. Converts µ-law (8 kHz) ↔ linear PCM (16 kHz). |
| `twilio_server.py` | **FastAPI server** (port 8765). Endpoints: `POST /twilio/incoming_call` (TwiML), `POST /twilio/make_call` (outbound), `WS /twilio/media-stream` (real-time audio). Creates one `VoiceAgentController` per call. |
| `stt_cartesia.py` | **Speech-to-Text** via Cartesia WebSocket API (model `ink-2`). Sends PCM chunks, receives streaming transcripts. |
| `tts_cartesia.py` | **Text-to-Speech** via Cartesia WebSocket API (model `sonic-3.5`). Supports streaming token→audio — LLM tokens are pushed incrementally so audio starts before the full response is ready. |
| `VAD.py` | **Voice Activity Detection** using Silero VAD. Returns full speech segments and a `started` flag (critical for barge-in). |
| `llm.py` | **Language Model.** Calls Groq's API (LLaMA) with async streaming support. |
| `conversation.py` | **Conversation history.** Tracks user/assistant turns, builds the message list sent to the LLM with a system prompt. |
| `state_machine.py` | **Call state machine.** 5 states (`IDLE`, `LISTENING`, `PROCESSING`, `SPEAKING`, `INTERRUPTED`), 7 events. Invalid transitions silently ignored. |
| `event_bus.py` | **Async pub/sub.** Defined but currently unused by `VoiceAgentController`. |
| `make_call.py` | Standalone script to initiate an outbound Twilio call via the REST API. |

---

## State Machine

```
          ┌──────────────────────────────────────────┐
          │              STATES                       │
          │                                          │
          │   IDLE ──▶ LISTENING ──▶ PROCESSING      │
          │    ▲                       │              │
          │    │              ┌────────▼────────┐     │
          │    │              │    SPEAKING     │     │
          │    │              └──┬──────────┬───┘     │
          │    │                 │          │         │
          │    │          ┌──────▼──┐  ┌────▼──────┐ │
          │    │          │INTERRUPT│  │PLAYBACK_END│ │
          │    │          │  ED     │  │           │ │
          │    │          └──┬──────┘  └───────────┘ │
          │    │             │                       │
          │    └─────── CALL_END ────────────────────┘
          └──────────────────────────────────────────┘
```

| State | What happens |
|-------|-------------|
| **IDLE** | Waiting for call to start |
| **LISTENING** | Audio device is live, VAD is watching for speech |
| **PROCESSING** | STT transcribing + LLM generating. Self-loop allows queued segments. |
| **SPEAKING** | TTS audio is playing through the audio device |
| **INTERRUPTED** | User spoke during playback — TTS was stopped, VAD accumulates remainder |

### Transitions

| From | Event | To |
|------|-------|----|
| IDLE | `CALL_START` | LISTENING |
| LISTENING | `SPEECH_END` | PROCESSING |
| LISTENING | `CALL_END` | IDLE |
| PROCESSING | `SPEECH_END` | PROCESSING |
| PROCESSING | `RESPONSE_READY` | SPEAKING |
| PROCESSING | `ERROR` | LISTENING |
| PROCESSING | `CALL_END` | IDLE |
| SPEAKING | `PLAYBACK_END` | LISTENING |
| SPEAKING | `INTERRUPT` | INTERRUPTED |
| SPEAKING | `CALL_END` | IDLE |
| INTERRUPTED | `SPEECH_END` | PROCESSING |
| INTERRUPTED | `CALL_END` | IDLE |

---

## Call Flow

### Normal call

1. Agent starts → audio device streams input chunks to VAD
2. State: `IDLE → LISTENING`
3. VAD accumulates chunks, detects speech start → start flag set
4. User stops speaking → VAD returns the complete audio segment
5. State: `LISTENING → PROCESSING`
6. Segment goes into an async queue
7. Processing loop picks it up:
   - **CartesiaSTT** transcribes to text
   - **ConversationManager** adds the user turn
   - **LLM** generates a streaming response
   - **CartesiaTTS** receives tokens as they arrive — audio starts before LLM finishes
8. State: `PROCESSING → SPEAKING`
9. TTS audio chunks play through the audio device in real-time
10. State: `SPEAKING → LISTENING`
11. Back to step 3

### Barge-in (interruption)

1. Agent is in **SPEAKING**, TTS playing
2. User starts speaking
3. VAD detects speech just started → `started=True` on next chunk
4. State: `SPEAKING → INTERRUPTED`
5. `tts.stop()` called → cancels current Cartesia TTS stream
6. `audio_device.stop_playback()` called → user hears silence
7. VAD keeps accumulating the user's speech
8. User stops → VAD returns the segment
9. State: `INTERRUPTED → PROCESSING`
10. Segment queued → processing continues normally

If the user speaks while still in **PROCESSING**, the speech is queued and handled after the current response finishes.

---

## Key Design Points

- **`AudioDevice` abstraction** — `LocalAudioDevice` for local mic/speaker, `TwilioMediaStreamDevice` for phone calls. Swap by passing a different device to `VoiceAgentController`.
- **Streaming token→audio** — LLM tokens are pushed to Cartesia TTS incrementally via `TextToSpeechStream.push()`, enabling concurrent generation and playback with `asyncio.gather`.
- **Barge-in** — VAD's `started` flag triggers immediate TTS interruption. VAD state is reset before each TTS playback to avoid stale detection.
- **Real-time throttling** — `TwilioMediaStreamDevice.play()` uses `time.monotonic()` drift compensation to stay synchronized with the 16 kHz audio clock.
- **µ-law conversion** — Twilio delivers 8 kHz µ-law audio. `twilio_device.py` handles µ-law ↔ linear PCM conversion and linear interpolation resampling (8↔16 kHz).

---

## Dependencies

| Package | Used for |
|---------|----------|
| `sounddevice` | Local audio I/O (`LocalAudioDevice`) |
| `silero-vad` | Voice Activity Detection |
| `numpy` | Audio data manipulation |
| `groq` | Groq LLM API |
| `cartesia[websockets]` | Cartesia STT + TTS |
| `fastapi` | Twilio web server |
| `uvicorn[standard]` | ASGI server |
| `twilio` | Twilio REST API + TwiML |
| `python-multipart` | FastAPI form parsing |
| `python-dotenv` | `.env` loading |
| `ngrok` | Tunnel for Twilio webhook |
