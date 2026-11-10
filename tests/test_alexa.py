"""Tests for the Alexa custom skill endpoint."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from custom_components.jarvis_voice.alexa import _short_id
from custom_components.jarvis_voice.alexa_auth import (
    AlexaAuthError,
    _validate_cert_url,
    _verify_signature,
    verify_timestamp,
)
from custom_components.jarvis_voice.const import (
    CONF_LAUNCH_SOUND,
    CONF_REMEMBER,
    CONF_VERIFY_SIGNATURE,
    ENDPOINT,
    PHRASES,
    QUERY_INTENT,
    QUERY_SLOT,
    RECOMMENDED_LAUNCH_SOUND,
)
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import AGENT_ID, SKILL_ID

# Real Alexa device ids are about this long, far past what OpenAI accepts.
DEVICE_ID = "amzn1.ask.device." + "A" * 208
SESSION_ID = "amzn1.echo-api.session.abc-123"

IT = PHRASES["it"]


def utcnow_isoformat() -> str:
    """Return a timestamp in the format Alexa stamps its requests with."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request(
    body: dict[str, Any],
    *,
    session_attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a request body in the envelope Alexa sends."""
    session: dict[str, Any] = {
        "sessionId": SESSION_ID,
        "application": {"applicationId": SKILL_ID},
    }
    if session_attributes is not None:
        session["attributes"] = session_attributes
    return {
        "version": "1.0",
        "session": session,
        "context": {
            "System": {
                "application": {"applicationId": SKILL_ID},
                "device": {"deviceId": DEVICE_ID},
                "apiEndpoint": "https://api.eu.amazonalexa.com",
                "apiAccessToken": "token",
            }
        },
        "request": {"timestamp": utcnow_isoformat(), "requestId": "req-1", **body},
    }


def _launch() -> dict[str, Any]:
    return _request({"type": "LaunchRequest", "locale": "it-IT"})


def _query(text: str, **kwargs: Any) -> dict[str, Any]:
    return _request(
        {
            "type": "IntentRequest",
            "locale": "it-IT",
            "intent": {
                "name": QUERY_INTENT,
                "slots": {QUERY_SLOT: {"name": QUERY_SLOT, "value": text}},
            },
        },
        **kwargs,
    )


@pytest.fixture
async def alexa_entry(init_integration: MockConfigEntry) -> MockConfigEntry:
    """The loaded entry, which serves the skill."""
    return init_integration


def _map_echo(hass: HomeAssistant) -> None:
    """Create an Echo as the Amazon Devices integration would, and label it."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    echo_entry = MockConfigEntry(domain="alexa_devices")
    echo_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=echo_entry.entry_id,
        identifiers={("alexa_devices", "G000TEST")},
    )
    # What the user does by hand: the id Alexa sends becomes a label on the Echo.
    label = lr.async_get(hass).async_create(DEVICE_ID)
    device_registry.async_update_device(device.id, labels={label.label_id})
    entity_registry.async_get_or_create(
        "notify",
        "alexa_devices",
        "G000TEST-announce",
        suggested_object_id="kitchen_announce",
        device_id=device.id,
        translation_key="announce",
    )


def _answer(text: str, delay: float = 0.0) -> Any:
    """Return a fake async_converse that answers after a delay."""

    async def _converse(hass: HomeAssistant, *args: Any, **kwargs: Any) -> Any:
        if delay:
            await asyncio.sleep(delay)
        response = intent.IntentResponse(language="it-IT")
        response.async_set_speech(text)
        return conversation.ConversationResult(
            response=response, conversation_id=kwargs.get("conversation_id")
        )

    return _converse


