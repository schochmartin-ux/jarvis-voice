"""Config flow for the Jarvis Voice integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    AssistPipelineSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ANNOUNCE_LATE,
    CONF_ASSISTANT,
    CONF_DEADLINE,
    CONF_LAUNCH_SOUND,
    CONF_REMEMBER,
    CONF_SKILL_ID,
    CONF_VERIFY_SIGNATURE,
    CONF_VOICE,
    DEADLINE_RANGE,
    DEFAULT_ASSISTANT,
    DOMAIN,
    ENDPOINT,
    LAUNCH_SOUNDS,
    RECOMMENDED_ANNOUNCE_LATE,
    RECOMMENDED_DEADLINE,
    RECOMMENDED_LAUNCH_SOUND,
    RECOMMENDED_REMEMBER,
    RECOMMENDED_VERIFY_SIGNATURE,
    RECOMMENDED_VOICE,
    VOICES,
    TITLE,
)

DEFAULTS: dict[str, Any] = {
    CONF_ASSISTANT: DEFAULT_ASSISTANT,
    CONF_LAUNCH_SOUND: RECOMMENDED_LAUNCH_SOUND,
    CONF_DEADLINE: RECOMMENDED_DEADLINE,
    CONF_REMEMBER: RECOMMENDED_REMEMBER,
    CONF_ANNOUNCE_LATE: RECOMMENDED_ANNOUNCE_LATE,
    CONF_VERIFY_SIGNATURE: RECOMMENDED_VERIFY_SIGNATURE,
    CONF_VOICE: RECOMMENDED_VOICE,
}

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SKILL_ID): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(CONF_ASSISTANT): AssistPipelineSelector(),
        vol.Optional(CONF_LAUNCH_SOUND): SelectSelector(
            SelectSelectorConfig(
                options=LAUNCH_SOUNDS,
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_VOICE): SelectSelector(
            SelectSelectorConfig(options=VOICES, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
        ),
        vol.Required(CONF_DEADLINE): NumberSelector(
            NumberSelectorConfig(
                min=DEADLINE_RANGE[0],
                max=DEADLINE_RANGE[1],
                step=0.1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Required(CONF_REMEMBER): bool,
        vol.Required(CONF_ANNOUNCE_LATE): bool,
        vol.Required(CONF_VERIFY_SIGNATURE): bool,
    }
)


def _validate(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Clean the form values and return them with any field errors."""
    data = dict(user_input)
    data[CONF_SKILL_ID] = (data.get(CONF_SKILL_ID) or "").strip()
    data[CONF_LAUNCH_SOUND] = (data.get(CONF_LAUNCH_SOUND) or "").strip()
    data[CONF_VOICE] = (data.get(CONF_VOICE) or "").strip()
    errors: dict[str, str] = {}
    if not data[CONF_SKILL_ID]:
        # The endpoint is open to the internet, so without the skill id the
        # signature check would be the only thing guarding it.
        errors[CONF_SKILL_ID] = "skill_id_required"
    sound = data[CONF_LAUNCH_SOUND]
    if sound and not sound.startswith(("soundbank://", "https://")):
        errors[CONF_LAUNCH_SOUND] = "sound_invalid"
    return data, errors


class AlexaAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the skill endpoint."""

    VERSION = 1
    MINOR_VERSION = 2

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the skill id and the assistant that answers."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(title=TITLE, data={}, options=data)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input or DEFAULTS),
            errors=errors,
            description_placeholders={"endpoint": ENDPOINT},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow (the same settings as setup)."""
        return AlexaAssistOptionsFlow()


class AlexaAssistOptionsFlow(OptionsFlow):
    """Change the skill settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the settings form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(data=data)

        current: Mapping[str, Any] = user_input or {**DEFAULTS, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(SCHEMA, current),
            errors=errors,
            description_placeholders={"endpoint": ENDPOINT},
        )
