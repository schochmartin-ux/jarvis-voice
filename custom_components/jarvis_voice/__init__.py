"""The Jarvis Voice integration.

Serves an Alexa custom skill endpoint so Echo devices can talk to any Home
Assistant assistant. Alexa keeps the wake word, the speech recognition and the
voice; Home Assistant runs the assistant's intent stage.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.assist_pipeline import async_get_pipeline, async_get_pipelines
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .alexa import MEMORY, SPEAKERS, async_setup_view
from .const import CONF_AGENT, CONF_ASSISTANT, DEFAULT_ASSISTANT, DOMAIN, LOGGER
from .memory import MemoryRegistry, subject_for
from .speaker import DEFAULT_ENROLL_MINUTES, SpeakerRegistry

STORAGE_VERSION = 1
SERVICE_ENROLL = "enroll_speaker"
SERVICE_FORGET = "forget_speaker"
SERVICE_ADDRESS = "set_address"
SERVICE_TRUST = "set_trusted"
SERVICE_VOICES = "list_voices"
SERVICE_REMEMBER = "remember"
SERVICE_ADD_NOTE = "add_note"
SERVICE_FORGET_NOTE = "forget_note"
SERVICE_LIST_NOTES = "list_notes"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jarvis Voice from a config entry."""
    # Views cannot be unregistered: the endpoint reads the loaded entry on every
    # request, so options apply at once and an unloaded entry answers 404.
    async_setup_view(hass)

    registry = SpeakerRegistry(Store(hass, STORAGE_VERSION, f"{DOMAIN}.speakers"))
    await registry.async_load()
    hass.data[SPEAKERS] = registry
    memory = MemoryRegistry(Store(hass, STORAGE_VERSION, f"{DOMAIN}.memory"))
    await memory.async_load()
    hass.data[MEMORY] = memory
    _async_register_services(hass, registry)
    _async_register_memory_services(hass, registry, memory)
    return True


def _person(value: str) -> str:
    """Accept a person entity id."""
    value = cv.entity_id(value)
    if not value.startswith("person."):
        raise vol.Invalid("Expected a person entity")
    return value


