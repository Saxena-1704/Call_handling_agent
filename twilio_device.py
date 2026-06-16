import asyncio
import base64
import numpy as np
from typing import Callable, AsyncIterator
from fastapi import WebSocket, WebSocketDisconnect
from audio_device import AudioDevice


_BIAS = 0x84


class TwilioMediaStreamDevice(AudioDevice):
    def __init__(self, websocket: WebSocket, stream_sid: str, call_sid: str = ""):
        self.ws = websocket
        self._stream_sid = stream_sid
        self._call_sid = call_sid
        self._on_audio: Callable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._playing = False
        self._stop_evt = asyncio.Event()
        self._call_ended = asyncio.Event()
        self._reader_task: asyncio.Task | None = None

    async def start(self, on_audio: Callable) -> None:
        self._loop = asyncio.get_running_loop()
        self._on_audio = on_audio
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            while True:
                msg = await self.ws.receive_json()
                event = msg.get("event")
                if event == "media":
                    payload = msg["media"]["payload"]
                    ulaw_bytes = base64.b64decode(payload)
                    chunk = _ulaw_to_linear(ulaw_bytes)
                    chunk = _upsample_8k_to_16k(chunk)
                    if self._on_audio:
                        await self._on_audio(chunk)
                elif event == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            self._call_ended.set()

    async def play(self, stream: AsyncIterator[np.ndarray]) -> None:
        self._playing = True
        self._stop_evt.clear()
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
                    await asyncio.sleep(len(chunk) / 16000)
                except Exception:
                    break
        finally:
            self._playing = False

    async def stop_playback(self) -> None:
        self._stop_evt.set()

    async def wait_for_call_end(self) -> None:
        await self._call_ended.wait()

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass


def _ulaw_to_linear(ulaw_bytes: bytes) -> np.ndarray:
    uval = (~np.frombuffer(ulaw_bytes, dtype=np.uint8)).astype(np.int32)
    sign = (uval >> 7) & 1
    seg = (uval >> 4) & 0x07
    mant = uval & 0x0F
    sample = (((mant << 3) + _BIAS) << seg) - _BIAS
    return np.where(sign != 0, -sample, sample).astype(np.int16)


def _linear_to_ulaw(pcm_bytes: bytes) -> bytes:
    pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
    x = np.abs(pcm).astype(np.int32)
    x = np.minimum(x, 32124)

    best_err = np.full(len(x), 2 ** 31 - 1, dtype=np.int32)
    best_seg = np.zeros(len(x), dtype=np.int32)
    best_mant = np.zeros(len(x), dtype=np.uint8)

    for s in range(8):
        mant = np.maximum(0, (x >> s) - _BIAS) >> 3
        mant_4bit = mant.astype(np.uint8) & 0x0F
        decoded = (((mant_4bit.astype(np.int32) << 3) + _BIAS) << s) - _BIAS
        err = np.abs(decoded.astype(np.int64) - x.astype(np.int64)).astype(np.int32)
        better = err < best_err
        best_err = np.where(better, err, best_err)
        best_seg = np.where(better, s, best_seg)
        best_mant = np.where(better, mant_4bit, best_mant)

    sign = (pcm >> 8) & 0x80
    packed = (best_seg.astype(np.uint8) << 4) | best_mant
    ulaw = packed ^ (0xFF - sign.astype(np.uint8))
    return ulaw.astype(np.uint8).tobytes()


def _upsample_8k_to_16k(audio_8k: np.ndarray) -> np.ndarray:
    x_old = np.arange(len(audio_8k))
    x_new = np.linspace(0, len(audio_8k) - 1, len(audio_8k) * 2)
    return np.interp(x_new, x_old, audio_8k).astype(np.int16)
