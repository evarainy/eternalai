"""The installed Runtime provider proposes only registered read primitives."""

import asyncio
from typing import Any

import pytest

from app.browser_skill.repair import RepairError
from app.browser_skill.site_rules import RegisteredExploration
from app.infra.browser.runtime_exploration import RuntimeLLMExplorationSuggester
from app.ports.llm_provider import LLMCompletionResponse, LLMMessage
from tests.infra.browser.test_local_read_execution import registered_site


class ProposalProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[LLMMessage] = []
        self.model: str | None = None

    async def complete(
        self, messages: list[LLMMessage], model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        self.messages, self.model = messages, model
        assert response_format is not None
        return LLMCompletionResponse(content=self.content, model_used=model)


def _publication():
    source = registered_site().manifest
    exploration = RegisteredExploration(
        allowed_regions=(source.site.steps[0].observation.region_id,),
        max_steps=2, max_seconds=30,
    )
    return source.model_copy(update={
        "site": source.site.model_copy(update={
            "primitive_rules": source.site.steps,
            "exploration": exploration,
        }),
    })


def test_runtime_exploration_uses_bounded_registered_catalog() -> None:
    async def run() -> None:
        publication = _publication()
        primitive = publication.site.steps[0].step.step_id
        provider = ProposalProvider(
            '{"status":"next","primitive_id":"' + primitive + '"}',
        )
        suggest = RuntimeLLMExplorationSuggester(publication, provider, "runtime-model")
        assert await suggest("run", ()) == primitive
        assert provider.model == "runtime-model"
        assert primitive in provider.messages[1].content
        assert "prior_primitive_ids" in provider.messages[1].content
        provider.content = '{"status":"stop","primitive_id":null}'
        assert await suggest("run", (primitive,)) is None
        with pytest.raises(RepairError):
            await suggest("run", (primitive, primitive))

    asyncio.run(run())


def test_runtime_exploration_rejects_unregistered_or_duplicate_proposal() -> None:
    async def run() -> None:
        publication = _publication()
        provider = ProposalProvider('{"status":"next","primitive_id":"unregistered"}')
        suggest = RuntimeLLMExplorationSuggester(publication, provider, "runtime-model")
        with pytest.raises(RepairError):
            await suggest("run", ())
        provider.content = '{"status":"stop","primitive_id":null,"status":"next"}'
        with pytest.raises(RepairError):
            await suggest("run", ())

    asyncio.run(run())
