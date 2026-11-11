"""Alexa custom skill endpoint, so Echo devices can talk to an assistant.

An Echo cannot be an Assist satellite: skills get no access to the microphone.
What a custom skill does give us is the recognised text, which is all the
assistant's intent stage needs. Alexa keeps the wake word, the speech
recognition and the voice; Home Assistant keeps the house.

The catch is time. Alexa allows a skill about 8 seconds to return a full
response, so a slow turn cannot simply be waited out. Answers that arrive in
time are spoken as the response; the rest are announced on the Echo that asked,
once they are ready. Nothing is spoken in between: the wait stays silent rather
than filled, so the first thing the Echo says is the answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from http import HTTPStatus
from typing import Any
from xml.sax.saxutils import escape

from aiohttp import web

from homeassistant.components.assist_pipeline import (
    OPTION_PREFERRED,
    PipelineEvent,
    PipelineEventType,
    PipelineInput,
    PipelineNotFound,
    PipelineRun,
    PipelineStage,
    async_get_pipeline,
)
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import chat_session
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.util.hass_dict import HassKey

from .alexa_auth import AlexaAuthError, async_verify_request, verify_timestamp
from .const import (
    CONF_ANNOUNCE_LATE,
    CONF_ASSISTANT,
    CONF_DEADLINE,
    CONF_LAUNCH_SOUND,
    CONF_REMEMBER,
    CONF_SKILL_ID,
    CONF_VERIFY_SIGNATURE,
    DOMAIN,
    ENDPOINT,
    LOGGER,
    PHRASES,
    QUERY_INTENT,
    QUERY_SLOT,
    RECOMMENDED_ANNOUNCE_LATE,
    RECOMMENDED_DEADLINE,
    RECOMMENDED_LAUNCH_SOUND,
    RECOMMENDED_REMEMBER,
    RECOMMENDED_VERIFY_SIGNATURE,
)
from .memory import MemoryRegistry
from .speaker import UNKNOWN, Speaker, SpeakerRegistry, speaker_prompt

VIEW_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_alexa_view")
SPEAKERS: HassKey[SpeakerRegistry] = HassKey(f"{DOMAIN}_speakers")
MEMORY: HassKey[MemoryRegistry] = HassKey(f"{DOMAIN}_memory")

STOP_INTENTS = ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NoIntent")

# Echoes already pointed out as unmapped, so the hint is logged once per run
# and every later request costs nothing but a set lookup.
_noted_devices: set[str] = set()


@callback
def async_setup_view(hass: HomeAssistant) -> None:
    """Register the skill endpoint once per Home Assistant run.

    Views cannot be unregistered, so the handler reads the options on every
    request instead and refuses to answer while the entry is not loaded.
    """
    if hass.data.get(VIEW_REGISTERED):
        return
    hass.data[VIEW_REGISTERED] = True
    hass.http.register_view(AlexaSkillView)
    LOGGER.debug("Registered the Alexa skill endpoint at %s", ENDPOINT)


@callback
def _async_options(hass: HomeAssistant) -> dict[str, Any] | None:
    """Return the options of the entry that serves the skill, if it is loaded."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return dict(entries[0].options) if entries else None


def _phrase(locale: str | None, key: str) -> str:
    """Return a fixed phrase in the language of the Echo."""
    code = (locale or "en").split("-")[0].lower()
    return PHRASES.get(code, PHRASES["en"])[key]


def _output_speech(speech: str) -> dict[str, str]:
    """Wrap speech for Alexa, as SSML when it carries markup."""
    if speech.startswith("<speak>"):
        return {"type": "SSML", "ssml": speech}
    return {"type": "PlainText", "text": speech}


def _earcon(sound: str) -> str:
    """Return SSML that plays one sound and says nothing."""
    return f'<speak><audio src="{escape(sound, {'"': "&quot;"})}"/></speak>'


def _response(
    speech: str | None,
    *,
    end_session: bool = True,
    reprompt: str | None = None,
) -> dict[str, Any]:
    """Build a skill response."""
    response: dict[str, Any] = {"shouldEndSession": end_session}
    if speech:
        response["outputSpeech"] = _output_speech(speech)
    if reprompt:
        response["reprompt"] = {"outputSpeech": _output_speech(reprompt)}

    return {"version": "1.0", "response": response}


