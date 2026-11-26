# Benutzerhandbuch

Dieses Handbuch erklärt **jede Seite und jede Funktion** der Oberfläche. Wie Sie OctaPiHub installieren, steht in der [Installation](installation.md), die Einrichtung anderer Systeme in [Smart Home](smarthome.md).

## 1. Grundlagen

### Die Idee
Sie legen einen Würfel auf eine Seite, und **eine Aktion passiert**: Licht an, Musik spielt, ein Webhook wird gesendet. Der Raspberry Pi („Hub“) ist die Schaltzentrale im Heimnetz. Eine **Aktion** ist ein Schritt wie „Hue-Szene Abend einschalten“, „Playlist Morgenmusik abspielen“ oder „Lauter“.

### Würfel, Seiten, Halterung, Joystick
- Ein Würfel hat **acht Seiten** (1 bis 8). Dazu kommt die **Halterung** („Seite 0“): der Zustand, in dem der Würfel senkrecht in seiner Halterung steht, also keine Seite eindeutig oben liegt. Sie lässt sich wie eine Seite belegen, z. B. mit „Alle Wiedergaben stoppen“.
- Eine Aktion wird ausgelöst, wenn der Würfel **auf eine Seite gelegt wird**. Der allererste Wert nach dem Verbinden löst nichts aus (er legt nur fest, wo der Würfel gerade liegt).
- Kippt der Würfel mehrfach schnell, zählt nur die **letzte** Seite (Entprellung, Standard 300 Millisekunden).
- Welche physische Seite welche Nummer hat, sehen Sie beim Ausprobieren: Auf der Seite *Würfel* steht „zuletzt erkannte Seite“. Notieren Sie es und beschriften Sie den Würfel nach Wunsch.
- Der **Joystick** des Sense HAT hat fünf „Seiten“: hoch, rechts, runter, links und Mitte drücken. Er löst seine Aktionen in der LED-Ansicht „Steuerung“ aus (siehe Abschnitt Anzeige).
- Bis zu **drei Würfel** sind möglich. Die „Verwendung“ (Licht, Musik, Universal) ist nur eine Beschriftung: jeder Würfel kann alles.

### Zugriff und Token
- **Ansehen** kann jeder im Heimnetz. **Ändern** (speichern, abspielen, koppeln, exportieren) braucht das **Zugriffs-Token**. Tragen Sie es am unteren Rand der Seite ein; der Browser merkt es sich nur, solange der Tab offen ist.
- Ist kein Token eingerichtet, sind alle Schaltflächen zum Ändern ausgegraut („Nur ansehen“).
- Das Token steht in `/etc/octapihub/octapihub.env` hinter `OCTAPIHUB_WEB_TOKEN=`.

### Darstellung
Oben rechts wechseln Sie zwischen **Automatisch, Hell und Dunkel** und zwischen zwei **Schriftgrößen**. Die Wahl gilt für diesen Browser.

### Hilfe-Buttons
Neben jedem Eingabefeld, jedem Schalter und jedem Häkchen steht ein kleines **ⓘ**. Ein Klick zeigt eine Erklärung: was das Feld ist, was Sie eintragen, ein Beispiel und was bei einem falschen Wert passiert. **Esc** schließt die Erklärungen.

## 2. Übersicht
Die Startseite zeigt auf einen Blick den Zustand von **Sense HAT** (Messwerte), **Würfeln** (wie viele verbunden, Akkuwarnungen), **Licht** (Bridge verbunden?), **Musik** (spielt/pausiert/bereit/Fehler, Titel, Gerät, Lautstärke) und **System**. Die Karten aktualisieren sich live. Unter den Karten erscheinen **Hinweise**, wenn etwas Aufmerksamkeit braucht. Ein gelbes Banner ganz oben („Startwarnung“) zeigt, wenn beim Start etwas nicht geklappt hat, zum Beispiel wenn eine Sicherung der Einstellungen verwendet wurde.

## 3. Würfel
Die wichtigste Seite. Sie hat drei Teile: **Status**, **Würfel verwalten** und **Zuordnung**.

