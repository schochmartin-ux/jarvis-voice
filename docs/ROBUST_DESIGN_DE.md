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
- **Namensschema konsistent im Bereich halten** (z. B. „Spot EG Flur 1“, „TH UG 1“).
- **Beschreibung in jeder Automation:** Zweck, Verhalten und vorherige Config-Hashes dokumentieren.
- **Nach jeder Änderung testen** (Taster drücken, Bewegung auslösen, Traces prüfen).
