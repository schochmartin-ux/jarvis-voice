# Home Assistant – Robust Design Guideline

Stand: 2026-11-16

Verbindliche Regeln für Automationen, Gerätetausch und Pflege im HäusleDeluxe-Home-Assistant. Gilt für jede neue
oder geänderte Automation. Erweitern, sobald wir etwas Neues lernen.

## 1. Robuste Licht-Automationen

- **Nachfassen statt Hoffen:** Nach jedem Schaltbefehl 2 s warten, dann Lampen, die nicht im Zielzustand sind,
  erneut schalten. 2–3 Versuche. Funk-/Matter-Lampen verlieren gelegentlich Befehle.
- **`continue_on_error: true`** bei Lichtbefehlen, damit eine nicht erreichbare Lampe nicht die ganze Automation
  abbricht.
- **Nur schalten, was nötig ist:** Beim Einschalten nur Lampen ansteuern, die aus sind. Bereits brennende (z. B. per
  Taster gedimmte) bleiben unverändert.
- **Mode `restart`** für Bewegungs- und Taster-Automationen, damit neue Ereignisse laufende Abläufe sauber ablösen.
- **Einheitliche Lichtwerte** pro Bereich (z. B. Treppenhaus UG: 100 %, 3389 K). Farbtemperatur immer in Kelvin
  (`color_temp_kelvin`).

## 2. Bewegungsmelder

- **Ausschalten erst, wenn alle Melder eines Bereichs frei sind** (z. B. 2 Min Nachlauf).
- **Hängende Melder abfangen:** Meldet ein Melder länger als 10 Min am Stück Bewegung, gilt er als hängend und wird
  ignoriert – für das Ausschalten und für das Wieder-Einschalten. Er zählt erst wieder mit, wenn er zwischendurch
  frei gemeldet hat.
  - Anlass: BTHome-Melder a4a9 hing am 16.11.2026 1 h 40 min auf „Bewegung“; Licht ging nie aus.
- **Bekanntes Restrisiko:** Hängen alle Melder eines Bereichs gleichzeitig, schaltet nichts aus.

## 3. Taster und manuelle Bedienung

- **Manuell hat Vorrang:** Ausschalten per Taster sperrt den Bewegungsmelder für 3 Min (Timer-Helper), damit das
  Licht nicht sofort wieder angeht.
- **Smart-Relais immer an:** Shelly im Detached-Modus, Relais dauerhaft an, damit die smarten Leuchtmittel Strom
  haben. Taster liefern nur Events (`single_push`, `long_push`).
- **Dimmen per Halten:** Schritte von 12 %, Richtung wechselt bei jedem Halten, Ende beim nächsten Tasterereignis;
  Ober- und Untergrenzen (5–100 %).

## 4. Modi

- **Partymodus** (`input_boolean.party`): Bewegungsautomationen pausieren, solange er an ist.
- Neue Automationen immer darauf prüfen, ob Party- oder Urlaubsmodus sie beeinflussen muss.

## 5. Gerätetausch

1. **Neues Gerät eindeutig identifizieren:** über Seriennummer (Apple Home / HA-Geräteinfo) oder per Farbtest –
   nicht über Namen; viele MOES-Lampen melden identisches Modell und Firmware.
2. **Verwendung des alten Geräts prüfen:** Automationen, Skripte, Szenen, Gruppen (auch UI-Gruppen-Helper),
   Dashboards.
3. **Entity-ID-Übernahme:** Altes Gerät auf `…_alt` umbenennen, dann bekommt das neue Gerät die alte Entity-ID. So
   laufen alle Verweise ohne Änderung weiter.
4. **Prüfen:** neue ID zeigt auf die richtige Seriennummer, Gruppen und Automationen verweisen darauf, keine
   Verweise auf `…_alt` mehr.
5. **Aufräumen:** altes Gerät löschen; doppelte und tote Einträge (z. B. Mehrfach-Kopplungen) entfernen.

## 6. Benennung und Pflege