class AlexaSkillView(HomeAssistantView):
    """Serve the Alexa custom skill.

    Unauthenticated by necessity: Alexa cannot send a bearer token. Every
    request is instead checked against Amazon's signature and the skill id.
    """

    url = ENDPOINT
    name = f"api:{DOMAIN}:alexa"
    requires_auth = False

    async def post(self, request: web.Request) -> web.StreamResponse:
        """Handle one request from Alexa."""
        hass = request.app[KEY_HASS]

        if (options := _async_options(hass)) is None:
            # Not set up: nothing to tell an unexpected caller about.
            return self.json_message("Not found", HTTPStatus.NOT_FOUND)

        body = await request.read()

        if options.get(CONF_VERIFY_SIGNATURE, RECOMMENDED_VERIFY_SIGNATURE):
            try:
                await async_verify_request(hass, request.headers, body)
            except AlexaAuthError as err:
                LOGGER.warning("Rejected a request to the Alexa endpoint: %s", err)
                return self.json_message("Forbidden", HTTPStatus.FORBIDDEN)

        try:
            message: dict[str, Any] = json.loads(body)
        except ValueError as err:
            LOGGER.warning("Alexa sent a body that is not JSON: %s", err)
            return self.json_message("Bad request", HTTPStatus.BAD_REQUEST)

        if not isinstance(message, dict):
            return self.json_message("Bad request", HTTPStatus.BAD_REQUEST)

        if (skill_id := options.get(CONF_SKILL_ID)) and (_application_id(message) != skill_id):
            LOGGER.warning(
                "Rejected a request for skill %s, expected %s",
                _application_id(message),
                skill_id,
            )
            return self.json_message("Forbidden", HTTPStatus.FORBIDDEN)

        try:
            verify_timestamp(message.get("request", {}).get("timestamp"))
        except AlexaAuthError as err:
            LOGGER.warning("Rejected a request to the Alexa endpoint: %s", err)
            return self.json_message("Forbidden", HTTPStatus.FORBIDDEN)

        return self.json(await _async_handle(hass, options, message))


@callback
def _application_id(message: dict[str, Any]) -> str | None:
    """Return the skill id the request claims to be for."""
    for parent in (message.get("context", {}).get("System", {}), message.get("session", {})):
        if application_id := parent.get("application", {}).get("applicationId"):
            return str(application_id)
    return None


async def _async_handle(
    hass: HomeAssistant, options: dict[str, Any], message: dict[str, Any]
) -> dict[str, Any]:
    """Turn one Alexa request into a response.

    Every answer closes the session. Only the greeting of "Alexa, open <skill>" leaves the
    microphone open, because otherwise there would be no way to ask the one
    question it is waiting for. Anything after that means invoking the skill
    again.
    """
    request = message.get("request", {})
    request_type = request.get("type")
    locale = request.get("locale")
    _async_note_device(hass, options, message)

    if request_type == "LaunchRequest":
        # A short sound beats a spoken greeting here: it is the cue to start
        # talking, and every tenth of a second of it is a tenth of a second the
        # user spends waiting. Configure it away to go back to a phrase.
        sound = options.get(CONF_LAUNCH_SOUND, RECOMMENDED_LAUNCH_SOUND)
        greeting = _earcon(sound) if sound else _phrase(locale, "launch")
        return _response(greeting, end_session=False, reprompt=greeting)

    if request_type == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}

    if request_type != "IntentRequest":
        LOGGER.debug("Ignoring an Alexa request of type %s", request_type)
        return _response(None)

    intent = request.get("intent", {})
    name = intent.get("name")
    spoken_answer = ""

    if name in ("AMAZON.YesIntent", "AMAZON.NoIntent"):
        # The answer to "Soll ich mir das merken?". Without a note waiting, a
        # "no" is a goodbye like before and a "yes" has nothing to confirm.
        reply = await _async_answer_pending(hass, message, yes=name == "AMAZON.YesIntent")
        if reply:
            return _response(reply)
        # No note is waiting, so this answers the assistant's own question
        # ("Soll ich sie schliessen?"). It goes to the assistant as plain text.
        spoken_answer = "Ja" if name == "AMAZON.YesIntent" else "Nein"

    if name in STOP_INTENTS:
        return _response(_phrase(locale, "goodbye"))

    if name == "AMAZON.HelpIntent":
        return _response(_phrase(locale, "help"))

    if name == "AMAZON.NavigateHomeIntent":
        return _response(None)

    query = spoken_answer
    if name == QUERY_INTENT:
        query = (intent.get("slots", {}).get(QUERY_SLOT, {}).get("value") or "").strip()

    if not query:
        # An unmapped intent or an empty slot both mean Alexa understood the
        # words but we have nothing to send to the assistant.
        LOGGER.debug("Alexa sent intent %s with no usable text", name)
        return _response(_phrase(locale, "not_understood"))

    speaker = await _async_identify(hass, message)

    memory = hass.data.get(MEMORY)
    if memory is not None and speaker.known and speaker.entity_id:
        # A bare "ja" or "nein" caught as free text answers a note waiting for it.
        if (reply := await memory.async_resolve_text(speaker.entity_id, query)) is not None:
            return _response(reply)

    speech = await _async_answer(hass, options, message, query, locale, speaker)

    if memory is not None and speaker.entity_id and memory.has_pending(speaker.entity_id):
        # The assistant just proposed a note: keep the microphone open for the answer.
        question = _phrase(locale, "confirm_reprompt")
        return _response(speech, end_session=False, reprompt=question)
    if speech.rstrip().endswith("?"):
        # The assistant asked something: keep the microphone open for the answer,
        # also for the confirmation before a door opens.
        return _response(speech, end_session=False, reprompt=speech)
    return _response(speech)


