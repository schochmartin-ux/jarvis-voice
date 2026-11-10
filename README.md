# Alexa Assist

Talk to **any Home Assistant assistant** from your Echo devices, whichever conversation agent it uses: Home
Assistant's own, or an LLM agent such as OpenAI, Anthropic, Google or Ollama.

```
"Alexa, ask Home Helper to turn on the kitchen light"   one question, one answer
"Alexa, open Home Helper"                               a short beep, then your question
```

An Echo cannot be an Assist satellite: skills get no access to the microphone. What an Alexa **custom skill**
does give us is the recognised text, which is all an assistant's intent stage needs. Alexa keeps the wake word,
the speech recognition and the voice; Home Assistant runs the assistant you pick, with the same tools, the same
settings and the same traces in its debug view as a voice satellite.

Home Assistant serves the skill itself, at `/api/jarvis_voice/alexa`. There is **no AWS account, no Lambda, no
code hosted at Amazon, no account linking and no long-lived token**: the endpoint is open, and every request is
checked against Amazon's signature (full certificate chain validation, 150 s timestamp tolerance) and against
your skill ID.

- **Any assistant**: pick the assistant that answers in the integration settings. Its conversation agent,
  language and **Prefer handling commands locally** all apply, so "turn on the kitchen light" is handled at once
  by Home Assistant and only the rest goes to the LLM.
