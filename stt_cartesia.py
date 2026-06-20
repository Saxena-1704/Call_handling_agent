import os
import numpy as np
from dotenv import load_dotenv
from cartesia import AsyncCartesia


class CartesiaSTT:
    def __init__(
        self,
        model: str = "ink-2",
        sample_rate: int = 16000,
        api_key: str | None = None,
    ):
        load_dotenv()
        api_key = api_key or os.environ.get("CARTESIA_API_KEY")
        if not api_key:
            raise RuntimeError("CARTESIA_API_KEY environment variable not set")
        self.model = model
        self.sample_rate = sample_rate
        self._client = AsyncCartesia(api_key=api_key)
        self._conn = None
        self._conn_mgr = None
        self._stream_active = False
        self._stream_buf = bytearray()
        self._stream_chunk_size = 3200

    async def _ensure_connected(self):
        if self._conn is None:
            self._conn_mgr = self._client.stt.manual_finalize.websocket(
                model=self.model,
                encoding="pcm_s16le",
                sample_rate=self.sample_rate,
            )
            self._conn = await self._conn_mgr.__aenter__()

    async def start_stream(self):
        await self._ensure_connected()
        self._stream_active = True
        self._stream_buf.clear()

    async def feed_chunk(self, chunk: np.ndarray):
        if not self._stream_active:
            return
        chunk_bytes = chunk.astype(np.int16).tobytes()
        self._stream_buf.extend(chunk_bytes)
        while len(self._stream_buf) >= self._stream_chunk_size:
            to_send = bytes(self._stream_buf[:self._stream_chunk_size])
            self._stream_buf = self._stream_buf[self._stream_chunk_size:]
            await self._conn.send_raw(to_send)

    async def finalize(self) -> str:
        if not self._stream_active:
            return ""
        self._stream_active = False
        if self._stream_buf:
            await self._conn.send_raw(bytes(self._stream_buf))
            self._stream_buf.clear()
        await self._conn.send_raw("finalize")
        parts: list[str] = []
        while True:
            msg = await self._conn.recv()
            t = getattr(msg, "type", None)
            if t == "transcript":
                text = getattr(msg, "text", "")
                is_final = getattr(msg, "is_final", False)
                if is_final:
                    parts.append(text)
            elif t == "flush_done":
                break
            elif t == "error":
                raise RuntimeError(
                    f"Cartesia STT error: {getattr(msg, 'message', 'unknown')}"
                )
        return "".join(parts)

    async def transcribe_async(self, audio: np.ndarray) -> str:
        await self._ensure_connected()
        audio_bytes = audio.astype(np.int16).tobytes()

        chunk_size = 3200
        for i in range(0, len(audio_bytes), chunk_size):
            await self._conn.send_raw(audio_bytes[i : i + chunk_size])

        await self._conn.send_raw("finalize")

        parts: list[str] = []
        while True:
            msg = await self._conn.recv()
            t = getattr(msg, "type", None)

            if t == "transcript":
                text = getattr(msg, "text", "")
                is_final = getattr(msg, "is_final", False)
                if is_final:
                    parts.append(text)
            elif t == "flush_done":
                break
            elif t == "error":
                raise RuntimeError(
                    f"Cartesia STT error: {getattr(msg, 'message', 'unknown')}"
                )

        return "".join(parts)

    async def close(self):
        if self._conn_mgr is not None:
            await self._conn_mgr.__aexit__(None, None, None)
            self._conn = None
            self._conn_mgr = None
        await self._client.close()
