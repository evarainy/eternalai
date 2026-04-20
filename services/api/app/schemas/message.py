"""Typed schemas for message submission boundaries."""

from typing import Literal

from pydantic import model_validator

from services.api.app.schemas.common import APIModel, MessageInputType, NonEmptyString, TaskId, TurnId


class SubmitMessageRequest(APIModel):
    input_type: MessageInputType
    text: NonEmptyString | None = None
    audio_ref: NonEmptyString | None = None
    language_hint: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_input_shape(self) -> "SubmitMessageRequest":
        if self.input_type == "text":
            if self.text is None:
                raise ValueError("text is required when input_type='text'")
            if self.audio_ref is not None:
                raise ValueError("audio_ref is not allowed when input_type='text'")
        if self.input_type == "audio":
            if self.audio_ref is None:
                raise ValueError("audio_ref is required when input_type='audio'")
            if self.text is not None:
                raise ValueError("text is not allowed when input_type='audio'")
        return self


class SubmitMessageResponse(APIModel):
    task_id: TaskId
    status: Literal["queued"] = "queued"
    accepted_turn_id: TurnId