async def _async_answer_pending(
    hass: HomeAssistant, message: dict[str, Any], *, yes: bool
) -> str | None:
    """Apply a spoken yes or no to the note the speaker was asked about."""
    memory = hass.data.get(MEMORY)
    if memory is None:
        return None
    speaker = await _async_identify(hass, message)
    if not speaker.known or not speaker.entity_id:
        return None
    return await memory.async_answer(speaker.entity_id, yes=yes)


async def _async_identify(hass: HomeAssistant, message: dict[str, Any]) -> Speaker:
    """Work out who is speaking, and say so on the event bus.

    The event carries no text, only who was heard, so automations can react to
    a person without the words they said ever leaving the request.
    """
    registry = hass.data.get(SPEAKERS)
    if registry is None:
        return UNKNOWN
    names = {state.entity_id: state.name for state in hass.states.async_all("person")}
    speaker = await registry.async_identify(message, names)
    registry.note_speaker(speaker)
    if speaker.person_id is not None and not speaker.known:
        LOGGER.warning(
            "Alexa recognised a voice that is not assigned to a person yet: %s. Call "
            "the service %s.enroll_speaker for the person and let them say something",
            speaker.person_id,
            DOMAIN,
        )
    hass.bus.async_fire(
        f"{DOMAIN}_speaker",
        {
            "known": speaker.known,
            "person": speaker.entity_id,
            "name": speaker.name,
            "trusted": speaker.trusted,
            "voice_id": speaker.person_id,
        },
    )
    return speaker


async def _async_answer(
    hass: HomeAssistant,
    options: dict[str, Any],
    message: dict[str, Any],
    query: str,
    locale: str | None,
    speaker: Speaker,
) -> str:
    """Run the assistant and return what Alexa should say.

    The turn is never cancelled: an answer that misses the deadline is still
    worth having, it just has to arrive on the Echo instead of in the response.
    """
    deadline = float(options.get(CONF_DEADLINE, RECOMMENDED_DEADLINE))

    task = hass.async_create_task(
        _async_run_assistant(hass, options, message, query, speaker),
        name=f"{DOMAIN}_alexa_converse",
    )

    done, _ = await asyncio.wait({task}, timeout=deadline)
    if not done:
        return _async_hand_over(hass, options, message, task, locale)

    try:
        speech = task.result()
    except HomeAssistantError as err:
        LOGGER.error("The assistant failed on a request from Alexa: %s", err)
        return _phrase(locale, "error")
    except Exception:
        LOGGER.exception("Unexpected error on a request from Alexa")
        return _phrase(locale, "error")

    return speech or _phrase(locale, "empty")


