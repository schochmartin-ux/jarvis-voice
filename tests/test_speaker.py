"""Tests for the speaker registry. They need no Home Assistant."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

_PATH = Path(__file__).parents[1] / "custom_components" / "jarvis_voice" / "speaker.py"
_SPEC = importlib.util.spec_from_file_location("jarvis_speaker", _PATH)
speaker = importlib.util.module_from_spec(_SPEC)
import sys  # noqa: E402

sys.modules["jarvis_speaker"] = speaker
_SPEC.loader.exec_module(speaker)

NAMES = {
    "person.martin": "Martin",
    "person.pati": "Pati",
    "person.julia": "Julia",
    "person.tessa": "Vani",
}


class FakeStore:
    def __init__(self, data=None):
        self.data = data
        self.saves = 0

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data
        self.saves += 1


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def msg(person_id=None, level=None):
    system = {}
    if person_id is not None:
        system["person"] = {"personId": person_id}
        if level is not None:
            system["person"]["authenticationConfidenceLevel"] = {"level": level}
    return {"context": {"System": system}}


def run(coro):
    return asyncio.run(coro)


def make(store=None, clock=None):
    reg = speaker.SpeakerRegistry(store or FakeStore(), clock=clock or Clock())
    run(reg.async_load())
    return reg


def test_extract_person():
    assert speaker.extract_person(msg()) == (None, None)
    assert speaker.extract_person({}) == (None, None)
    assert speaker.extract_person({"context": {"System": {"person": "x"}}}) == (None, None)
    assert speaker.extract_person(msg("amzn1.ask.person.A")) == ("amzn1.ask.person.A", 100)
    assert speaker.extract_person(msg("amzn1.ask.person.A", 400)) == ("amzn1.ask.person.A", 400)
    assert speaker.extract_person({"context": {"System": {"person": {"personId": ""}}}}) == (
        None,
        None,
    )


def test_no_voice_means_unknown_and_tabu():
    reg = make()
    who = run(reg.async_identify(msg(), NAMES))
    assert who == speaker.UNKNOWN and not who.known and not who.trusted
    prompt = speaker.speaker_prompt(who)
    assert "unbekannt" in prompt and "tabu" in prompt and "keine Anrede" in prompt


def test_unmapped_voice_is_listed_not_guessed():
    reg = make()
    who = run(reg.async_identify(msg("P1"), NAMES))
    assert who.person_id == "P1" and not who.known
    assert reg.unmapped_recently() == ["P1"]
    assert "noch keiner Person" in speaker.speaker_prompt(who)


def test_enrollment_assigns_next_unclaimed_voice_once():
    store = FakeStore()
    reg = make(store)
    reg.start_enrollment("person.pati")
    assert reg.pending == "person.pati"
    who = run(reg.async_identify(msg("P-PATI"), NAMES))
    assert who.entity_id == "person.pati" and who.address == "Chefin" and who.trusted
    assert reg.pending is None
    assert store.data["persons"] == {"P-PATI": "person.pati"}
    # A second, different voice is not assigned to Pati afterwards.
    other = run(reg.async_identify(msg("P-X"), NAMES))
    assert not other.known


def test_enrollment_never_steals_a_voice_that_is_already_mapped():
    reg = make()
    reg.start_enrollment("person.martin")
    run(reg.async_identify(msg("P-M"), NAMES))
    reg.start_enrollment("person.julia")
    again = run(reg.async_identify(msg("P-M"), NAMES))
    assert again.entity_id == "person.martin"
    assert reg.pending == "person.julia"  # still waiting for Julia's own voice


def test_enrollment_expires():
    clock = Clock()
    reg = make(clock=clock)
    reg.start_enrollment("person.julia", minutes=1)
    clock.now += 61
    assert reg.pending is None
    assert not run(reg.async_identify(msg("P-J"), NAMES)).known


def test_daughters_get_first_name_and_may_open_doors_after_a_question():
    reg = make()
    reg.start_enrollment("person.julia")
    julia = run(reg.async_identify(msg("P-J"), NAMES))
    assert julia.address == "Julia" and julia.trusted
    prompt = speaker.speaker_prompt(julia)
    assert '"Julia"' in prompt and "darf Türen" in prompt and "kurz nach" in prompt
    run(reg.async_set_trusted("person.julia", False))
    again = run(reg.async_identify(msg("P-J"), NAMES))
    assert not again.trusted and "nicht per Sprache öffnen" in speaker.speaker_prompt(again)
    reg.start_enrollment("person.tessa")
    vani = run(reg.async_identify(msg("P-V"), NAMES))
    assert vani.address == "Vani"  # friendly name of the person called Tessa


def test_unknown_people_never_open_doors():
    reg = make()
    for m in (msg(), msg("P-GUEST")):
        who = run(reg.async_identify(m, NAMES))
        assert not who.trusted and "tabu" in speaker.speaker_prompt(who)


def test_last_speaker_is_remembered_briefly():
    clock = Clock()
    reg = make(clock=clock)
    assert reg.recent_speaker() is None
    reg.start_enrollment("person.pati")
    who = run(reg.async_identify(msg("P-P"), NAMES))
    reg.note_speaker(who)
    assert reg.recent_speaker() == ("person.pati", "Pati")
    clock.now += 600
    assert reg.recent_speaker() is None


def test_parents_may_open_doors_but_are_asked_first():
    reg = make()
    reg.start_enrollment("person.martin")
    martin = run(reg.async_identify(msg("P-M"), NAMES))
    prompt = speaker.speaker_prompt(martin)
    assert '"Chef"' in prompt and "darf Türen" in prompt and "kurz nach" in prompt


def test_persistence_roundtrip_and_overrides():
    store = FakeStore()
    reg = make(store)
    reg.start_enrollment("person.julia")
    run(reg.async_identify(msg("P-J"), NAMES))
    run(reg.async_set_address("person.julia", "Miss Julia"))
    run(reg.async_set_trusted("person.julia", True))
    again = make(FakeStore(store.data))
    who = run(again.async_identify(msg("P-J"), NAMES))
    assert who.address == "Miss Julia" and who.trusted


def test_forget_removes_all_voices():
    reg = make()
    for pid in ("A", "B"):
        reg.start_enrollment("person.julia")
        run(reg.async_identify(msg(pid), NAMES))
    assert run(reg.async_forget("person.julia")) == 2
    assert not run(reg.async_identify(msg("A"), NAMES)).known


def test_deleted_person_is_not_guessed():
    reg = make()
    reg.start_enrollment("person.julia")
    run(reg.async_identify(msg("P-J"), NAMES))
    who = run(
        reg.async_identify(msg("P-J"), {k: v for k, v in NAMES.items() if k != "person.julia"})
    )
    assert not who.known and who.person_id == "P-J"