### Status der Würfel
Oben steht eine Zusammenfassung („3 von 3 Würfeln verbunden“). Jede Karte zeigt:
- den **Zustand**: *Verbunden*, *Verbindet …*, *Wartet auf Bluetooth*, *Neuer Versuch*, *Störung*;
- den **Akkustand** mit Farbe (grün, gelb unter 20 %, rot unter 10 %) und dem Hinweis „Bald laden“;
- die **zuletzt erkannte Seite**.
Die Seite, die gerade oben liegt, ist in der Zuordnung weiter unten **hervorgehoben** und mit einem Punkt markiert.

### Würfel verwalten (koppeln und entfernen)
1. Würfel einmal **kippen** (so wacht er auf), dann *Nach Würfeln suchen*. Die Suche dauert etwa 8 Sekunden.
2. In der Liste Ihren Würfel suchen. Vermutliche Würfel sind markiert (Name enthält „Timeular“ oder „ZEI“); die Adresse wird gekürzt gezeigt. Geben Sie einen **Namen** und die **Verwendung** an und klicken Sie *Hinzufügen*. Der Würfel verbindet sich danach neu.
3. Alternativ tragen Sie unter „Adresse selbst eintragen“ die Bluetooth-Adresse ein.
4. **Entfernen:** Häkchen „Ja, entfernen“ setzen, dann „<Name> entfernen“. Dabei gehen auch Zuordnung, Namen und Farben dieses Würfels verloren. Die bisherige Konfiguration wird vorher gesichert.
Es sind höchstens **3 Würfel** möglich, mindestens **einer** bleibt eingerichtet. Ein Würfel spricht nur mit **einem** Gerät: trennen Sie ihn vorher von Ihrem Smartphone.

### Zuordnung: was löst jede Seite aus?
Für jeden Würfel (und den Joystick) gibt es Karten für die Halterung und die Seiten 1 bis 8. Auf jeder Karte wählen Sie im Auswahlfeld **eine Aktion**:

| Gruppe | Aktionen |
|---|---|
| **Licht: Steuern** | Ein/Aus umschalten, Einschalten, Ausschalten, Heller, Dunkler, Nächste Farbe, Nächste Szene, Vorherige Szene |
| **Licht: Farbe setzen** | Warmweiß, Neutralweiß, Kaltweiß, Rot, Orange, Gelb, Grün, Türkis, Blau, Violett, Pink |
| **Licht: Szene einschalten** | Jede Hue-Szene (nach dem Laden der Szenen) |
| **Smart Home** | Ihre Aktionen von der Seite *Smart Home* |
| **Musik: Playlist abspielen** | Eine Ihrer Playlists (▶) |
| **Musik: Playlist starten oder pausieren** | Läuft sie, pausiert die Seite; ist sie pausiert, setzt sie fort (⏯) |
| **Musik: Steuern** | Pause / Weiter, Pause, Weiter, Stopp, Alle Wiedergaben stoppen, Nächster Titel, Vorheriger Titel, Lauter, Leiser |

Klicken Sie danach auf **Zuordnung speichern** (unten, mit Token). Sie gilt **sofort**, die Würfel müssen sich nicht neu verbinden. Mit **Jetzt auslösen** testen Sie eine Seite, ohne den Würfel zu bewegen.

#### Name, Farbe, Optionen und Details
Jede Karte hat einen Bereich **„Name, Farbe, Optionen und Details“**:
- **Name dieser Seite** (bis 40 Zeichen): erscheint statt „Seite 3“, zum Beispiel „Morgenmusik“.
- **Farbe**: acht Farben zur Orientierung in dieser Oberfläche (ein farbiger Rand der Karte). Der Würfel selbst bekommt davon nichts mit.
- **Optionen**:
  - *Lampen / Raum*: für Licht-Aktionen, welcher Raum gesteuert wird (Standard: alle Lampen). „Nächste Szene“ geht die Szenen **dieses Raumes** alphabetisch durch.
  - *Gerät*: auf welchem Wiedergabegerät diese Musik-Aktion läuft.
  - *Reihenfolge*: wie die Playlist, zufällig oder in Reihenfolge.
  - *Wiederholen*: wie die Playlist, nicht wiederholen, alles wiederholen, Titel wiederholen.