@callback
def _async_register_services(hass: HomeAssistant, registry: SpeakerRegistry) -> None:
    """Register the services that manage who is who."""
    if hass.services.has_service(DOMAIN, SERVICE_ENROLL):
        return

    async def enroll(call):  # type: ignore[no-untyped-def]
        registry.start_enrollment(call.data["person"], call.data["minutes"])
        LOGGER.info("Waiting for %s to speak to Alexa", call.data["person"])

    async def forget(call):  # type: ignore[no-untyped-def]
        count = await registry.async_forget(call.data["person"])
        LOGGER.info("Forgot %s voice(s) of %s", count, call.data["person"])

    async def set_address(call):  # type: ignore[no-untyped-def]
        await registry.async_set_address(call.data["person"], call.data.get("address"))

    async def set_trusted(call):  # type: ignore[no-untyped-def]
        await registry.async_set_trusted(call.data["person"], call.data["trusted"])

    async def list_voices(call):  # type: ignore[no-untyped-def]
        return {
            "assigned": registry.assignments(),
            "unassigned_recently": registry.unmapped_recently(),
            "waiting_for": registry.pending,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_ENROLL,
        enroll,
        vol.Schema(
            {
                vol.Required("person"): _person,
                vol.Optional("minutes", default=DEFAULT_ENROLL_MINUTES): vol.All(
                    vol.Coerce(float), vol.Range(min=1, max=120)
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORGET, forget, vol.Schema({vol.Required("person"): _person})
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADDRESS,
        set_address,
        vol.Schema({vol.Required("person"): _person, vol.Optional("address"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TRUST,
        set_trusted,
        vol.Schema({vol.Required("person"): _person, vol.Required("trusted"): cv.boolean}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_VOICES,
        list_voices,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Move older entries to the current options."""
    if entry.version > 1:
        return False

    if entry.minor_version < 2:
        # 1.2 answers through an assistant instead of a bare conversation agent.
        options = dict(entry.options)
        assistant = _async_assistant_for_agent(hass, options.pop(CONF_AGENT, None))
        options[CONF_ASSISTANT] = assistant
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
        LOGGER.info("Alexa now answers through assistant %s", assistant)

    return True


@callback
def _async_assistant_for_agent(hass: HomeAssistant, agent: str | None) -> str:
    """Return the assistant that answers with the agent an entry used to call.

    The preferred assistant wins when it uses that agent, then any other one.
    When no assistant does, the agent id itself is kept: Home Assistant runs a
    conversation entity as a plain assistant, so Alexa keeps answering as before.
    """
    if not agent:
        return DEFAULT_ASSISTANT

    preferred = async_get_pipeline(hass)
    for pipeline in (preferred, *async_get_pipelines(hass)):
        if pipeline.conversation_engine == agent:
            return pipeline.id

    LOGGER.warning(
        "No assistant answers with %s, so Alexa keeps calling it directly. Pick an "
        "assistant in the Jarvis Voice options to use its settings",
        agent,
    )
    return agent


def _names(hass: HomeAssistant) -> dict[str, str]:
    """Return the names of all persons, by entity id."""
    return {state.entity_id: state.name for state in hass.states.async_all("person")}


def _author(hass: HomeAssistant, registry: SpeakerRegistry, call_data: dict) -> str:
    """Return who a note is for: the given person, else whoever just spoke."""
    if person := call_data.get("person"):
        return person
    if recent := registry.recent_speaker():
        return recent[0]
    raise ServiceValidationError(
        "Es ist unklar, wer spricht. Gib die Person an oder lass Alexa die Stimme erst erkennen."
    )


@callback
def _async_register_memory_services(
    hass: HomeAssistant, registry: SpeakerRegistry, memory: MemoryRegistry
) -> None:
    """Register the services behind Jarvis' memory.

    ``remember`` is what the assistant calls (through a script): it only proposes,
    and the note is saved when the person says yes. ``add_note`` saves at once and
    is for you, not for the assistant.
    """
    if hass.services.has_service(DOMAIN, SERVICE_REMEMBER):
        return

    def subject(call_data: dict, author: str) -> tuple[str, str]:
        return subject_for(call_data.get("about"), author, _names(hass))

    async def remember(call):  # type: ignore[no-untyped-def]
        author = _author(hass, registry, call.data)
        key, label = subject(call.data, author)
        ok, text = memory.propose(author, key, label, call.data["text"])
        return {"saved": False, "needs_confirmation": ok, "say": text}

    async def add_note(call):  # type: ignore[no-untyped-def]
        author = _author(hass, registry, call.data)
        key, label = subject(call.data, author)
        reason = await memory.async_add(key, label, call.data["text"], author)
        if reason:
            raise ServiceValidationError(reason)

    async def forget_note(call):  # type: ignore[no-untyped-def]
        author = _author(hass, registry, call.data)
        key, _ = subject(call.data, author)
        if call.data.get("all"):
            return {"deleted": await memory.async_forget_all(key)}
        return {"deleted": await memory.async_forget(key, call.data.get("match"))}

    async def list_notes(call):  # type: ignore[no-untyped-def]
        key = None
        if call.data.get("person") or call.data.get("about"):
            author = _author(hass, registry, call.data)
            key, _ = subject(call.data, author)
        return memory.listing(key)

    base = {
        vol.Optional("person"): _person,
        vol.Optional("about"): cv.string,
    }
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMEMBER,
        remember,
        vol.Schema({vol.Required("text"): cv.string, **base}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_NOTE, add_note, vol.Schema({vol.Required("text"): cv.string, **base})
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORGET_NOTE,
        forget_note,
        vol.Schema({vol.Optional("match"): cv.string, vol.Optional("all"): cv.boolean, **base}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_NOTES,
        list_notes,
        vol.Schema(base),
        supports_response=SupportsResponse.ONLY,
    )
