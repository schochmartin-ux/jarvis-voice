"""Who is speaking: Alexa Voice ID turned into a Home Assistant person.

Alexa recognises the voice of an enrolled household member on the Echo itself
and, for a skill that asks for it, puts a skill-scoped ``personId`` into every
request (``context.System.person``). The id says nothing about *who* it is, so
this module keeps the mapping from that id to a Home Assistant person and turns
it into a short German hint for the assistant: the name, how to address the
person and whether the person may do security relevant things.

Nothing here talks to Home Assistant, so it can be tested without it. The
storage is passed in as a small object with ``async_load`` and ``async_save``,
which is exactly what ``homeassistant.helpers.storage.Store`` offers.

Voice ID is a convenience, not a lock: a recording of a voice may be accepted.
The hint therefore only ever widens what the assistant is *allowed* to do for
people who are enrolled as trusted, and never grants anything by itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

# Alexa reports how sure it is: below 400 means "recognised by voice", 400 means
# voice plus PIN. Anything we are told about is therefore at least a voice match.
CONFIDENCE_VOICE = 100
CONFIDENCE_PIN = 400

DEFAULT_ENROLL_MINUTES = 30

# A fixed form of address for the parents; everybody else is addressed by their
# name in Home Assistant. Everyone in the family may open doors and gates by voice
# (after a question back); a voice that is not assigned to a person never may.
DEFAULT_ADDRESSES = {
    "person.martin": "Chef",
    "person.pati": "Chefin",
}
DEFAULT_TRUSTED = (
    "person.martin",
    "person.pati",
    "person.julia",
    "person.lena",
    "person.tessa",
)


class _Store(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class Speaker:
    """The outcome of looking at one request."""

    person_id: str | None  # Alexa's id, None when Alexa did not recognise anyone
    entity_id: str | None  # the Home Assistant person, None when not mapped
    name: str | None
    address: str | None
    trusted: bool
    confidence: int | None

    @property
    def known(self) -> bool:
        """True when a Home Assistant person is behind the voice."""
        return self.entity_id is not None


UNKNOWN = Speaker(None, None, None, None, False, None)


def extract_person(message: dict[str, Any]) -> tuple[str | None, int | None]:
    """Return Alexa's ``personId`` and its confidence from a skill request.

    Alexa leaves the ``person`` object out when it did not recognise the voice,
    or when the speaker has not allowed skills to personalise. Both look the same
    from here, and both mean "unknown".
    """
    person = message.get("context", {}).get("System", {}).get("person")
    if not isinstance(person, dict):
        return None, None
    person_id = person.get("personId")
    if not isinstance(person_id, str) or not person_id:
        return None, None
    level = person.get("authenticationConfidenceLevel", {})
    confidence = level.get("level") if isinstance(level, dict) else None
    return person_id, confidence if isinstance(confidence, int) else CONFIDENCE_VOICE


class SpeakerRegistry:
    """Maps Alexa person ids to Home Assistant persons, and remembers them."""

    def __init__(self, store: _Store, *, clock: Any = time.monotonic) -> None:
        self._store = store
        self._clock = clock
        self._persons: dict[str, str] = {}  # personId -> person entity_id
        self._addresses: dict[str, str] = dict(DEFAULT_ADDRESSES)
        self._trusted: set[str] = set(DEFAULT_TRUSTED)
        self._pending: tuple[str, float] | None = None  # (entity_id, expires)
        self._unmapped_seen: dict[str, float] = {}
        self._last: tuple[str, str, float] | None = None  # (entity_id, name, when)

    # -- persistence -------------------------------------------------------

    async def async_load(self) -> None:
        """Read what was stored, keeping the defaults for anything missing."""
        data = await self._store.async_load() or {}
        self._persons = dict(data.get("persons", {}))
        self._addresses = {**DEFAULT_ADDRESSES, **data.get("addresses", {})}
        trusted = data.get("trusted")
        self._trusted = set(trusted) if trusted is not None else set(DEFAULT_TRUSTED)

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "persons": self._persons,
                "addresses": self._addresses,
                "trusted": sorted(self._trusted),
            }
        )

    # -- administration (called from services) -----------------------------

    def start_enrollment(self, entity_id: str, minutes: float = DEFAULT_ENROLL_MINUTES) -> None:
        """Assign the next voice nobody has claimed yet to this person."""
        self._pending = (entity_id, self._clock() + minutes * 60)

    def cancel_enrollment(self) -> None:
        """Stop waiting for a voice."""
        self._pending = None

    @property
    def pending(self) -> str | None:
        """The person currently waiting to be assigned a voice, if any."""
        if self._pending and self._clock() < self._pending[1]:
            return self._pending[0]
        self._pending = None
        return None

    async def async_forget(self, entity_id: str) -> int:
        """Drop every voice assigned to a person. Returns how many were dropped."""
        gone = [pid for pid, ent in self._persons.items() if ent == entity_id]
        for pid in gone:
            del self._persons[pid]
        if gone:
            await self._async_save()
        return len(gone)

    async def async_set_address(self, entity_id: str, address: str | None) -> None:
        """Set, or with ``None`` remove, how a person is addressed."""
        if address:
            self._addresses[entity_id] = address
        else:
            self._addresses.pop(entity_id, None)
        await self._async_save()

    async def async_set_trusted(self, entity_id: str, trusted: bool) -> None:
        """Allow or forbid a person to unlock doors and gates by voice."""
        (self._trusted.add if trusted else self._trusted.discard)(entity_id)
        await self._async_save()

    def assignments(self) -> dict[str, str]:
        """Return the current voice-to-person mapping (for diagnostics)."""
        return dict(self._persons)

    # -- lookup ------------------------------------------------------------

    async def async_identify(self, message: dict[str, Any], names: dict[str, str]) -> Speaker:
        """Work out who is speaking.

        ``names`` maps a person entity id to its friendly name; it is passed in
        so this module never needs a Home Assistant object.
        """
        person_id, confidence = extract_person(message)
        if person_id is None:
            return UNKNOWN

        entity_id = self._persons.get(person_id)
        if entity_id is None and (waiting := self.pending) is not None:
            # A voice nobody claimed, while somebody is waiting for theirs.
            entity_id = waiting
            self._persons[person_id] = entity_id
            self._pending = None
            await self._async_save()
        if entity_id is None:
            self._unmapped_seen[person_id] = self._clock()
            return Speaker(person_id, None, None, None, False, confidence)
        if entity_id not in names:
            # The person was deleted in Home Assistant; do not keep guessing.
            return Speaker(person_id, None, None, None, False, confidence)

        name = names[entity_id]
        return Speaker(
            person_id=person_id,
            entity_id=entity_id,
            name=name,
            address=self._addresses.get(entity_id) or name,
            trusted=entity_id in self._trusted,
            confidence=confidence,
        )

    def note_speaker(self, speaker: Speaker) -> None:
        """Remember the last recognised person, for services that act "for the speaker"."""
        if speaker.entity_id and speaker.name:
            self._last = (speaker.entity_id, speaker.name, self._clock())

    def recent_speaker(self, max_age: float = 180) -> tuple[str, str] | None:
        """Return ``(entity_id, name)`` of whoever was recognised a moment ago."""
        if self._last and self._clock() - self._last[2] <= max_age:
            return self._last[0], self._last[1]
        return None

    def unmapped_recently(self, within: float = 3600) -> list[str]:
        """Voices Alexa recognised that nobody is assigned to, newest last."""
        now = self._clock()
        return [
            p
            for p, t in sorted(self._unmapped_seen.items(), key=lambda x: x[1])
            if now - t <= within
        ]


def speaker_prompt(speaker: Speaker) -> str:
    """Return the per-request hint handed to the assistant, in German.

    It is the whole identity story for the assistant, so it is explicit about
    what is not known and about what the speaker may and may not do.
    """
    if speaker.known:
        lines = [
            f"Sprecher: {speaker.name}, von Alexa Voice ID erkannt.",
            f'Sprich diese Person mit "{speaker.address}" an.',
        ]
        if speaker.trusted:
            lines.append(
                "Diese Person gehört zur Familie und darf Türen, Tore und Schlösser per Sprache öffnen. "
                "Frage vorher immer einmal kurz nach, ob wirklich geöffnet werden soll."
            )
        else:
            lines.append(
                "Diese Person darf Türen, Tore und Schlösser nicht per Sprache öffnen. "
                "Sag freundlich, dass sie das direkt an der Tür oder in der App machen soll."
            )
        return "\n".join(lines)

    if speaker.person_id is not None:
        return (
            "Sprecher: Alexa erkennt die Stimme, aber sie ist noch keiner Person im Haus "
            "zugeordnet. Verwende keine Anrede. Türen, Tore und Schlösser bleiben tabu."
        )
    return (
        "Sprecher: unbekannt, Alexa hat die Stimme nicht erkannt. Verwende keine Anrede und "
        "rate nicht, wer spricht. Hinweise aus dem Raum sind nur eine Vermutung. Türen, Tore "
        "und Schlösser bleiben tabu. Wenn die Anfrage eine Person braucht, frage einmal kurz, "
        "wer spricht."
    )