- **Zeit** (nur bei Seiten mit einer Auswahl, nicht bei Seiten mit mehreren Schritten):
  - *Verzögerung*: 0 bis 30 Sekunden warten, bevor die Aktion startet, zum Beispiel „Musik 2 Sekunden nach dem Licht“. Währenddessen warten die weiteren Aktionen dieses Würfels.
  - *Nur von … bis …*: ein Zeitfenster, auch über Mitternacht (22:00 bis 06:00). Das Ende selbst zählt nicht mehr dazu.
  - *Wochentage*: nur an den angekreuzten Tagen; keiner angekreuzt = jeden Tag.
  - Außerhalb der Bedingung passiert beim Drehen **nichts** (auch kein Fehler). „Jetzt auslösen“ ignoriert die Bedingung, damit Sie testen können. Die Uhrzeit folgt der eingestellten Zeitzone.
- **Was passiert, wenn diese Seite oben liegt?** In Klartext: bei einer Playlist Quelle, Ordner, Gerät, Zufall und Wiederholen und Lautstärke; bei Licht Aktion und Raum; bei einer Szene deren Name; bei Smart Home Art und Ziel (Web-Adressen werden gekürzt, weil sie Geheimnisse enthalten können).

#### Ungespeicherte Änderungen
Haben Sie auf einer Seite mit „Speichern“ etwas geändert und wollen die Seite verlassen, warnt der Browser. Unten neben dem Speichern-Knopf steht dann „● Ungespeicherte Änderungen“. Das Token zählt nicht als Änderung.

#### Vorlagen: ein Würfel mit einem Klick belegen
Unter **Vorlagen** (Seite Würfel) gibt es zwei fertige Belegungen, die ohne eigene Szenen oder Playlists funktionieren:
- **Licht-Würfel** (braucht Hue): Seite 1 Ein/Aus, 2 heller, 3 dunkler, 4 Farbe wechseln, 5 Warmweiß, 6 alles aus.
- **Musik-Würfel** (braucht Musik): Seite 1 Pause/Weiter, 2 nächster Titel, 3 vorheriger Titel, 4 lauter, 5 leiser, 6 Stopp.

Wählen Sie die Vorlage und den Würfel, setzen Sie das Häkchen „Ja, ersetzen“ und geben Sie das Token ein. **Die bisherige Zuordnung, Namen und Farben dieses Würfels werden ersetzt**; vorher wird die Konfiguration gesichert (Seite Sicherung → Frühere Versionen). Danach lässt sich jede Seite wie gewohnt anpassen. Den Joystick gibt es als Ziel nicht.

#### Ein Gerät für den ganzen Würfel
Über jedem Würfel steht **„Wiedergabegerät für die Musik-Aktionen dieses Würfels“**. Wählen Sie dort z. B. „Nest Mini Küche“, gilt es für alle Musik-Seiten dieses Würfels. Einzelne Seiten dürfen abweichen. So ist immer klar, welche Seite *was* *wo* auslöst.

#### Halterung (Würfel senkrecht)
Für einen Musik-Würfel empfiehlt sich dort „Alle Wiedergaben stoppen“. Bei einem Licht-Würfel am besten **keine Aktion**: Dann bleibt das Licht, wie es ist, wenn der Würfel in die Halterung gestellt wird.

#### Gesperrte Karten
Zuordnungen mit **mehreren Aktionen hintereinander** (nur in der Konfigurationsdatei möglich) erscheinen **gesperrt** mit ihrer Beschreibung und bleiben beim Speichern unverändert. Name und Farbe lassen sich trotzdem vergeben.

### Gut zu wissen
- Eine Aktion wird **nicht nachgeholt**, wenn sie zu spät käme: Musik und Smart-Home-Aktionen verfallen nach etwa 1 bis 2 Minuten, Licht nach 2 Minuten. So geht nicht plötzlich später Musik an.
- Fehler, die Sie beheben müssen (Gerät gelöscht, Musik aus, Quelle fehlt), werden nicht ständig wiederholt. Verbindungsfehler werden kurz und mit Pausen wiederholt.
- „Pause“ und „Stopp“ ohne Gerät wirken auf **alles, was gerade läuft**.
- Lauter/Leiser ändern die Lautstärke in Schritten von 10 Prozentpunkten.

