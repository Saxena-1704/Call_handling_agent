import os
import asyncio
import base64
import numpy as np
from dotenv import load_dotenv
from cartesia import AsyncCartesia


class TextToSpeechStream:
    """Persistent TTS context for streaming LLM tokens into immediate audio."""

    def __init__(self, ctx):
        self._ctx = ctx
        self._done = False

    async def push(self, token: str) -> None:
        if self._done:
            return
        await self._ctx.push(transcript=token, continue_=True)

    async def finish(self) -> None:
        if self._done:
            return
        self._done = True
        await self._ctx.no_more_inputs()

    async def cancel(self) -> None:
        if not self._done:
            self._done = True
            await self._ctx.cancel()

    async def receive(self):
        """Async generator yielding np.int16 PCM chunks as they arrive."""
        async for event in self._ctx.receive():
            if event.type == "chunk":
                data = event.data
                if isinstance(data, str):
                    data = base64.b64decode(data)
                if len(data) % 2 != 0:
                    data = data[:-1]
                if data:
                    yield np.frombuffer(data, dtype=np.int16)
            elif event.type == "done":
                break
            elif event.type == "error":
                raise RuntimeError(
                    f"Cartesia TTS error: {getattr(event, 'message', str(event))}"
                )


class CartesiaTTS:
    def __init__(
        self,
        model: str = "sonic-3.5",
        voice: dict | None = None,
        api_key: str | None = None,
    ):
        load_dotenv()
        api_key = api_key or os.environ.get("CARTESIA_API_KEY")
        if not api_key:
            raise RuntimeError("CARTESIA_API_KEY environment variable not set")
        self.model = model
        self.voice = voice or {
            "mode": "id",
            "id": "1fcd23d0-bf12-4896-8f60-4f21ef5c9b98",
        }
        self._client = AsyncCartesia(api_key=api_key)
        self._conn_mgr = None
        self._conn = None
        self._current_stream: TextToSpeechStream | None = None
        self._stopped = False

    async def _ensure_connected(self):
        if self._conn is None:
            self._conn_mgr = self._client.tts.websocket_connect()
            self._conn = await self._conn_mgr.__aenter__()

    async def create_stream(self) -> TextToSpeechStream:
        """Create a streaming TTS context for incremental token feeding."""
        await self._ensure_connected()
        ctx = self._conn.context(
            model_id=self.model,
            voice=self.voice,
            output_format={
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
            },
        )
        stream = TextToSpeechStream(ctx)
        self._current_stream = stream
        return stream

    async def synthesize_stream(self, text: str):
        """Full text → yields np.int16 PCM chunks. Drop-in for tts.py."""
        stream = await self.create_stream()
        await stream.push(text)
        await stream.finish()
        async for chunk in stream.receive():
            yield chunk

    async def synthesize(self, text: str) -> bytes:
        chunks: list[bytes] = []
        async for pcm in self.synthesize_stream(text):
            chunks.append(pcm.tobytes())
        return b"".join(chunks)

    def stop(self) -> None:
        self._stopped = True
        if self._current_stream is not None:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._current_stream.cancel())
            self._current_stream = None

    async def close(self):
        if self._current_stream is not None:
            await self._current_stream.cancel()
            self._current_stream = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        if self._conn_mgr is not None:
            await self._conn_mgr.__aexit__(None, None, None)
            self._conn_mgr = None
        await self._client.close()
