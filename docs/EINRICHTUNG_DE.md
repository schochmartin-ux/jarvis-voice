# Jarvis Voice – Einrichtung (Deutsch)

Echo (Alexa) -> Alexa-Skill "Haus Jarvis" -> Home Assistant (Jarvis Voice) -> Claude.
Alexa erkennt die Stimme (Voice ID) und liefert eine ID mit. Jarvis Voice ordnet sie einer HA-Person zu
und gibt Name und Anrede an Claude weiter.

## 0. Voraussetzungen
- HA 2026.10 oder neuer, öffentliche HTTPS-Adresse (Nabu Casa oder Cloudflare-Tunnel).
- Claude als Assistent (Einstellungen > Geräte & Dienste > Anthropic, API-Key selbst eintragen).
- Personen in HA: person.martin, person.pati, person.julia, person.lena, person.tessa (Vani).

## 1. Dateien nach Home Assistant
1. Studio Code Server öffnen.
2. Den Ordner `custom_components/jarvis_voice` in den HA-Ordner `/config/custom_components/` kopieren.
3. HA neu starten.

## 2. Alexa: Voice ID (jeder selbst, in der Alexa-App)
1. Mehr > Einstellungen > Konto-Einstellungen > Erkennung deiner Stimme: Voice ID einrichten.
2. Dort "Skills personalisieren" aktivieren (sonst liefert Alexa keine Personen-ID).
3. Mädels: Profil in der Amazon-Familie (Haushalt), Voice ID am Echo einrichten.

## 3. Alexa Developer Console: Skill anlegen
1. Create Skill: Name "Haus Jarvis", Sprache Deutsch (Deutschland), Typ Custom, Hosting "Provision your own".
2. Interaction Model > JSON Editor: Inhalt von `skill-package/interactionModels/custom/de-DE.json`, speichern, Build.
3. Endpoint: HTTPS, `https://<deine-externe-URL>/api/jarvis_voice/alexa`, Zertifikat je nach URL
   (Nabu Casa: "trusted CA").
4. Berechtigungen (Permissions): "Skill Personalization" aktivieren (Scope alexa::person_id:read).
5. Distribution: Datenschutzerklärung-URL eintragen (Text unten).
6. Skill-ID (amzn1.ask.skill...) kopieren. Test aktivieren (Development).

## 4. Integration in HA hinzufügen
Einstellungen > Geräte & Dienste > Integration hinzufügen > "Jarvis Voice": Skill-ID und Assistent eintragen
(Assistent mit Claude und dem Jarvis-Prompt aus `docs/JARVIS_PROMPT_DE.txt`).

## 5. Stimmen zuordnen (einmalig pro Person)
Entwicklerwerkzeuge > Aktionen:
```
action: jarvis_voice.enroll_speaker
data:
  person: person.martin
```
Dann sagt Martin am Echo: "Alexa, frag Haus Jarvis, wie spät ist es." Die erkannte Stimme gehört jetzt Martin.
Das Gleiche für person.pati, person.julia, person.lena, person.tessa. Immer nur eine Person auf einmal, und die
Person muss allein sprechen. Kontrolle: `jarvis_voice.list_voices`.

## 6. Anreden und Rechte (Standard)
- Martin = "Chef", Pati = "Chefin", die Töchter mit Vornamen. Türen/Tore dürfen alle Familienmitglieder nach einer
  Rückfrage, unbekannte Stimmen nie.
- Ändern: `jarvis_voice.set_address` (person, address) und `jarvis_voice.set_trusted` (person, trusted).

## 6b. Gedächtnis
- Skripte aus `docs/SKRIPTE_DE.yaml` in HA anlegen und für den Sprachassistenten freigeben, damit Claude sie nutzen kann.
- Ablauf: "Merk dir, ich mag abends 22 Grad." Jarvis fragt "Soll ich mir merken, dass ...?" Erst nach "Ja" wird gespeichert.
  Das Ja wird im Code erkannt, nicht vom Modell. Nach 5 Minuten ohne Antwort verfällt die Notiz.
- Verboten sind Gesundheit, Geld, Schule, Beziehungen, Geheimnisse und alles zu Türen, Rechten und Sicherheit.
- Ansehen: `jarvis_voice.list_notes` (Antwort in den Entwicklerwerkzeugen). Löschen: `jarvis_voice.forget_note`
  (match, all, about, person). Direkt speichern ohne Rückfrage: `jarvis_voice.add_note` (nur für dich).
- Notizen liegen lokal in HA (`.storage/jarvis_voice.memory`). Sie gehen bei jeder Anfrage an Anthropic mit.
- Im Skill-Modell sind Ja und Nein als Intents enthalten: Nach der Rückfrage bleibt das Mikrofon offen.

## 7. Wichtig
- Voice ID ist Komfort, keine Sicherheit. Eine Aufnahme kann die Erkennung täuschen.
- Erkennt Alexa jemanden nicht, ist der Sprecher "unbekannt": keine Anrede, keine Türen.
- Der Endpunkt hat keinen HA-Login: Wer am Echo spricht, erreicht den Assistenten.

## Datenschutzerklärung (Text für die URL)
"Dieser private Alexa-Skill wird nur von einer Familie im eigenen Haushalt genutzt. Er verarbeitet Sprachbefehle, um das
eigene Smart Home zu steuern. Die erkannte Sprecher-ID wird lokal im eigenen Home Assistant gespeichert und nicht an
Dritte weitergegeben. Es werden keine Zahlungs- oder Kontaktdaten verarbeitet."