## 3a. Handy und App
- **Menü unten:** Auf dem Handy zeigt die Leiste unten Übersicht, Würfel, Licht und Musik. Alles Weitere (Smart Home, Sense HAT, Anzeige, System, Einstellungen) steckt hinter **Mehr**. Ein Tippen daneben oder die Taste Esc schließt das Menü.
- **Als App ablegen:** Im Browser des Handys „Zum Startbildschirm hinzufügen“ (Android: Menü ⋮, iPhone: Teilen-Symbol) wählen. OctaPiHub erscheint dann mit eigenem Symbol und öffnet sich ohne Adressleiste. Das ist nur eine Verknüpfung auf dieselbe Seite; es gibt keinen Offline-Betrieb.

## 4. Licht (Philips Hue)
1. **Bridge eintragen:** „Hue verwenden“ einschalten und die **Adresse der Bridge** eintragen (Hue-App → Einstellungen → Bridges). *Erweiterte Verbindungseinstellungen* (Port, Zertifikat, Name im Zertifikat) brauchen Sie normalerweise nicht.
2. **Verbinden:** Die **runde Taste** an der Bridge drücken und innerhalb von 30 Sekunden *Mit Bridge verbinden* klicken. Der Schlüssel liegt danach in `secrets.env`, nie in der Oberfläche oder in Protokollen.
3. **Szenen:** *Szenen laden* liest Szenen **und Räume**. Mit *Testen* schalten Sie eine Szene probeweise.
4. **Lampen und Räume:** Pro Raum gibt es Testtasten *Ein, Aus, Heller, Dunkler, Nächste Farbe, Nächste Szene*.
5. **Verbindung trennen:** Häkchen „Ja, Verbindung trennen“, dann *Verbindung trennen*. Der Schlüssel wird gelöscht.
**Live-Rückmeldung:** Der Hub hört der Bridge zu und kennt so, ob ein Raum an oder aus ist, auch wenn Sie die Hue-App benutzen. „Ein/Aus umschalten“ nutzt diesen Zustand. Oben steht, ob die Rückmeldung aktiv ist und welche Räume an oder aus sind. Ist sie nicht verbunden, fragt der Hub die Bridge bei jedem Umschalten ab; er verbindet sich automatisch neu (1 bis 60 Sekunden Pause).

Der Zustand der Verbindung steht oben (*Verbunden*, *Nicht gekoppelt*, *Aus*) mit Fehlercode, falls etwas nicht stimmt.

## 5. Musik
Die Seite hat drei Teile.

### Schritt 1 – Musik einschalten
- **Musik verwenden:** Hauptschalter. Aus = alle Musik-Aktionen tun nichts (die Meldung sagt das).
- **Musikordner:** Ordner auf dem Pi mit Ihrer Musik, z. B. `/srv/music`. Es werden **nur Dateien aus diesem Ordner** abgespielt.
- **Adresse dieses Geräts:** Meist leer lassen. Nur nötig, wenn Google-Lautsprecher Dateien aus dem Musikordner holen sollen und die automatische Erkennung nicht stimmt.

### Schritt 2 – Wiedergabegeräte
Hier legen Sie fest, **wo** Musik spielt. Pro Gerät: Name, Art, Gerätename, Lautstärke.

| Art | Wofür | Gerätename |
|---|---|---|
| **Google Nest / Hub** | Google-Lautsprecher und Displays im WLAN | Name wie in der Google-Home-App; *Google-Geräte suchen* zeigt die Namen |
| **Raspberry-Pi-Ausgang** | Kopfhörerbuchse, HDMI, USB-Soundkarte | leer = Standard; *Audioausgänge anzeigen* listet die Namen |
| **Bluetooth-Box** | Box, mit dem Pi gekoppelt | Bluetooth-Adresse `AA:BB:CC:DD:EE:FF` |