async def _async_run_assistant(
    hass: HomeAssistant,
    options: dict[str, Any],
    message: dict[str, Any],
    query: str,
    speaker: Speaker,
) -> str:
    """Run the text through the assistant's intent stage and return its speech.

    Going through the assistant rather than straight to its agent is what makes
    its own settings apply ("Prefer handling commands locally" above all) and
    what puts the run in the assistant's debug view. Alexa already did the
    speech, so the run starts and ends at the intent stage.
    """
    assistant = options.get(CONF_ASSISTANT)
    try:
        pipeline = async_get_pipeline(
            hass, None if assistant in (None, "", OPTION_PREFERRED) else assistant
        )
    except PipelineNotFound as err:
        raise HomeAssistantError(
            f"The assistant Alexa answers with is gone, pick another one: {err}"
        ) from err

    events: list[PipelineEvent] = []
    with chat_session.async_get_chat_session(
        hass, _conversation_id(options, message, speaker)
    ) as session:
        await PipelineInput(
            run=PipelineRun(
                hass,
                context=Context(),
                pipeline=pipeline,
                start_stage=PipelineStage.INTENT,
                end_stage=PipelineStage.INTENT,
                event_callback=events.append,
            ),
            session=session,
            intent_input=query,
            conversation_extra_system_prompt=_extra_prompt(hass, speaker),
            device_id=_async_echo_device(hass, message),
        ).execute(validate=True)

    for event in events:
        data = event.data or {}
        if event.type is PipelineEventType.ERROR:
            raise HomeAssistantError(data.get("message") or data.get("code"))
        if event.type is PipelineEventType.INTENT_END:
            response = data.get("intent_output", {}).get("response", {})
            return str(response.get("speech", {}).get("plain", {}).get("speech") or "")
    return ""


@callback
def _extra_prompt(hass: HomeAssistant, speaker: Speaker) -> str:
    """Who is speaking, plus what Jarvis has been told to remember."""
    parts = [speaker_prompt(speaker)]
    memory = hass.data.get(MEMORY)
    if memory is not None and (notes := memory.prompt(speaker.entity_id, speaker.name)):
        parts.append(notes)
    return "\n\n".join(parts)


@callback
def _conversation_id(
    options: dict[str, Any], message: dict[str, Any], speaker: Speaker | None = None
) -> str | None:
    """Return the Home Assistant conversation this turn belongs to.

    Neither id is a ULID, which is what makes Home Assistant keep ours instead
    of generating a fresh one.

    Keyed on the Echo, consecutive questions continue one conversation and a
    follow-up knows what it refers to. Home Assistant drops a session after five
    idle minutes, so the thread also resets itself without anything to clean up.
    Keyed on the Alexa session instead, every invocation starts empty.

    Alexa's ids run past 200 characters and agents pass the conversation id on
    to their provider (OpenAI caps its safety_identifier at 128), so the id is
    hashed down to a short one that never leaves Amazon's id behind.
    """
    session_id = message.get("session", {}).get("sessionId")
    if not options.get(CONF_REMEMBER, RECOMMENDED_REMEMBER):
        return _short_id(session_id)
    device_id = message.get("context", {}).get("System", {}).get("device", {}).get("deviceId")
    # Two people at one Echo must not share a thread: the second would inherit
    # the first one's context (and, worse, their idea of who is speaking).
    voice = speaker.person_id if speaker else None
    key = f"{device_id}|{voice}" if device_id and voice else device_id
    # Simulated requests carry no device, so they fall back to the session.
    return _short_id(key or session_id)


def _short_id(alexa_id: str | None) -> str | None:
    """Return a short, stable conversation id for an Alexa id."""
    if not alexa_id:
        return None
    return f"alexa-{hashlib.sha256(alexa_id.encode()).hexdigest()[:32]}"


@callback
def _async_hand_over(
    hass: HomeAssistant,
    options: dict[str, Any],
    message: dict[str, Any],
    task: asyncio.Task[str],
    locale: str | None,
) -> str:
    """Arrange for a late answer to be spoken, and say so.

    Returns the phrase to use for the response that is about to be sent.
    """
    target: str | None = None
    if options.get(CONF_ANNOUNCE_LATE, RECOMMENDED_ANNOUNCE_LATE):
        target = _async_late_target(hass, message)

    def _finished(finished: asyncio.Future[str]) -> None:
        """Announce the answer, and keep the task's outcome from being lost."""
        try:
            speech = finished.result()
        except asyncio.CancelledError:
            return
        except HomeAssistantError as err:
            LOGGER.error("The assistant failed on a late request from Alexa: %s", err)
            return
        except Exception:
            LOGGER.exception("Unexpected error on a late request from Alexa")
            return

        if target is None or not speech:
            LOGGER.info("Dropped a late answer from Alexa: %s", speech if target else "no target")
            return

        LOGGER.debug("Announcing a late answer on %s", target)
        hass.async_create_task(
            hass.services.async_call(
                "notify",
                "send_message",
                {"entity_id": target, "message": speech},
                blocking=False,
            ),
            name=f"{DOMAIN}_alexa_announce",
        )

    task.add_done_callback(_finished)
    return _phrase(locale, "late" if target else "late_dropped")


