"""Tests for the Alexa Assist config and options flows."""

from __future__ import annotations

from typing import Any

from custom_components.jarvis_voice.const import (
    CONF_ANNOUNCE_LATE,
    CONF_ASSISTANT,
    CONF_DEADLINE,
    CONF_LAUNCH_SOUND,
    CONF_REMEMBER,
    CONF_SKILL_ID,
    CONF_VERIFY_SIGNATURE,
    DEFAULT_ASSISTANT,
    DOMAIN,
    ENDPOINT,
    RECOMMENDED_LAUNCH_SOUND,
    TITLE,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import SKILL_ID

ASSISTANT_ID = "01JALEXAASSISTANTPIPELINE0"

FORM: dict[str, Any] = {
    CONF_SKILL_ID: f"  {SKILL_ID} ",
    CONF_ASSISTANT: ASSISTANT_ID,
    CONF_LAUNCH_SOUND: RECOMMENDED_LAUNCH_SOUND,
    CONF_DEADLINE: 6.5,
    CONF_REMEMBER: True,
    CONF_ANNOUNCE_LATE: True,
    CONF_VERIFY_SIGNATURE: True,
}


def _suggested(result: Any, key: str) -> Any:
    """Return the value a form field is pre-filled with."""
    for field in result["data_schema"].schema:
        if field == key:
            return (field.description or {}).get("suggested_value")
    raise KeyError(key)


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """Setup asks for the skill, defaults to the preferred assistant, and stores the options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"endpoint": ENDPOINT}
    assert _suggested(result, CONF_ASSISTANT) == DEFAULT_ASSISTANT

    result = await hass.config_entries.flow.async_configure(result["flow_id"], FORM)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {}
    assert result["options"][CONF_SKILL_ID] == SKILL_ID
    assert result["options"][CONF_ASSISTANT] == ASSISTANT_ID


async def test_user_flow_needs_a_skill_id(hass: HomeAssistant) -> None:
    """A blank skill id would leave the open endpoint guarded by the signature alone."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**FORM, CONF_SKILL_ID: "   "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SKILL_ID: "skill_id_required"}


async def test_user_flow_rejects_a_bad_sound(hass: HomeAssistant) -> None:
    """Only soundbank:// and https:// sounds are accepted."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**FORM, CONF_LAUNCH_SOUND: "http://example.com/a.mp3"}
    )
    assert result["errors"] == {CONF_LAUNCH_SOUND: "sound_invalid"}


async def test_single_instance(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """There is only one endpoint, so only one entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_changes_the_settings(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The options form starts from the current values and saves the new ones."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert _suggested(result, CONF_SKILL_ID) == SKILL_ID

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**FORM, CONF_LAUNCH_SOUND: "", CONF_REMEMBER: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options[CONF_LAUNCH_SOUND] == ""
    assert init_integration.options[CONF_REMEMBER] is False
    assert init_integration.options[CONF_ASSISTANT] == ASSISTANT_ID