- Es spielt **immer nur ein Gerät**: Startet Musik auf einem anderen, stoppt das vorherige.
- Jedes gespeicherte Gerät zeigt Zustand (**Bereit, Spielt, Pausiert, Lädt, Fehler**), Titel und Lautstärke und hat Tasten für Zurück, Pause/Weiter, Weiter, Stopp, Leiser, Lauter. *Alles stoppen* stoppt alle Geräte, *Status aktualisieren* fragt neu ab.
- **Entfernen:** Häkchen „Dieses Gerät entfernen“, speichern. Ein Gerät, das noch von einer Playlist genutzt wird, lässt sich erst entfernen, wenn Sie die Playlist umstellen.

### Schritt 3 – Playlists
Eine Playlist ist eine **Quelle** plus Wiedergabe-Vorlieben.

| Quelle | Beispiel |
|---|---|
| Ordner im Musikordner (relativ oder vollständig) | `Jazz/Album` |
| Einzelne Datei | `Jazz/Album/01 Titel.mp3` |
| Playlist-Datei (`.m3u`, `.m3u8`, `.pls`) im Musikordner | `Morgens.m3u` |
| Radio-/Stream-Adresse | `https://radio.example.org/stream.mp3` |
| YouTube-Video oder -Playlist (auch YouTube Music) | `https://www.youtube.com/watch?v=…` |

- Ordner werden **mit Unterordnern** in natürlicher Reihenfolge (`2` vor `10`) gelesen; versteckte Dateien und Verknüpfungen aus dem Ordner heraus werden übersprungen. Höchstens 1000 Titel je Playlist.
- **Abspielen auf:** ein festes Gerät oder „Zuletzt verwendetes Gerät“.
- **Wiederholen:** Nicht, ganze Playlist oder einen Titel endlos. **Zufällige Reihenfolge** mischt beim Start.
- **Lautstärke:** leer = wie das Gerät.
- **Spotify** wird erkannt, aber **nicht abgespielt** (Spotify erlaubt das nur in seinen eigenen Apps).
- *Jetzt abspielen (Test)* startet eine gespeicherte Playlist sofort.
- **Zufall bei Online-Quellen:** lokal und bei YouTube über den Pi funktioniert Zufall. YouTube-Playlists auf einem **Google-Gerät** lassen sich nicht mischen.
Mit *Einstellungen speichern* wird alles auf der Seite zusammen geprüft. Fehler stehen direkt am Feld, und es wird dann **nichts** gespeichert.

## 6. Smart Home
Steuert andere Systeme mit Würfelseiten. Ausführlich erklärt in [Smart Home](smarthome.md). Kurz:
- **Verbindungen:** ein MQTT-Broker oder Home Assistant (Adresse, bei MQTT Benutzername; Passwort/Token werden nur in `secrets.env` gespeichert).
- **Aktionen:** *Webhook* (Web-Adresse aufrufen), *MQTT-Nachricht senden*, *Home-Assistant-Dienst aufrufen*. Jede hat *Jetzt senden (Test)* und zeigt, ob der letzte Versuch geklappt hat.
- Die Aktionen wählen Sie danach auf der Seite *Würfel* unter **Smart Home**.

## 7. Sense HAT
Zeigt Temperatur, Luftfeuchtigkeit und Luftdruck (weitere Sensoren lassen sich einschalten) **live** und als **Diagramm** für 1 Stunde, 24 Stunden, 7 oder 30 Tage. Die Temperatur wird neben dem Rohwert **korrigiert** angezeigt (der Pi heizt das Sense HAT auf). Mit „Temperatur ohne Korrektur zeigen“ sehen Sie die Rohwerte. Neue Werte erscheinen im Diagramm mit einigen Minuten Verzögerung, weil der Verlauf gebündelt gespeichert wird, um die SD-Karte zu schonen.

## 8. Anzeige und Joystick
Die 8×8-LEDs zeigen eine von **21 Ansichten**; der Joystick steuert sie.

