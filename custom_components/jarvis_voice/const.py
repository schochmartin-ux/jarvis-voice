"""Constants for the Jarvis Voice integration."""

import logging

DOMAIN = "jarvis_voice"
LOGGER = logging.getLogger(__package__)

TITLE = "Jarvis Voice"

# Echo devices cannot be Assist satellites (no raw microphone access), so the
# only way in is a custom skill: Alexa does wake word, speech recognition and
# text to speech, and Home Assistant runs the assistant's intent stage.
CONF_SKILL_ID = "skill_id"
CONF_ASSISTANT = "assistant"
# Replaced by CONF_ASSISTANT in 1.2; only read to migrate older entries.
CONF_AGENT = "agent"
CONF_VERIFY_SIGNATURE = "verify_signature"
CONF_DEADLINE = "deadline"
CONF_ANNOUNCE_LATE = "announce_late"
CONF_LAUNCH_SOUND = "launch_sound"
CONF_REMEMBER = "remember"
CONF_VOICE = "voice"

ENDPOINT = f"/api/{DOMAIN}/alexa"

# Whichever assistant is marked preferred in Settings > Voice assistants.
DEFAULT_ASSISTANT = "preferred"

# Alexa gives a skill about 8 seconds to return a full response, so the answer
# has to be in the body of the first reply. The rest of the budget goes to TLS,
# the round trip to Amazon and our own JSON handling. The wait itself stays
# silent: a progressive response would only put a filler in front of the answer
# without buying any time against those 8 seconds.
RECOMMENDED_DEADLINE = 6.5
DEADLINE_RANGE = (1.0, 7.5)
RECOMMENDED_VERIFY_SIGNATURE = True
RECOMMENDED_ANNOUNCE_LATE = True
# Key the conversation on the Echo rather than on the Alexa session, so asking
# again within Home Assistant's five minute window continues the same thread
# and "and tomorrow?" knows what it refers to.
RECOMMENDED_REMEMBER = True

# Alexa speaks German with its own voice unless the SSML names another one. These
# are the German voices Alexa skills may ask for; empty keeps the Echo default.
VOICES = ["Hans", "Marlene", "Vicki"]
RECOMMENDED_VOICE = "Hans"

# Played instead of a spoken greeting when the skill is opened, so the wait
# before the microphone opens is as short as possible. Copied verbatim from the
# SSML the Alexa sound library prints for each sound: the URI does NOT follow
# the category shown in its table, so these cannot be derived by hand.
# https://developer.amazon.com/en-US/docs/alexa/custom-skills/ask-soundlibrary.html
LAUNCH_SOUNDS = [
    "soundbank://soundlibrary/computers/beeps_tones/beeps_tones_06",  # 0.34 s
    "soundbank://soundlibrary/computers/beeps_tones/beeps_tones_01",  # 0.50 s
    "soundbank://soundlibrary/musical/amzn_sfx_bell_short_chime_01",  # 0.71 s
]
RECOMMENDED_LAUNCH_SOUND = LAUNCH_SOUNDS[0]

# Name of the catch-all intent and slot in the skill's interaction model
# (skill-package/). The slot is typed AMAZON.Person and not AMAZON.SearchQuery:
# the console rejects a sample utterance that is only a SearchQuery slot
# ("should include a carrier phrase").
QUERY_INTENT = "AssistQueryIntent"
QUERY_SLOT = "query"

# Requests carry the Echo's skill-scoped device id, which is unrelated to the
# serial numbers the alexa_devices integration knows. To speak a late answer on
# the Echo that asked, the id is matched against Home Assistant device labels.

PHRASES = {
    "de": {
        "launch": "Ja, bitte?",
        "reprompt": "Ich höre zu.",
        "late": "Einen Moment, ich brauche etwas länger. Ich melde mich gleich.",
        "late_dropped": "Die Antwort kam leider zu spät. Frag mich bitte noch einmal.",
        "error": "Entschuldigung, da ist etwas schiefgelaufen.",
        "empty": "Dazu habe ich keine Antwort.",
        "goodbye": "Bis später!",
        "help": "Du kannst mir Fragen stellen oder Befehle geben, zum Beispiel: Wie warm ist es im Wohnzimmer? Oder: Mach das Licht im Flur aus.",
        "not_understood": "Das habe ich nicht verstanden. Kannst du es wiederholen?",
        "confirm_reprompt": "Soll ich mir das merken?",
        "nothing_pending": "Dazu liegt nichts an.",
    },
    "it": {
        "launch": "Ciao, dimmi.",
        "reprompt": "Dimmi.",
        "late": "Ci sto ancora pensando, ti rispondo tra poco.",
        "late_dropped": "Non ho fatto in tempo, prova a richiedermelo.",
        "error": "Si è verificato un errore.",
        "empty": "Non ho una risposta.",
        "goodbye": "A presto.",
        "help": "Dimmi un comando per la casa oppure fammi una domanda.",
        "not_understood": "Non ho capito, puoi ripetere?",
        "confirm_reprompt": "Vuoi che lo ricordi?",
        "nothing_pending": "Non c'è nulla in sospeso.",
    },
    "en": {
        "launch": "Hi, go ahead.",
        "reprompt": "Go ahead.",
        "late": "I am still thinking, I will get back to you shortly.",
        "late_dropped": "I did not make it in time, please ask me again.",
        "error": "Something went wrong.",
        "empty": "I have no answer.",
        "goodbye": "See you.",
        "help": "Tell me a command for the house or ask me a question.",
        "not_understood": "Sorry, can you repeat that?",
        "confirm_reprompt": "Should I remember that?",
        "nothing_pending": "There is nothing waiting.",
    },
}