@callback
def _device_id(message: dict[str, Any]) -> str | None:
    """Return the skill-scoped id of the Echo the request came from."""
    return message.get("context", {}).get("System", {}).get("device", {}).get("deviceId")


@callback
def _async_note_device(
    hass: HomeAssistant, options: dict[str, Any], message: dict[str, Any]
) -> None:
    """Point out an Echo that has nothing to announce late answers on.

    Without this the id only ever surfaces when an answer has already been
    dropped, which means provoking a slow question before the Echo can be
    mapped at all. Asking it anything is enough to put the id in the log.
    """
    if not (device_id := _device_id(message)) or device_id in _noted_devices:
        return
    _noted_devices.add(device_id)

    if not options.get(CONF_ANNOUNCE_LATE, RECOMMENDED_ANNOUNCE_LATE):
        return
    if _async_notify_target(hass, device_id) is not None:
        return

    # A warning rather than info: an instance with no log file still keeps
    # these in the system log, which is where Home Assistant surfaces them.
    LOGGER.warning(
        "The Echo that just spoke is not mapped to anything, so a late answer "
        "would be dropped. Add the label %s to that Echo's device in Home "
        "Assistant (Settings > Devices & Services > Devices) to hear late "
        "answers on it",
        device_id,
    )


@callback
def _async_late_target(hass: HomeAssistant, message: dict[str, Any]) -> str | None:
    """Return the notify entity that speaks on the Echo that asked."""
    if not (device_id := _device_id(message)):
        return None

    if (target := _async_notify_target(hass, device_id)) is None:
        LOGGER.warning(
            "Nothing is mapped to the Echo labelled %s, so this answer is lost. "
            "Add that id as a label on the Echo's device in Home Assistant",
            device_id,
        )
    return target


@callback
def _async_echo_device(hass: HomeAssistant, message: dict[str, Any]) -> str | None:
    """Return the Home Assistant device of the Echo that asked, if it is mapped.

    Handing it to the assistant is what makes "turn off the light" mean the
    light in the Echo's area, as it would for a voice satellite.
    """
    if not (alexa_id := _device_id(message)):
        return None
    if (label := lr.async_get(hass).async_get_label_by_name(alexa_id)) is None:
        return None
    # Only a label on a device counts: one on a notify entity redirects late
    # answers (to a phone, say), which says nothing about where the Echo is.
    devices = dr.async_entries_for_label(dr.async_get(hass), label.label_id)
    return devices[0].id if devices else None


@callback
def _async_notify_target(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the notify entity mapped to an Alexa device id, if any.

    Alexa hands out a skill-scoped device id that has nothing in common with
    the serial numbers behind the alexa_devices integration, so the two are
    tied together by hand: the id goes on the Echo as a Home Assistant label.
    """
    if (label := lr.async_get(hass).async_get_label_by_name(device_id)) is None:
        return None

    entity_registry = er.async_get(hass)

    # The label can sit on a notify entity directly, which is the escape hatch
    # for sending somewhere other than the Echo.
    for entry in er.async_entries_for_label(entity_registry, label.label_id):
        if entry.domain == "notify":
            return entry.entity_id

    candidates: dict[str | None, str] = {}
    for device in dr.async_entries_for_label(dr.async_get(hass), label.label_id):
        for entry in er.async_entries_for_device(entity_registry, device.id):
            if entry.domain == "notify" and entry.platform == "alexa_devices":
                candidates[entry.translation_key] = entry.entity_id

    # "announce" interrupts with the announcement chime, "speak" just talks;
    # announce is the one that reads as an answer arriving.
    return candidates.get("announce") or candidates.get("speak")