| Bewegung | Wirkung |
|---|---|
| ◀ ▶ | Ansicht wechseln |
| ▲ ▼ | in der Ansicht auswählen (oder Helligkeit ändern) |
| Mitte drücken | Erklärung als Laufschrift |
| Mitte lange halten | Anzeige aus/ein |

**Ansichten:** Übersicht (Ampel je Komponente), Würfel, Steuerung, Musik, Akku, Temperatur, Luftfeuchtigkeit, Luftdruck, Verlauf, Uhr, Binäruhr, Laufschrift, Kompass, Wasserwaage, Umgebungsfarbe, Lampe, Regenbogen, Atmen, Farbpalette, Helligkeit und Aus. Welche im Wechsel erscheinen, stellen Sie unter *Einstellungen → LED-Anzeige* ein.

- **Musik-Ansicht:** ▶ grün blinkend = spielt, ‖ gelb = pausiert, Note grau = bereit, rotes ✕ = Fehler. Der Balken am rechten Rand zeigt die Lautstärke. Mitte drücken zeigt den Titel als Laufschrift.
- **Steuerung-Ansicht:** Hier löst der Joystick **seine Aktionen** aus: kurz drücken = Aktion der Richtung (▲ ▶ ▼ ◀ ●). Zum Wechseln der Ansicht ◀ oder ▶ **lange** halten, zum Ein-/Ausschalten die Mitte lange halten. Welche Richtung was auslöst, legen Sie auf der Seite *Würfel* im Block „Joystick“ fest.
- **Rückmeldung nach einer Würfelaktion:** erst die Seitenzahl, dann ein Häkchen (geklappt) oder Kreuz (fehlgeschlagen). Symbole blinken bei Verbindungsverlust, schwachem Akku und Fehlern.
- Die Seite **Anzeige** zeigt alles live im Browser, mit **virtuellem Joystick** zum Ausprobieren und Helligkeitsregler.
Bei gedrehter Anzeige (Einstellung *Drehung*) drehen die Joystick-Richtungen mit. **Das wurde an echter Hardware nicht geprüft.**

## 9. System
Gerätezustand auf einen Blick: Laufzeit, Auslastung, Arbeitsspeicher, **CPU-Temperatur**, **Unterspannung/Drosselung**, Speicherplatz. Darunter die **letzten Meldungen** des Programms (filterbar nach Stufe, Zugangsdaten sind unkenntlich gemacht) und das **Diagnose-Paket**: ein ZIP ohne Geheimnisse, das Sie bei Problemen weitergeben können.

## 10. Einstellungen
Hier stellen Sie das Sense HAT ein. Jedes Feld hat ein ⓘ; hier die Übersicht.

| Feld | Bedeutung |
|---|---|
| Sense HAT verwenden | Hauptschalter für Messwerte, Anzeige und Joystick |
| Hardware-Version | Automatisch, Version 1 oder 2 (Version 2 hat einen Farbsensor) |
| Temperatur: Faktor, Versatz | Gleicht die Aufheizung durch den Pi aus |
| Farbsensor: Verstärkung, Zyklen | Empfindlichkeit und Messdauer (nur Version 2) |
| Sensorgruppen | je Gruppe: einschalten, Messintervall, Verlauf, Darstellung, Nachkommastellen |
| Verlauf: einschalten, Aufbewahrung (Tage) | Wie lange Messwerte gespeichert werden |
| Joystick | ein/aus |
| LED-Anzeige | einschalten, Drehung, schwaches Licht, Umgebungshelligkeit, Helligkeit, Startansicht, Ansichten, automatisches Weiterschalten, Nachtmodus (Zeiten, Modus) |

Änderungen werden **ohne Neustart** angewendet. Ungültige Werte werden direkt am Feld erklärt, und es wird dann nichts gespeichert. Ganz oben führt ein Link zu **Sichern & Wiederherstellen**.

