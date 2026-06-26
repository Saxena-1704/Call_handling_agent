import os
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

from twilio_device import TwilioMediaStreamDevice
from agent import VoiceAgentController


load_dotenv()

_WS_URL = os.environ.get(
    "TWILIO_WS_URL",
    "wss://your-ngrok-url.ngrok.io/twilio/media-stream",
)

_DEFAULT_NGROK_BASE = _WS_URL.replace("wss://", "https://").replace("/twilio/media-stream", "")

app = FastAPI(title="Call Handling Agent — Twilio Server")
_active_calls: set[str] = set()


@app.api_route("/twilio/incoming_call", methods=["GET", "POST"])
async def incoming_call():
    resp = VoiceResponse()
    connect = Connect()
    connect.stream(url=_WS_URL)
    resp.append(connect)
    return Response(content=str(resp), media_type="application/xml")


@app.post("/twilio/make_call")
async def make_outbound_call():
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_number = os.environ["TWILIO_PHONE_NUMBER"]
    my_number = os.environ["MY_PHONE_NUMBER"]

    call = Client(account_sid, auth_token).calls.create(
        url=f"{_DEFAULT_NGROK_BASE}/twilio/incoming_call",
        to=my_number,
        from_=twilio_number,
    )

    return {"call_sid": call.sid, "status": "initiated"}


@app.websocket("/twilio/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    device = None
    agent = None
    agent_task = None
    call_sid = ""

    try:
        msg = await ws.receive_json()
        while msg.get("event") != "start":
            msg = await ws.receive_json()

        start = msg["start"]
        stream_sid = start["streamSid"]
        call_sid = start.get("callSid", "")

        if call_sid and call_sid in _active_calls:
            return

        if call_sid:
            _active_calls.add(call_sid)

        device = TwilioMediaStreamDevice(ws, stream_sid, call_sid)
        agent = VoiceAgentController(audio_device=device)
        agent_task = asyncio.create_task(agent.run())

        await device.wait_for_call_end()

    except WebSocketDisconnect:
        pass
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        if agent:
            await agent.stop()
        if agent_task:
            try:
                await agent_task
            except Exception:
                pass
        if device:
            await device.close()
        if call_sid:
            _active_calls.discard(call_sid)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("twilio_server:app", host="0.0.0.0", port=8765, reload=True)
