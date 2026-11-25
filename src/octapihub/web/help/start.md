# Schnellstart

## Was ist OctaPiHub?
OctaPiHub ist eine kleine Schaltzentrale („Hub“) für Ihr Zuhause, die auf einem **Raspberry Pi** läuft. Sie steuern damit **Licht, Musik und andere Smart-Home-Geräte**, indem Sie einen **Würfel** (Timeular-/ZEI-Tracker) auf eine Seite legen. Jede der acht Seiten, die Halterung und – falls vorhanden – die Richtungen des Joysticks vom **Sense HAT** können eine Aktion auslösen.

Alles läuft **lokal in Ihrem Heimnetz**. Es gibt kein Konto und keinen Cloud-Dienst von OctaPiHub. Bedient wird der Hub mit dem Browser (Handy, Tablet oder Computer).

> **Ehrlicher Stand:** OctaPiHub wurde bisher nur mit Simulationen getestet (Fake-Würfel, Fake-Sense-HAT, Fake-Lautsprecher, Fake-Bridge und Fake-Broker), nicht mit echter Hardware. Die Handbücher beschreiben, wie die Software gedacht ist. Was auf Ihrer Hardware anders ist, zeigt die Prüfliste in der Installationsanleitung.

## Was kann der Hub?
| Bereich | Fähigkeiten |
|---|---|
| **Würfel** | Bis zu 3 Würfel per Bluetooth; Verbindungsstatus, Akkustand, aktive Seite; neue Würfel per Suche koppeln |
| **Licht (Philips Hue)** | Szenen, Ein/Aus, heller/dunkler, Farbe, nächste Szene – je Raum |
| **Musik** | Google Nest/Hub, Raspberry-Pi-Ausgang, Bluetooth-Box; Ordner, Playlists, Radio, YouTube; Zufall und Wiederholen |
| **Smart Home** | Webhooks, MQTT (Zigbee2MQTT, Tasmota …), Home Assistant (Weg zu Matter) |
| **Sense HAT** | Temperatur, Luftfeuchtigkeit, Luftdruck u. a., Verlauf, 21 LED-Ansichten, Joystick |
| **Verwaltung** | Namen und Farben je Seite, Export/Import, CSV, Protokolle, Fehlercodes |

## In zehn Minuten loslegen
1. **Oberfläche öffnen:** Im Browser die Adresse Ihres Raspberry Pi eingeben (z. B. `http://192.168.1.50:8080`). Wie Sie den Hub installieren, steht unter **Hilfe → Installation**.
2. **Token bereithalten:** Lesen ist frei, Ändern braucht das **Zugriffs-Token** aus `/etc/octapihub/octapihub.env`. Tragen Sie es unten in das Feld „Zugriffs-Token“ ein.
3. **Würfel verbinden:** Seite **Würfel** → „Würfel verwalten“ → *Nach Würfeln suchen* → Ihren Würfel hinzufügen. Legen Sie ihn danach einmal auf eine Seite.
4. **Etwas einrichten:** Licht auf der Seite **Licht** (Bridge koppeln), Musik auf der Seite **Musik** (Gerät und Playlist), andere Geräte auf **Smart Home**.
5. **Seiten belegen:** Seite **Würfel** → für jede Seite im Auswahlfeld eine Aktion wählen → *Zuordnung speichern*. Sie gilt sofort.
6. **Ausprobieren:** Würfel auf die Seite legen – oder *Jetzt auslösen* klicken.

## Orientierung in der Oberfläche
- **Oben** die Navigation, rechts **Hilfe**, **Darstellung** (Automatisch/Hell/Dunkel) und **Schriftgröße**. Auf dem Handy ist die Navigation unten.
- Jede Seite hat oben eine **Statusanzeige** (Ampel): grün = in Ordnung, gelb = Hinweis, rot = Störung.
- Neben **jedem Eingabefeld** steht ein kleines **ⓘ**: darauf klicken, und es erscheint eine Erklärung mit Beispiel.
- Fehler erscheinen in einem Kasten mit Erklärung und **Fehlercode** (z. B. `CH-MUS-002`). Unter **Hilfe → Fehlercodes** steht, was zu tun ist.

## Wo finde ich was?
- **Alles zur Bedienung:** [Benutzerhandbuch](handbuch.md)
- **Installation auf dem Pi:** [Installation](installation.md)
- **Andere Geräte (MQTT, Home Assistant, Matter):** [Smart Home](smarthome.md)
- **Etwas klappt nicht:** [Fehlersuche](fehlersuche.md)
- **Begriffe:** [Glossar](glossar.md) · **Kurze Antworten:** [Häufige Fragen](faq.md)
