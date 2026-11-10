"""Tests for answering through a Home Assistant assistant."""

from __future__ import annotations

from typing import Any, Literal
from unittest.mock import patch

from custom_components.jarvis_voice.const import (
    CONF_AGENT,
    CONF_ASSISTANT,
    CONF_SKILL_ID,
    CONF_VERIFY_SIGNATURE,
    DOMAIN,
    ENDPOINT,
    TITLE,
)
from homeassistant.components import conversation
from homeassistant.components.assist_pipeline import Pipeline, async_get_pipeline
from homeassistant.components.assist_pipeline.runtime import KEY_ASSIST_PIPELINE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import AGENT_ID, SKILL_ID
from .test_alexa import IT, _answer, _map_echo, _query

CONVERSE = "homeassistant.components.conversation.async_converse"


class _LlmAgent(conversation.AbstractConversationAgent):
    """An agent that stands in for an LLM and records what it is asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    @property
    def supported_languages(self) -> Literal["*"]:
        return "*"

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        self.asked.append(user_input.text)
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech("Dall'LLM.")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )


async def _llm_assistant(hass: HomeAssistant, *, prefer_local: bool) -> tuple[Pipeline, _LlmAgent]:
    """Create an assistant that answers with an LLM agent."""
    agent_entry = MockConfigEntry(domain="test_llm")
    agent_entry.add_to_hass(hass)
    agent = _LlmAgent()
    conversation.async_set_agent(hass, agent_entry, agent)

    pipeline = await hass.data[KEY_ASSIST_PIPELINE].pipeline_store.async_create_item(
        {
            "conversation_engine": agent_entry.entry_id,
            "conversation_language": "*",
            "language": "it",
            "name": "LLM",
            "stt_engine": None,
            "stt_language": None,
            "tts_engine": None,
            "tts_language": None,
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
            "prefer_local_intents": prefer_local,
        }
    )
    return pipeline, agent


async def _use(hass: HomeAssistant, entry: MockConfigEntry, assistant: str) -> None:
    """Point the skill at an assistant."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_ASSISTANT: assistant}
    )
    await hass.async_block_till_done()


async def _ask(hass_client_no_auth: Any, text: str) -> str:
    """Ask the skill something and return what Alexa would say."""
    client = await hass_client_no_auth()
    response = await client.post(ENDPOINT, json=_query(text))
    return (await response.json())["response"]["outputSpeech"]["text"]


async def test_a_local_command_skips_the_llm(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """With "Prefer handling commands locally", a command the house knows never reaches the LLM."""
    pipeline, agent = await _llm_assistant(hass, prefer_local=True)
    await _use(hass, init_integration, pipeline.id)

    local = intent.IntentResponse(language="it")
    local.async_set_speech("Luce accesa.")
    with patch("homeassistant.components.conversation.async_handle_intents", return_value=local):
        speech = await _ask(hass_client_no_auth, "accendi la luce")

    assert speech == "Luce accesa."
    assert agent.asked == []


async def test_everything_else_goes_to_the_assistant_agent(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """What the house does not know is answered by the assistant's own agent."""
    pipeline, agent = await _llm_assistant(hass, prefer_local=True)
    await _use(hass, init_integration, pipeline.id)

    with patch("homeassistant.components.conversation.async_handle_intents", return_value=None):
        speech = await _ask(hass_client_no_auth, "chi ha scritto la Divina Commedia")

    assert speech == "Dall'LLM."
    assert agent.asked == ["chi ha scritto la Divina Commedia"]


async def test_questions_show_in_the_assistant_debug_view(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Each question is a run of the assistant, listed where its debug view looks."""
    with patch(CONVERSE, new=_answer("Sono le tre.")):
        await _ask(hass_client_no_auth, "che ore sono")

    preferred = async_get_pipeline(hass)
    assert len(hass.data[KEY_ASSIST_PIPELINE].pipeline_debug[preferred.id]) == 1


async def test_a_mapped_echo_gives_the_assistant_its_device(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """The Echo's device goes along, so "the light" means the one in its area."""
    _map_echo(hass)
    seen: list[dict[str, Any]] = []
    answer = _answer("Fatto.")

    async def _capture(hass: HomeAssistant, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return await answer(hass, *args, **kwargs)

    with patch(CONVERSE, new=_capture):
        await _ask(hass_client_no_auth, "spegni la luce")

    [echo] = [
        device
        for device in dr.async_get(hass).devices
        if ("alexa_devices", "G000TEST") in device.identifiers
    ]
    assert seen[0]["device_id"] == echo.id


async def test_an_unmapped_echo_sends_no_device(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Without the label there is no device to hand over."""
    seen: list[dict[str, Any]] = []
    answer = _answer("Fatto.")

    async def _capture(hass: HomeAssistant, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return await answer(hass, *args, **kwargs)

    with patch(CONVERSE, new=_capture):
        await _ask(hass_client_no_auth, "spegni la luce")

    assert seen[0]["device_id"] is None


async def test_a_deleted_assistant_is_spoken_as_an_error(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """An assistant removed after setup fails loudly in the log, politely on the Echo."""
    await _use(hass, init_integration, "01JGONEGONEGONEGONEGONEGON")

    assert await _ask(hass_client_no_auth, "che ore sono") == IT["error"]


def _legacy_entry(hass: HomeAssistant, agent: str) -> MockConfigEntry:
    """Return an entry as 0.1 stored it, with a conversation agent."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        version=1,
        minor_version=1,
        data={},
        options={CONF_SKILL_ID: SKILL_ID, CONF_AGENT: agent, CONF_VERIFY_SIGNATURE: False},
    )
    entry.add_to_hass(hass)
    return entry


async def test_migration_picks_the_assistant_that_uses_the_agent(hass: HomeAssistant) -> None:
    """An entry that called the built-in agent now answers through its assistant."""
    # Load the pipeline store first, which creates the default assistant.
    assert await hass.config_entries.async_setup(_legacy_entry(hass, AGENT_ID).entry_id)
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.minor_version == 2
    assert CONF_AGENT not in entry.options
    assert entry.options[CONF_ASSISTANT] == async_get_pipeline(hass).id


async def test_migration_keeps_an_agent_no_assistant_uses(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """With no assistant for the agent, Alexa keeps calling that agent as before."""
    entry = _legacy_entry(hass, "conversation.not_in_any_assistant")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.options[CONF_ASSISTANT] == "conversation.not_in_any_assistant"