- **Gerätename = Anzeigename**, Entity-Name folgt dem Gerät (sonst entstehen Dopplungen wie „TH UG 1 TH UG 1“).
- **Raum am Gerät setzen**, nicht an der Entity.
- **Namensschema konsistent im Bereich halten** (z. B. „Spot Flur EG 1“, „Spot Küche 1“), Details in Abschnitt 7.
- **Beschreibung in jeder Automation:** Zweck, Verhalten und vorherige Config-Hashes dokumentieren.
- **Nach jeder Änderung testen** (Taster drücken, Bewegung auslösen, Traces prüfen).

## 7. Namenskonvention

Gilt für Geräte, Entities, Gruppen, Automationen, Skripte und Helper.

### Warum so

- **Alexa braucht eindeutige Namen.** Die Leuchten sind über Home Assistant Cloud an Alexa freigegeben. Alexa kennt
  die Bereiche aus Home Assistant nicht. Deshalb bleibt der Ort im Namen, und jeder Name kommt im ganzen Haus nur
  einmal vor.
- **Alexa erkennt Geräte an der Entity-ID.** Eine neue Entity-ID ist für Alexa ein neues Gerät. Routinen, Gruppen und
  Raumzuordnungen in der Alexa-App hängen dann am alten, toten Gerät.
- **Dashboards und Automationen verweisen auf Entity-IDs.** Home Assistant passt diese Verweise beim Umbenennen nicht
  an. Ein geänderter Anzeigename stört sie dagegen nicht.

### Grundregeln

- **Gerätename = Anzeigename**, der Entity-Name folgt dem Gerät.
- **Im ganzen Haus eindeutig.**
- **Deutsch, mit Umlauten**, keine Hersteller- oder Modellnamen (nicht „MOES Matter Light“, „H61E5“).
- **Keine Abkürzungen außer EG, OG, UG.** Kein „TH“: Sprachassistenten erkennen und sprechen es schlecht.
- **Ort vor Geschoss**, wie bei den Bereichen: „Flur EG“, nicht „EG Flur“.
- **Die Nummer bezeichnet eine feste Position** (z. B. von der Tür aus im Uhrzeigersinn) und bleibt beim Gerätetausch
  gleich. Keine Leerzeichen am Ende.
- **Jedes Gerät hat einen Bereich.**

### Schema nach Typ

- **Leuchte:** `<Typ> <Ort> <Nr>`, z. B. „Spot Küche 1“, „Spot Insel 2“, „Spot Flur EG 3“, „Lampe Sofa“, „LED Kamin“.
  Typ aus: Spot, Lampe, LED, Leuchte, Wandlicht, Backlight. Nummer nur, wenn es mehrere gibt.
- **Gruppe:** Mehrzahl plus Ort, z. B. „Spots Küche“, „Spots Flur EG“.
- **Automation:** `<Bereich oder Thema>: <was passiert>`, Bereich ausgeschrieben, z. B. „Treppenhaus UG: Licht bei
  Bewegung“, „Urlaubsmodus: Licht abends“.
- **Skript:** wie Automation, mit einem Verb, z. B. „Jalousien: alle schließen“.
- **Helper:** gleiches Präfix wie die Automation, die ihn nutzt, z. B. „Treppenhaus UG Bewegung Sperre“.
- **Modus-Schalter:** nur der Modusname, z. B. „Party“, „Urlaubsmodus“.

### Entity-IDs

- **Bleiben stabil.** Der Anzeigename darf sich ändern, die Entity-ID nicht.
- **Umbenannt wird nur, was nichtssagend oder falsch ist** (`moes_matter_light_7`, `h61e5`, Reste wie `_2`), und
  dann gebündelt:
  1. Verweise in Automationen, Skripten, Szenen, UI-Gruppen und Dashboards suchen (wie in Abschnitt 5).
  2. Entity-ID ändern und alle Verweise umstellen; danach nach der alten ID suchen, es dürfen keine Treffer bleiben.
  3. In der Alexa-App Geräte neu suchen, die alten Geräte löschen, Gruppen und Routinen prüfen.
- **Neue IDs aus dem Namen**, z. B. `light.spot_buro_3`.

### Sprache

- Für natürliche Formulierungen („Kellertreppe“, „Licht unten im Flur“) Aliase in Home Assistant setzen. Sie gelten
  für Assist und Jarvis. Ob Alexa sie übernimmt, ist nicht geprüft; für Alexa zählt der Name.
