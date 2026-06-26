from enum import Enum, auto


class CallState(Enum):
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    SPEAKING = auto()
    INTERRUPTED = auto()


class CallEvent(Enum):
    CALL_START = auto()
    CALL_END = auto()
    SPEECH_END = auto()
    RESPONSE_READY = auto()
    INTERRUPT = auto()
    PLAYBACK_END = auto()
    ERROR = auto()


_TRANSITIONS: dict[CallState, dict[CallEvent, CallState]] = {
    CallState.IDLE: {
        CallEvent.CALL_START: CallState.LISTENING,
    },
    CallState.LISTENING: {
        CallEvent.SPEECH_END: CallState.PROCESSING,
        CallEvent.RESPONSE_READY: CallState.SPEAKING,
        CallEvent.CALL_END: CallState.IDLE,
    },
    CallState.PROCESSING: {
        CallEvent.SPEECH_END: CallState.PROCESSING,
        CallEvent.RESPONSE_READY: CallState.SPEAKING,
        CallEvent.ERROR: CallState.LISTENING,
        CallEvent.CALL_END: CallState.IDLE,
    },
    CallState.SPEAKING: {
        CallEvent.PLAYBACK_END: CallState.LISTENING,
        CallEvent.INTERRUPT: CallState.INTERRUPTED,
        CallEvent.CALL_END: CallState.IDLE,
    },
    CallState.INTERRUPTED: {
        CallEvent.SPEECH_END: CallState.PROCESSING,
        CallEvent.CALL_END: CallState.IDLE,
    },
}


class StateMachine:
    def __init__(self, on_transition=None):
        self._state = CallState.IDLE
        self._on_transition = on_transition

    @property
    def state(self) -> CallState:
        return self._state

    def transition(self, event: CallEvent) -> None:
        allowed = _TRANSITIONS.get(self._state)
        if allowed is None or event not in allowed:
            return
        old = self._state
        self._state = allowed[event]
        if self._on_transition:
            self._on_transition(old, self._state, event)
