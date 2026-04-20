from dataclasses import dataclass, field

from services.api.app.schemas.session import GetSessionResponse
from services.api.app.schemas.task import GetTaskResponse


@dataclass
class InMemoryAPIState:
    sessions: dict[str, GetSessionResponse] = field(default_factory=dict)
    tasks: dict[str, GetTaskResponse] = field(default_factory=dict)

    def reset(self) -> None:
        self.sessions.clear()
        self.tasks.clear()


STATE = InMemoryAPIState()
