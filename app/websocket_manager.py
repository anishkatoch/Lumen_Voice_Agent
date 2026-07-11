"""
WebSocket connection manager.
Tracks all active sessions, isolates state per user.
"""
import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from fastapi import WebSocket
from app.config import WS_MAX_USERS, VAD_SENSITIVITY, VAD_BARGE_IN_SENSITIVITY


class AgentState(str, Enum):
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"


@dataclass
class UserSession:
    user_id: str
    websocket: WebSocket
    conversation_id: str
    state: AgentState = AgentState.LISTENING
    sensitivity: float = field(default_factory=lambda: VAD_SENSITIVITY)
    barge_in_sensitivity: float = field(default_factory=lambda: VAD_BARGE_IN_SENSITIVITY)
    audio_buffer: bytearray = field(default_factory=bytearray)
    message_count: int = 0
    tts_task: asyncio.Task | None = None
    speaking_started_at: float = 0.0
    barge_in_armed: bool = False

    def reset_audio(self):
        self.audio_buffer = bytearray()


class ConnectionManager:
    def __init__(self):
        self._sessions: dict[str, UserSession] = {}
        self._max_users = WS_MAX_USERS

    def is_full(self) -> bool:
        return len(self._sessions) >= self._max_users

    def add_session(self, session: UserSession):
        self._sessions[session.user_id] = session

    def remove_session(self, user_id: str) -> UserSession | None:
        return self._sessions.pop(user_id, None)

    def get_session(self, user_id: str) -> UserSession | None:
        return self._sessions.get(user_id)

    def active_count(self) -> int:
        return len(self._sessions)

    async def send_json(self, websocket: WebSocket, data: dict):
        try:
            await websocket.send_text(json.dumps(data))
        except Exception:
            pass

    async def send_bytes(self, websocket: WebSocket, data: bytes):
        try:
            await websocket.send_bytes(data)
        except Exception:
            pass


manager = ConnectionManager()