async def test_endpoint_is_absent_once_unloaded(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """With the entry unloaded the endpoint tells a caller nothing."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    client = await hass_client_no_auth()
    response = await client.post(ENDPOINT, json=_launch())
    assert response.status == HTTPStatus.NOT_FOUND


async def test_launch_plays_a_sound_and_listens(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Opening the skill plays an earcon rather than saying anything.

    The greeting is pure waiting time, so it is a sound the user can talk over
    the end of instead of a sentence they have to sit through.
    """
    client = await hass_client_no_auth()
    response = await client.post(ENDPOINT, json=_launch())

    assert response.status == HTTPStatus.OK
    payload = await response.json()
    speech = payload["response"]["outputSpeech"]
    assert speech["type"] == "SSML"
    assert speech["ssml"] == (f'<speak><audio src="{RECOMMENDED_LAUNCH_SOUND}"/></speak>')
    assert "text" not in speech
    assert payload["response"]["shouldEndSession"] is False
    # Silence brings the same cue back, not a spoken nudge.
    assert payload["response"]["reprompt"]["outputSpeech"]["ssml"] == speech["ssml"]
    # No session state to carry: the answer that follows will close the session.
    assert "sessionAttributes" not in payload


async def test_clearing_the_sound_brings_the_spoken_greeting_back(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """An empty sound falls back to the phrase."""
    hass.config_entries.async_update_entry(
        alexa_entry, options={**alexa_entry.options, CONF_LAUNCH_SOUND: ""}
    )
    await hass.async_block_till_done()

    client = await hass_client_no_auth()
    response = await client.post(ENDPOINT, json=_launch())

    payload = await response.json()
    assert payload["response"]["outputSpeech"]["type"] == "PlainText"
    assert payload["response"]["outputSpeech"]["text"] == IT["launch"]


async def test_a_sound_uri_cannot_break_out_of_the_ssml(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """The configured URI is escaped into the attribute."""
    hass.config_entries.async_update_entry(
        alexa_entry,
        options={
            **alexa_entry.options,
            CONF_LAUNCH_SOUND: 'https://x/a.mp3?a=1&b=2"/><speak>boo',
        },
    )
    await hass.async_block_till_done()

    client = await hass_client_no_auth()
    response = await client.post(ENDPOINT, json=_launch())

    ssml = (await response.json())["response"]["outputSpeech"]["ssml"]
    assert ssml.count("<speak>") == 1
    assert "&amp;" in ssml
    assert "&quot;" in ssml


async def test_one_shot_question_answers_and_hangs_up(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """A one-shot question ("Alexa, ask <skill> ...") answers once."""
    client = await hass_client_no_auth()
    with patch(
        "homeassistant.components.conversation.async_converse",
        new=_answer("Sono le tre."),
    ):
        response = await client.post(ENDPOINT, json=_query("che ore sono"))

    payload = await response.json()
    assert payload["response"]["outputSpeech"]["text"] == "Sono le tre."
    assert payload["response"]["shouldEndSession"] is True
    assert "sessionAttributes" not in payload


async def test_an_answer_always_ends_the_session(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Answering inside an opened session still hangs up.

    Asking again means invoking the skill again: no "Dimmi" follow-up, and no
    open microphone left behind.
    """
    client = await hass_client_no_auth()
    with patch(
        "homeassistant.components.conversation.async_converse",
        new=_answer("Fatto."),
    ):
        response = await client.post(
            ENDPOINT,
            json=_query("accendi la luce", session_attributes={"dialog": True}),
        )

    payload = await response.json()
    assert payload["response"]["outputSpeech"]["text"] == "Fatto."
    assert payload["response"]["shouldEndSession"] is True
    assert "reprompt" not in payload["response"]
    assert "sessionAttributes" not in payload


async def test_nothing_understood_also_ends_the_session(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Even "non ho capito" hangs up rather than waiting for a retry."""
    client = await hass_client_no_auth()
    response = await client.post(
        ENDPOINT,
        json=_request(
            {
                "type": "IntentRequest",
                "locale": "it-IT",
                "intent": {"name": "AMAZON.FallbackIntent"},
            },
            session_attributes={"dialog": True},
        ),
    )

    payload = await response.json()
    assert payload["response"]["outputSpeech"]["text"] == IT["not_understood"]
    assert payload["response"]["shouldEndSession"] is True
    assert "reprompt" not in payload["response"]


async def test_questions_from_one_echo_share_a_conversation(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Two separate invocations of the same Echo continue one conversation.

    Each invocation is its own Alexa session, so keying on the device is what
    lets a follow-up refer back. Neither id is a ULID, which is what makes Home
    Assistant keep ours instead of generating a fresh one.
    """
    seen: list[dict[str, Any]] = []
    answer = _answer("Fatto.")

    async def _capture(hass: HomeAssistant, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return await answer(hass, *args, **kwargs)

    first = _query("che tempo fa")
    second = _query("e domani")
    # A second invocation really is a new Alexa session.
    second["session"]["sessionId"] = "amzn1.echo-api.session.a-different-one"

    client = await hass_client_no_auth()
    with patch("homeassistant.components.conversation.async_converse", new=_capture):
        await client.post(ENDPOINT, json=first)
        await client.post(ENDPOINT, json=second)

    assert [call["conversation_id"] for call in seen] == [_short_id(DEVICE_ID)] * 2
    assert len(seen[0]["conversation_id"]) <= 64
    assert DEVICE_ID not in seen[0]["conversation_id"]
    # The assistant's language applies, as for any satellite, not the Echo's locale.
    assert [call["language"] for call in seen] == [hass.config.language] * 2
    assert seen[0]["agent_id"] == AGENT_ID


async def test_forgetting_falls_back_to_the_alexa_session(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """With remembering off, each invocation starts empty."""
    hass.config_entries.async_update_entry(
        alexa_entry, options={**alexa_entry.options, CONF_REMEMBER: False}
    )
    await hass.async_block_till_done()

    seen: list[dict[str, Any]] = []
    answer = _answer("Fatto.")

    async def _capture(hass: HomeAssistant, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return await answer(hass, *args, **kwargs)

    client = await hass_client_no_auth()
    with patch("homeassistant.components.conversation.async_converse", new=_capture):
        await client.post(ENDPOINT, json=_query("che ore sono"))

    assert seen[0]["conversation_id"] == _short_id(SESSION_ID)


async def test_a_request_without_a_device_uses_the_session(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Simulated requests carry no device id and must still work."""
    body = _query("che ore sono")
    del body["context"]["System"]["device"]

    seen: list[dict[str, Any]] = []
    answer = _answer("Fatto.")

    async def _capture(hass: HomeAssistant, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return await answer(hass, *args, **kwargs)

    client = await hass_client_no_auth()
    with patch("homeassistant.components.conversation.async_converse", new=_capture):
        await client.post(ENDPOINT, json=body)

    assert seen[0]["conversation_id"] == _short_id(SESSION_ID)


async def test_stop_intent_says_goodbye(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Stop ends the session."""
    client = await hass_client_no_auth()
    response = await client.post(
        ENDPOINT,
        json=_request(
            {
                "type": "IntentRequest",
                "locale": "it-IT",
                "intent": {"name": "AMAZON.StopIntent"},
            }
        ),
    )

    payload = await response.json()
    assert payload["response"]["outputSpeech"]["text"] == IT["goodbye"]
    assert payload["response"]["shouldEndSession"] is True


async def test_unknown_intent_is_not_sent_to_the_agent(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """An intent without usable text never reaches the agent."""
    client = await hass_client_no_auth()
    with patch("homeassistant.components.conversation.async_converse") as converse:
        response = await client.post(
            ENDPOINT,
            json=_request(
                {
                    "type": "IntentRequest",
                    "locale": "it-IT",
                    "intent": {"name": "AMAZON.FallbackIntent"},
                }
            ),
        )

    converse.assert_not_called()
    payload = await response.json()
    assert payload["response"]["outputSpeech"]["text"] == IT["not_understood"]


async def test_a_request_for_another_skill_is_refused(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """The skill id is what identifies the caller once signatures are off."""
    body = _launch()
    body["context"]["System"]["application"]["applicationId"] = "amzn1.ask.skill.other"
    body["session"]["application"]["applicationId"] = "amzn1.ask.skill.other"

    client = await hass_client_no_auth()
    response = await client.post(ENDPOINT, json=body)
    assert response.status == HTTPStatus.FORBIDDEN


async def test_a_stale_request_is_refused(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """A replayed request is outside the timestamp tolerance."""
    body = _launch()
    body["request"]["timestamp"] = "2020-01-01T00:00:00Z"

    client = await hass_client_no_auth()
    response = await client.post(ENDPOINT, json=body)
    assert response.status == HTTPStatus.FORBIDDEN


async def test_an_unsigned_request_is_refused(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """With verification on, an unsigned body does not get through."""
    hass.config_entries.async_update_entry(
        alexa_entry,
        options={**alexa_entry.options, CONF_VERIFY_SIGNATURE: True},
    )
    await hass.async_block_till_done()

    client = await hass_client_no_auth()
    response = await client.post(ENDPOINT, json=_launch())
    assert response.status == HTTPStatus.FORBIDDEN


async def test_an_unmapped_echo_is_named_in_the_log_once(
    hass: HomeAssistant,
    alexa_entry: MockConfigEntry,
    hass_client_no_auth: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any question surfaces the id needed to map the Echo.

    Otherwise the id only appears once an answer has already been dropped, so
    you would have to provoke a slow question before you could map it. It is a
    warning because an instance with no log file still keeps those.
    """
    from custom_components.jarvis_voice import alexa as alexa_module

    alexa_module._noted_devices.clear()

    client = await hass_client_no_auth()
    with caplog.at_level(logging.WARNING, logger="custom_components.jarvis_voice"):
        with patch(
            "homeassistant.components.conversation.async_converse",
            new=_answer("Fatto."),
        ):
            await client.post(ENDPOINT, json=_query("accendi la luce"))
            await client.post(ENDPOINT, json=_query("spegni la luce"))

    hints = [r for r in caplog.records if "not mapped to anything" in r.message]
    assert len(hints) == 1
    assert hints[0].levelno == logging.WARNING
    assert DEVICE_ID in hints[0].getMessage()


async def test_a_mapped_echo_is_not_mentioned(
    hass: HomeAssistant,
    alexa_entry: MockConfigEntry,
    hass_client_no_auth: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Once the label exists there is nothing to point out."""
    from custom_components.jarvis_voice import alexa as alexa_module

    alexa_module._noted_devices.clear()
    _map_echo(hass)

    client = await hass_client_no_auth()
    with caplog.at_level(logging.WARNING, logger="custom_components.jarvis_voice"):
        with patch(
            "homeassistant.components.conversation.async_converse",
            new=_answer("Fatto."),
        ):
            await client.post(ENDPOINT, json=_query("accendi la luce"))

    assert not [r for r in caplog.records if "not mapped to anything" in r.message]


async def test_a_late_answer_is_announced_on_the_echo_that_asked(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """Past the deadline the answer follows on the Echo, not in the response."""
    _map_echo(hass)

    calls: list[dict[str, Any]] = []

    async def _record(call: Any) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("notify", "send_message", _record)

    client = await hass_client_no_auth()
    with patch(
        "homeassistant.components.conversation.async_converse",
        new=_answer("Domani sereno.", delay=0.6),
    ):
        response = await client.post(ENDPOINT, json=_query("che tempo fa domani"))
        payload = await response.json()
        # Answered inside the budget with a promise, not with the answer.
        assert payload["response"]["outputSpeech"]["text"] == IT["late"]

        await hass.async_block_till_done()

    assert calls == [{"entity_id": "notify.kitchen_announce", "message": "Domani sereno."}]


async def test_a_late_answer_without_a_mapped_echo_says_so(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """With nowhere to send it, the answer is dropped and the phrase differs."""
    client = await hass_client_no_auth()
    with patch(
        "homeassistant.components.conversation.async_converse",
        new=_answer("Troppo tardi.", delay=0.6),
    ):
        response = await client.post(ENDPOINT, json=_query("che tempo fa domani"))
        payload = await response.json()
        await hass.async_block_till_done()

    assert payload["response"]["outputSpeech"]["text"] == IT["late_dropped"]


async def test_an_agent_failure_is_spoken(
    hass: HomeAssistant, alexa_entry: MockConfigEntry, hass_client_no_auth: Any
) -> None:
    """A broken turn says something rather than making Alexa chime an error."""

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise HomeAssistantError("no model")

    client = await hass_client_no_auth()
    with patch("homeassistant.components.conversation.async_converse", new=_boom):
        response = await client.post(ENDPOINT, json=_query("che ore sono"))

    payload = await response.json()
    assert payload["response"]["outputSpeech"]["text"] == IT["error"]


@pytest.mark.parametrize(
    "url",
    [
        "http://s3.amazonaws.com/echo.api/cert",
        "https://s3.amazonaws.com/badpath/cert",
        "https://notamazon.com/echo.api/cert",
        "https://s3.amazonaws.com:8443/echo.api/cert",
        "https://s3.amazonaws.com/echo.api/../evil/cert",
    ],
)
def test_bad_certificate_urls_are_rejected(url: str) -> None:
    """Only Amazon's own bucket may serve the signing certificate."""
    with pytest.raises(AlexaAuthError):
        _validate_cert_url(url)


def test_a_good_certificate_url_passes() -> None:
    """The documented shape is accepted."""
    _validate_cert_url("https://s3.amazonaws.com/echo.api/echo-api-cert.pem")


def test_timestamps_outside_the_tolerance_are_rejected() -> None:
    """Old, missing and malformed timestamps all fail."""
    verify_timestamp(utcnow_isoformat())
    for bad in (None, "", "not-a-date", "2020-01-01T00:00:00Z"):
        with pytest.raises(AlexaAuthError):
            verify_timestamp(bad)


def _self_signed() -> tuple[Any, Any]:
    """Return a private key and a certificate carrying its public key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "echo-api.amazon.com")])
    now = datetime.now(tz=UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def test_a_body_signed_by_the_certificate_is_accepted() -> None:
    """The happy path of the signature check."""
    key, certificate = _self_signed()
    body = b'{"request": {"type": "LaunchRequest"}}'
    signature = key.sign(body, padding.PKCS1v15(), hashes.SHA256())

    _verify_signature(certificate, signature, body)


def test_a_tampered_body_is_rejected() -> None:
    """Changing one byte of the body invalidates the signature."""
    key, certificate = _self_signed()
    body = b'{"text": "accendi la luce"}'
    signature = key.sign(body, padding.PKCS1v15(), hashes.SHA256())

    with pytest.raises(AlexaAuthError):
        _verify_signature(certificate, signature, body.replace(b"accendi", b"spegni_"))


def test_a_signature_from_another_key_is_rejected() -> None:
    """A caller cannot sign with a key of their own."""
    _, certificate = _self_signed()
    other, _ = _self_signed()
    body = b'{"request": {"type": "LaunchRequest"}}'

    with pytest.raises(AlexaAuthError):
        _verify_signature(certificate, other.sign(body, padding.PKCS1v15(), hashes.SHA256()), body)
