import asyncio
import numpy as np
from audio_device import AudioDevice, LocalAudioDevice
from VAD import VoiceActivityDetector
from stt import SpeechToText
from llm import LLM
from tts import TextToSpeech
from conversation import ConversationManager
from state_machine import StateMachine, CallState, CallEvent
from event_bus import EventBus


class VoiceAgentController:
    def __init__(
        self,
        audio_device: AudioDevice | None = None,
        vad: VoiceActivityDetector | None = None,
        stt: SpeechToText | None = None,
        llm: LLM | None = None,
        tts: TextToSpeech | None = None,
        conversation: ConversationManager | None = None,
        event_bus: EventBus | None = None,
    ):
        self.audio_device = audio_device or LocalAudioDevice()
        self.vad = vad or VoiceActivityDetector(min_silence_duration_ms=500)
        self.stt = stt or SpeechToText()
        self.llm = llm or LLM()
        self.tts = tts or TextToSpeech()
        self.conversation = conversation or ConversationManager(
            system_prompt=(
                "You are a friendly and helpful voice assistant on a phone call. "
                "Keep responses concise and conversational. "
                "If you are interrupted, do not acknowledge it. "
                "Never mention that you are an AI."
            )
        )
        self.event_bus = event_bus or EventBus()

        self._sm = StateMachine(on_transition=self._on_transition)
        self._segment_queue: asyncio.Queue[np.ndarray] = asyncio.Queue()
        self._processing_task: asyncio.Task | None = None
        self._running = False

    @property
    def state(self) -> CallState:
        return self._sm.state

    def _on_transition(self, old: CallState, new: CallState, event: CallEvent) -> None:
        print(f"[STATE] {old.name} --({event.name})--> {new.name}")

    async def run(self) -> None:
        self._running = True
        await self.audio_device.start(self._on_audio_chunk)
        self._sm.transition(CallEvent.CALL_START)
        self._processing_task = asyncio.create_task(self._processing_loop())

        print("[AGENT] Ready. Speak to start the conversation.")
        try:
            while self._running:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            if self._processing_task:
                self._processing_task.cancel()
            self._sm.transition(CallEvent.CALL_END)
            await self.audio_device.close()

    async def stop(self) -> None:
        self._running = False

    async def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        seg, started = self.vad.process(chunk)
        current = self._sm.state

        if current == CallState.SPEAKING and started:
            self._sm.transition(CallEvent.INTERRUPT)
            self.tts.stop()
            await self.audio_device.stop_playback()

        if seg is not None:
            current = self._sm.state
            if current in (CallState.LISTENING, CallState.INTERRUPTED, CallState.PROCESSING):
                self._sm.transition(CallEvent.SPEECH_END)
                await self._segment_queue.put(seg)

    async def _processing_loop(self) -> None:
        while self._running:
            segment = await self._segment_queue.get()
            try:
                await self._handle_segment(segment)
            except Exception as e:
                print(f"[ERROR] {e}")
                import traceback
                traceback.print_exc()
                if self._sm.state in (CallState.PROCESSING, CallState.SPEAKING):
                    self._sm.transition(CallEvent.ERROR)

    async def _handle_segment(self, segment: np.ndarray) -> None:
        text = await self.stt.transcribe_async(segment)
        print(f"[USER] {text}")

        self.conversation.add_turn("user", text)

        if self._sm.state != CallState.PROCESSING:
            return

        print("[LLM] generating...")
        response = await self.llm.generate(self.conversation.messages)
        print(f"[LLM] done")
        print(f"[AGENT] {response}")

        self.conversation.add_turn("assistant", response)

        if self._sm.state != CallState.PROCESSING:
            return

        self._sm.transition(CallEvent.RESPONSE_READY)
        self.vad.reset()

        stream = self.tts.synthesize_stream(response)
        await self.audio_device.play(stream)

        if self._sm.state == CallState.SPEAKING:
            self._sm.transition(CallEvent.PLAYBACK_END)


async def main():
    controller = VoiceAgentController()
    try:
        await controller.run()
    except KeyboardInterrupt:
        await controller.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