### Uhrzeit und Zeitzone
Oben auf der Seite *Einstellungen* steht **„Uhrzeit und Zeitzone“**. Die **Uhr selbst** stellt der Raspberry Pi über das Internet (NTP) automatisch; die Seite *System* zeigt, ob sie abgeglichen ist. Hier wählen Sie nur die **Zeitzone der Anzeige**: leer = die Zeitzone, die am Gerät eingestellt ist (automatisch), sonst ein Eintrag wie `Europe/Zurich`. Sie gilt sofort für die Oberfläche, die **LED-Uhr** und den **Nachtmodus**. Ein Raspberry Pi hat keine Batterie-Uhr: nach einem Stromausfall geht die Uhr falsch, bis das Netz wieder da ist.

## 11. Sichern & Wiederherstellen
Unter *Einstellungen → Sichern & Wiederherstellen*.

### Exportieren
Wählen Sie die Bereiche (Musik, nur Playlists, Zuordnung, Namen und Farben, ein einzelner Würfel, Sense HAT, Hue-Einstellungen) und laden Sie die Export-Datei (`.json`) herunter. Sie enthält **keine Schlüssel, Passwörter oder Token** und nicht die Liste der Würfel. **Ein einzelner Würfel** exportiert dessen Belegung samt Namen und Farben ohne Bluetooth-Adresse.

### Importieren
1. Datei auswählen (der Inhalt erscheint im Textfeld, Sie können ihn auch einfügen) und *Datei prüfen*. Jeder Bereich wird gegen **diese** Installation geprüft und zusammengefasst. Bereiche, die nicht passen, sind markiert.
2. Bereiche ankreuzen und *Ausgewählte Bereiche übernehmen*. **Alles oder nichts:** Schlägt etwas fehl, bleibt alles wie es war. Vorher wird die bisherige Konfigurationsdatei als `octapihub.toml.bak-<Zeit>` gesichert (die neuesten fünf bleiben).
- **Einzelner Würfel:** Wählen Sie bei „anwenden auf“ den Zielwürfel; dessen bisherige Zuordnung wird ersetzt. So übertragen Sie eine Belegung auf einen weiteren Würfel.
- „Nur Playlists“ ergänzt vorhandene Playlists und entfernt Gerätebezüge, die es hier nicht gibt (mit Hinweis).
- Pfade der Datenbank bleiben unverändert. Beim Import der Hue-Einstellungen müssen Sie ggf. neu koppeln.

### Messdaten und Protokolle
- **CSV:** Messgröße, Zeitraum (1 Stunde bis 30 Tage) und Genauigkeit (Mittelwerte oder einzelne Messwerte bis 24 Stunden). Öffnet sich in Excel/LibreOffice (Zeiten in UTC, Temperatur mit Rohwert und korrigiertem Wert).
- **Logdateien:** letzte Meldungen (alle oder nur Warnungen und Fehler) oder die Logdatei vom Gerät, ohne Zugangsdaten.

## 12. Hilfe
Diese Seiten. Oben rechts auf **Hilfe** klicken. Die **Suche** findet Wörter in allen Dokumenten und Fehlercodes. Unter *Fehlercodes* steht zu jedem `CH-…`-Code, was er bedeutet und was zu tun ist.

## 13. Meldungen und Fehler
Fehlermeldungen erscheinen in einem Kasten mit **Erklärung in normaler Sprache** und einem **Fehlercode**. Den Code schlagen Sie unter **Hilfe → Fehlercodes** nach. Bei einem unerwarteten Fehler erscheint zusätzlich eine **Kennung**, die Sie im Protokoll wiederfinden. Ausführlich: [Fehlersuche und Protokolle](fehlersuche.md).

**So geht OctaPiHub mit Fehlern um**
- Fällt eine Komponente aus (z. B. Sense HAT fehlt, Lautsprecher aus), **läuft alles andere weiter**.
- Aktionen laufen über eine **Warteschlange** je Würfel in der richtigen Reihenfolge. Vorübergehende Fehler werden mit Pausen wiederholt, abgelaufene und nicht behebbare nicht.
- Eine ungültige Konfiguration wird nie angewendet; gespeichert wird **atomar** (ganz oder gar nicht). Ist die Konfigurationsdatei beim Start unbrauchbar, nutzt der Hub die neueste gültige **Sicherung** und zeigt einen Hinweis.