- **Knows the room**: once an Echo is mapped to its Home Assistant device (see
  [Announcing late answers](#announcing-late-answers)), "turn off the light" means the light in that Echo's area.
- **Late answers are not lost**: Alexa allows about 8 seconds per answer. Past the deadline the skill says the
  answer is coming and **announces it on the Echo that asked** once the assistant is done.
- **Follow-ups work**: the conversation is kept per Echo, so "and tomorrow?" knows what it refers to.

## What you lose

Alexa does the speech, so only the assistant's intent stage runs: its speech-to-text, text-to-speech and
wake-word settings are ignored, and there are no satellite features (on-device timers, media announcements).

## Before you start

- **The skill endpoint must be reachable from the internet** over HTTPS on port 443, with a certificate from a CA
  Amazon trusts and the domain in the certificate's SANs. Amazon's cloud calls it; there is no local-only way.
  See [Making the endpoint reachable](#making-the-endpoint-reachable). A self-signed certificate only works for
  console testing.
- The Amazon account behind your Echo devices must be the one you log into the developer console with. A skill
  left in the Development stage works on those devices indefinitely and never needs certification.
- Home Assistant 2026.10 or newer.

## Making the endpoint reachable

Alexa runs in Amazon's cloud and calls the skill's endpoint itself, so `/api/jarvis_voice/alexa` needs a public
HTTPS address. Common ways to get one:

| Option | How | Notes |
|---|---|---|
| **Home Assistant Cloud** (Nabu Casa) | Turn on Remote UI and use `https://<id>.ui.nabu.casa/api/jarvis_voice/alexa` | Easiest. Exposes the whole instance (behind Home Assistant's login), not only the skill path. |
| **Cloudflare Tunnel** (`cloudflared` add-on) | Public hostname → tunnel → Home Assistant | Free and no router port to open. Bot protection may need a rule that skips the skill path. |
| **Reverse proxy + port forward** (NGINX Proxy Manager, Caddy, Traefik with Let's Encrypt) | Router port 443 → proxy → Home Assistant | The classic self-hosted setup. |
| **Tailscale Funnel** | Funnel publishes a public URL with a valid certificate | No router port to open. |

**Only the skill path needs to be public.** If your tunnel or proxy lets you, forward only
`/api/jarvis_voice/alexa` and block everything else (a Cloudflare WAF rule, a proxy `location` block, an ingress
path rule). The internet then sees just the signed, skill-ID-checked endpoint, and none of Home Assistant's login
page, REST or WebSocket API. If you also use the same hostname for the companion app or remote access, keep
those paths reachable too, or protect them separately (for example with Cloudflare Access).

The endpoint has no Home Assistant authentication, because Alexa cannot send a token. Instead every request must
carry a valid Amazon signature, be for your skill ID and be less than 150 seconds old; anything else is refused
before it reaches the assistant. It only accepts text for the one assistant you picked, with the same
permissions as a voice satellite: limit what that assistant can do through what you expose to Assist, because anyone
who can talk to your Echo can ask it.

## 1. Install the integration

1. HACS → Integrations → ⋮ → Custom repositories → add this repository (category *Integration*), install
   **Alexa Assist**, restart Home Assistant. Or download `jarvis_voice.zip` from the latest release and unzip it into
   `config/custom_components/jarvis_voice/`.
2. Settings → Devices & services → Add integration → **Alexa Assist**. You need the skill ID from step 2, so keep
   this tab open.

## 2. Create the skill

1. Go to <https://developer.amazon.com/alexa/console/ask> and **Create Skill**.
2. Name it, pick the language of your Echo devices, then **Custom** model and **Provision your own** hosting.
3. Open **Interaction Model → JSON Editor**, paste
   [`skill-package/interactionModels/custom/en-US.json`](skill-package/interactionModels/custom/en-US.json)
   (or [`it-IT.json`](skill-package/interactionModels/custom/it-IT.json)), change `invocationName` if you like,
   **Save**, then **Build**. For another language, copy one of the two files: only the invocation name changes,
   and add the language's phrases to `PHRASES` in `const.py`.
4. Under **Endpoint**, choose **HTTPS** and enter `https://your-external-url/api/jarvis_voice/alexa`. For the
   certificate pick *"My development endpoint is a sub-domain of a domain that has a wildcard certificate from a
   certificate authority"* when your certificate covers `*.example.org` rather than the host itself (Cloudflare's
   universal certificate does exactly that), otherwise the plain trusted-CA option.
5. Copy the **Skill ID** (`amzn1.ask.skill.…`) from the same page.

**Choosing the invocation name.** Use two words, and pick them for how Alexa *hears* them: a name Alexa already
knows as content (a radio station, a game, a service) is resolved before a private skill is ever reached.

**Why the slot is `AMAZON.Person`.** The console rejects a sample utterance that is only an
`AMAZON.SearchQuery` slot ("should include a carrier phrase"), which would force "ask Home Helper **to** …"
for everything. `AMAZON.Person` accepts a bare `{query}` and captures the whole sentence. It biases recognition
slightly towards names; if that hurts, switch the slot to `AMAZON.SearchQuery` and the sample to `to {query}`.

## 3. Finish the setup in Home Assistant

- **Skill ID**: the one you just copied. Requests for any other skill are refused.
- **Assistant**: the assistant that answers, from Settings → Voice assistants. Defaults to the preferred one.
  Its language is the one the agent hears, so pick (or create) an assistant in the language of your Echo
  devices. Turn on **Prefer handling commands locally** in it when its agent is an LLM.
- **Ready sound**: the earcon played when the skill opens, see below.
- **Answer deadline**: 6.5 s is a good default, see below.
- **Remember between invocations**: on, see below.
- **Announce late answers on the Echo**: on, see below.
- **Verify Amazon's signature**: on. Turn it off only while debugging: it is the only thing standing between an
  open endpoint and your house.

Everything can be changed later with **Configure**, without a restart.

### Or create the skill from the command line

The skill is defined as files in [`skill-package/`](skill-package), so the console clicking is optional.
Install the [ASK CLI](https://developer.amazon.com/en-US/docs/alexa/smapi/quick-start-alexa-skills-kit-command-line-interface.html)
(`npm install -g ask-cli`), run `ask configure` and say **no** when it offers to link an AWS account.

Copy `skill.json.example` to `skill.json` (gitignored, so your address stays out of the repository), put your
external URL in it, add your vendor id (`ask smapi get-vendor-list`), then:

```bash
ask smapi create-skill-for-vendor --manifest "file:./skill-package/skill.json"
ask smapi set-interaction-model --skill-id <id> --stage development --locale en-US --interaction-model "file:./skill-package/interactionModels/custom/en-US.json"
ask smapi set-skill-enablement --skill-id <id> --stage development
```

On Windows, pass Windows-style paths to `file:`. You can test end to end, with a real signed request, without
saying anything out loud:

```bash
ask smapi simulate-skill --skill-id <id> --stage development --device-locale en-US --session-mode FORCE_NEW_SESSION --input-content "ask home helper what time it is"
```

## How it behaves

### One question per invocation

"Alexa, open …" plays the ready sound and waits for one question. Every other response (an answer, the help
text, "sorry, can you repeat that?") ends the session; asking again means invoking the skill again.
"Alexa, ask Home Helper …" is the phrasing that hands the assistant the cleanest text.

### The ready sound

Opening the skill plays a short sound instead of a spoken greeting: the greeting is pure waiting time, and a
0.34 s blip is a cue you can start talking over. The setting offers three sounds from
[Amazon's sound library](https://developer.amazon.com/en-US/docs/alexa/custom-skills/ask-soundlibrary.html):

| URI | Length |
|---|---|
| `soundbank://soundlibrary/computers/beeps_tones/beeps_tones_06` | 0.34 s (default) |
| `soundbank://soundlibrary/computers/beeps_tones/beeps_tones_01` | 0.50 s |
| `soundbank://soundlibrary/musical/amzn_sfx_bell_short_chime_01` | 0.71 s |

To use another one, copy the `src` from the SSML the sound library prints for it: **do not build the URI from
the Category column**, the two do not match. A self-hosted `https://` MP3 works too (MPEG version 2, 48 kbps,
16/22.05/24 kHz, trusted certificate). Clearing the field brings the spoken greeting back.

### What it remembers

The conversation is keyed on the **Echo**, not on the Alexa session, so a question asked shortly after the last
one continues the same thread:

```
"Alexa, ask Home Helper what the weather is like in Rome"   "Sunny, 22 degrees."
"Alexa, ask Home Helper and tomorrow"                        knows you still mean Rome
```

Home Assistant drops a conversation after **five idle minutes**, so nothing has to be reset by hand. Each Echo
keeps its own thread. Switch *Remember between invocations* off to start every invocation empty.

### The eight seconds

Alexa gives a skill about 8 seconds to return a full response, and progressive responses count against the same
budget, so a slow answer cannot be waited out. Alexa Assist therefore:

- answers within the deadline whenever it can;
- stays silent while the assistant thinks rather than speaking a filler, which would buy no extra time;
- past the deadline, says the answer is coming and **announces it on the Echo that asked** once it is ready.

LLM agents with web search or several tool calls are the ones that go that route. Pick a fast model and low
reasoning effort for the agent of the assistant that answers Alexa, and let **Prefer handling commands locally**
take house commands off the LLM entirely. The first question after a restart is the slowest: the
signing certificate has to be fetched from Amazon once (then it is cached).

## Announcing late answers

Alexa gives a skill a device id of its own (`amzn1.ask.device.…`) that has nothing in common with the serial
numbers the [Amazon Devices](https://www.home-assistant.io/integrations/alexa_devices/) integration knows, so the
two are tied together by hand, once per Echo:

1. Set up the **Amazon Devices** integration, so each Echo gets `notify.<echo>_announce` and
   `notify.<echo>_speak` entities.
2. Ask that Echo anything. The first question from an Echo that is not mapped yet puts its id in the log
   (Settings → System → Logs):

   ```
   WARNING ... The Echo that just spoke is not mapped to anything, so a late answer would be dropped.
   Add the label amzn1.ask.device.AMAX… to that Echo's device in Home Assistant
   ```

3. Add that id as a **label** on that Echo's device (Settings → Devices & services → Devices → the Echo → Add
   label).

From then on late answers are announced on the right Echo: `announce` is preferred, `speak` is the fallback.
The same label tells the assistant which device is asking, so commands without a room ("turn off the light") act
on the Echo's area.
Putting the label on a **notify entity** instead of a device sends late answers there, for example to your
phone.

## If something does not work

| Symptom | Cause |
|---|---|
| "There was a problem with the requested skill's response" | The endpoint was not reached, or answered too slowly. Check the Home Assistant log. |
| `403` in the log with "request is not signed" | Something other than Alexa called the endpoint. |
| `403` with "Rejected a request for skill …" | The skill ID in the settings does not match the skill. |
| `404` | The integration is not set up or not loaded. |
| Nothing in the Home Assistant log at all | The request never arrived: check the external URL, port 443 and any proxy in front. Cloudflare's bot protection may need a rule that skips `/api/jarvis_voice/alexa`. |
| The Echo says "Something went wrong" | The assistant failed. The log line starting "The assistant failed" says why; the assistant's debug view (Settings → Voice assistants → the assistant → ⋮ → Debug) shows the whole run. |
| "The assistant Alexa answers with is gone" in the log | The assistant was deleted. Pick another one under **Configure**. |
| "certificate chain is not trusted" | Amazon rotated its signing certificate and the CA bundle is older. Update Home Assistant. |

## Upgrading from 0.1

0.1 called a conversation agent directly. 0.2 answers through an assistant, so the assistant's settings apply.
The update picks the assistant that already uses your agent (the preferred one first). If none does, Alexa keeps
calling that agent as before and the log says so: create an assistant with that agent, or pick one under
**Configure**. The assistant's language is now the one the agent hears, rather than the Echo's.

## Development

```powershell
uv venv --python 3.14 .venv
uv pip install --python .venv/Scripts/python.exe pytest-homeassistant-custom-component==0.13.371 `
  hassil==3.12.1 home-assistant-intents==2026.10.6 gazetteer-matcher==1.1.0 mutagen==1.48.1 ha-ffmpeg==3.2.2
.venv/Scripts/python.exe -m pytest
```

CI also installs `pymicro-vad` and `pyspeex-noise`, which `assist_pipeline` imports. They have no Windows wheels,
so on Windows `tests/conftest.py` stands in for them; text runs never use them.

On Windows, Home Assistant's test tooling also needs small `fcntl.py` and `resource.py` stub modules in
`.venv/Lib/site-packages` (Linux and CI don't).
