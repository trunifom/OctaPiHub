# OctaPiHub – Benutzerhandbuch

Dieses Handbuch erklärt die **Bedienung**. Wie Sie OctaPiHub auf dem Raspberry Pi installieren,
steht in [deploy/INSTALL.md](../deploy/INSTALL.md).

> **Ehrlicher Stand:** OctaPiHub wurde bisher nur mit Simulationen (Fake-Würfel, Fake-Sense-HAT,
> Fake-Lautsprecher, Fake-Hue-Bridge) getestet, **nicht** mit echten Würfeln, einem echten Sense
> HAT, einer echten Hue Bridge, echten Google-Lautsprechern oder Bluetooth-Boxen. Was sich auf
> echter Hardware anders verhält, finden Sie nach der Inbetriebnahme (INSTALL.md, Abschnitt 11)
> heraus. Das Handbuch beschreibt, wie die Software gedacht ist.

## Inhalt
1. [Die Idee in einer Minute](#1-die-idee-in-einer-minute)
2. [Die Würfel und ihre Seiten](#2-die-würfel-und-ihre-seiten)
3. [Die Weboberfläche im Überblick](#3-die-weboberfläche-im-überblick)
4. [Erste Schritte: Licht](#4-erste-schritte-licht)
5. [Musik einrichten](#5-musik-einrichten)
6. [Würfelseiten programmieren](#6-würfelseiten-programmieren)
7. [Der Joystick und die LED-Anzeige](#7-der-joystick-und-die-led-anzeige)
8. [Sensoren und Verlauf](#8-sensoren-und-verlauf)
9. [Sichern, Wiederherstellen, Exportieren](#9-sichern-wiederherstellen-exportieren)
10. [Meldungen, Fehlercodes und Protokolle](#10-meldungen-fehlercodes-und-protokolle)
11. [Sicherheit und Datenschutz](#11-sicherheit-und-datenschutz)
12. [Häufige Fragen](#12-häufige-fragen)

## 1. Die Idee in einer Minute
Sie legen einen Würfel auf eine Seite, und **eine Aktion passiert**: Licht an, Musik spielt, Musik
pausiert. Der Raspberry Pi (der „Hub“) ist die Schaltzentrale im Heimnetz. Alles läuft lokal, es
gibt kein Konto und keinen Cloud-Dienst von OctaPiHub.

- Sie können bis zu **drei Würfel** nutzen. Jeder kann alles: z. B. ein Würfel für Lichtszenen, einer
  für Playlists, einer für Besonderes. Die „Rolle“ beim Würfel ist nur eine Beschriftung.
- Das **Sense HAT** (Aufsatz mit LED-Matrix, Joystick und Sensoren) kann zusätzlich Anzeige,
  Messwerte und fünf weitere Auslöser liefern (der Joystick).
- Jede **Aktion** ist ein Schritt wie „Hue-Szene Abend einschalten“, „Playlist Morgenmusik
  abspielen“ oder „Lauter“.

## 2. Die Würfel und ihre Seiten
Ein Würfel hat acht Seiten (1 bis 8). Dazu kommt die **Halterung** (Seite 0): der Zustand, in dem
der Würfel in seiner Halterung steht, also keine Seite eindeutig oben liegt. Beides lässt sich
wie eine Seite belegen, z. B. „Halterung → Alle Wiedergaben stoppen“.

- Eine Aktion wird ausgelöst, wenn der Würfel **auf eine Seite gelegt wird**. Der erste Wert nach
  dem Verbinden löst nichts aus (er legt nur fest, wo der Würfel liegt).
- Kippt der Würfel mehrfach schnell, zählt nur die letzte Seite (Entprellung, Standard 300 ms).
- Ihre Seitennummern sind die Zahlen, die die Software meldet. **Welche physische Seite welche Zahl hat,
  sehen Sie beim Ausprobieren** auf der Seite *Würfel* („zuletzt erkannte Seite“). Notieren Sie es und
  beschriften Sie den Würfel nach Wunsch.

Mit **Jetzt auslösen** (Seite *Würfel*) können Sie eine Zuordnung testen, ohne den Würfel zu bewegen.

## 3. Die Weboberfläche im Überblick
Öffnen Sie im Browser die Adresse Ihres Raspberry Pi (z. B. `http://192.168.1.50:8080`).

| Seite | Wofür |
|---|---|
| **Übersicht** | Ampel für Würfel, Licht, Sense HAT und Gerätezustand |
| **Würfel** | Verbindung, Akku, zuletzt erkannte Seite, **Zuordnung Seite → Aktion** |
| **Licht** | Hue Bridge verbinden, Szenen laden und testen |
| **Musik** | Wiedergabegeräte, Playlists, Steuerung, Test |
| **Sense HAT** | Messwerte live und als Diagramm |
| **Anzeige** | LED-Matrix live, alle Ansichten, virtueller Joystick |
| **System** | Gerätezustand, Meldungen, Diagnose-Paket |
| **Einstellungen** | Sensoren, Verlauf, LED-Anzeige; Link zu **Sichern & Wiederherstellen** |

Oben rechts wechseln Sie zwischen **Automatisch / Hell / Dunkel** und zwei Schriftgrößen. Auf dem
Handy erscheint die Navigation unten.

**Ansehen oder ändern?** Ansehen kann jeder im Heimnetz. **Ändern** (speichern, abspielen, koppeln,
exportieren) braucht das **Zugriffs-Token**. Tragen Sie es am unteren Rand der Seite ein; der Browser
merkt es sich nur, solange der Tab offen ist. Ist kein Token eingerichtet, sind alle Schaltflächen
zum Ändern ausgegraut.

## 4. Erste Schritte: Licht
1. Seite **Licht**: Adresse der Bridge eintragen (Hue-App → Einstellungen → Bridges) und speichern.
2. Die **runde Taste** an der Bridge drücken und innerhalb von 30 Sekunden *Mit Bridge verbinden*.
3. *Szenen laden*. Mit *Testen* schalten Sie eine Szene probeweise.
4. Seite **Würfel**: pro Seite eine Szene wählen, *Speichern*. Die Zuordnung gilt sofort.

Der Hue-Schlüssel liegt in `secrets.env` auf dem Gerät und erscheint nirgends in der Oberfläche
oder in Protokollen.

## 5. Musik einrichten
Seite **Musik**. Sie hat drei Teile.

### Schritt 1 – Musik einschalten
- **Musik verwenden:** Hauptschalter. Aus = alle Musik-Aktionen tun nichts (die Meldung sagt das).
- **Musikordner:** Ordner auf dem Raspberry Pi mit Ihrer Musik (z. B. `/srv/music`). Es werden **nur
  Dateien aus diesem Ordner** abgespielt.
- **Adresse dieses Geräts:** Meist leer lassen. Nur nötig, wenn Google-Lautsprecher Dateien aus dem
  Musikordner holen sollen und die automatische Erkennung der Adresse nicht stimmt.

### Schritt 2 – Wiedergabegeräte
Hier legen Sie fest, **wo** Musik spielt. Pro Gerät: Name, Art, Gerätename, Lautstärke.

| Art | Wofür | Gerätename |
|---|---|---|
| **Google Nest / Home / Nest Hub** | Google-Lautsprecher und Displays im WLAN | Name wie in der Google-Home-App; *Google-Geräte suchen* zeigt die Namen |
| **Audioausgang dieses Raspberry Pi** | Kopfhörerbuchse, HDMI, USB-Soundkarte | leer = Standard; *Audioausgänge anzeigen* listet die Namen |
| **Bluetooth-Box** | Box, mit dem Pi gekoppelt | Bluetooth-Adresse `AA:BB:CC:DD:EE:FF` |

- Es spielt **immer nur ein Gerät**: Startet Musik auf einem anderen Gerät, stoppt das vorherige.
- Jedes gespeicherte Gerät zeigt seinen Zustand (**Bereit, Spielt, Pausiert, Lädt, Fehler**), den
  Titel und die Lautstärke und hat Tasten für Zurück, Pause/Weiter, Weiter, Stopp, Leiser, Lauter.
  *Alles stoppen* stoppt alle Geräte. *Status aktualisieren* fragt die Geräte neu ab.
- **Entfernen:** Häkchen „Dieses Gerät entfernen“, dann speichern. Ein Gerät, das noch von einer
  Playlist verwendet wird, lässt sich erst entfernen, wenn Sie die Playlist umstellen.

### Schritt 3 – Playlists
Eine Playlist ist eine **Quelle** plus Wiedergabe-Vorlieben.

| Quelle | Beispiel |
|---|---|
| Ordner im Musikordner (relativ oder vollständig) | `Jazz/Album` |
| Einzelne Datei | `Jazz/Album/01 Titel.mp3` |
| Playlist-Datei (`.m3u`, `.m3u8`, `.pls`) im Musikordner | `Morgens.m3u` |
| Radio-/Stream-Adresse | `https://radio.example.org/stream.mp3` |
| YouTube-Video oder -Playlist | `https://www.youtube.com/watch?v=…` |

- Ordner werden **mit Unterordnern** in natürlicher Reihenfolge (`2` vor `10`) gelesen, versteckte
  Dateien und Verknüpfungen aus dem Ordner heraus werden übersprungen. Pro Playlist höchstens 1000 Titel.
- **Abspielen auf:** ein festes Gerät oder „Zuletzt verwendetes Gerät“.
- **Wiederholen:** Nicht, Alles oder Titel. **Zufällige Reihenfolge:** mischt beim Start.
- **Lautstärke:** leer = Standard des Geräts.
- **Spotify** wird erkannt, aber nicht abgespielt; die Seite sagt das beim Speichern.
- *Jetzt abspielen (Test)* startet eine gespeicherte Playlist sofort.

**Speichern:** Alles auf der Seite wird mit *Einstellungen speichern* zusammen geprüft. Fehler stehen
direkt am Feld, und es wird dann **nichts** gespeichert. Erfolgreiche Änderungen gelten sofort.

## 6. Würfelseiten programmieren
Seite **Würfel → Zuordnung**. Jede Karte steht für eine Seite eines Würfels (plus „Halterung“ und die
fünf Joystick-Richtungen). Im Auswahlfeld wählen Sie **eine Aktion**:

| Gruppe | Aktionen |
|---|---|
| **Licht: Szene einschalten** | jede geladene Hue-Szene |
| **Musik: Playlist abspielen** | eine Ihrer Playlists (▶) |
| **Musik: Playlist starten oder pausieren** | eine Playlist; läuft sie, pausiert die Seite, ist sie pausiert, setzt sie fort (⏯) |
| **Musik: Steuern** | Pause / Weiter, Pause, Weiter, Stopp, Alle Wiedergaben stoppen, Nächster Titel, Vorheriger Titel, Lauter, Leiser |

**Beispiele für eine Würfelaufteilung**
- *Licht-Würfel:* Seite 1 „Hell“, 2 „Gemütlich“, 3 „Film“, 4 „Nachtlicht“, Halterung „Alles aus“.
- *Musik-Würfel:* 1 „Morgenmusik“, 2 „Jazz“, 3 „Radio“, 4 Pause/Weiter, 5 Nächster Titel, 6 Leiser,
  7 Lauter, Halterung „Alle Wiedergaben stoppen“.
- *Dritter Würfel:* was Ihnen einfällt, z. B. Hue-Szenen für das Schlafzimmer.

**Gut zu wissen**
- Eine Aktion wird **nicht nachgeholt**, wenn sie zu spät käme. Musik, die länger als etwa eine
  Minute nicht gestartet werden konnte (Gerät aus), wird verworfen, damit sie nicht plötzlich später spielt.
- Fehler, die Sie beheben müssen (Gerät gelöscht, Musik aus, Quelle fehlt), werden nicht ständig
  wiederholt; Verbindungsfehler schon, kurz und mit Pausen.
- „Pause“ und „Stopp“ ohne Gerätenamen wirken auf **alles, was gerade läuft**.
- „Lauter/Leiser“ ändern die Lautstärke in 10er-Schritten.
- Zuordnungen, die mehrere Aktionen hintereinander enthalten (nur in der Konfigurationsdatei
  möglich), erscheinen **gesperrt** mit ihrer Beschreibung und bleiben beim Speichern unverändert.

## 7. Der Joystick und die LED-Anzeige
Die 8×8-LEDs zeigen eine von **21 Ansichten**; der Joystick steuert sie.

| Bewegung | Wirkung |
|---|---|
| ◀ ▶ | Ansicht wechseln |
| ▲ ▼ | in der Ansicht auswählen (oder Helligkeit ändern) |
| Mitte drücken | Erklärung als Laufschrift |
| Mitte lange halten | Anzeige aus/ein |

Ansichten: Übersicht (Ampel je Komponente), Würfel, **Steuerung**, **Musik**, Akku, Temperatur,
Luftfeuchtigkeit, Luftdruck, Verlauf, Uhr, Binäruhr, Laufschrift, Kompass, Wasserwaage,
Umgebungsfarbe, Lampe, Regenbogen, Atmen, Farbpalette, Helligkeit und Aus. Welche davon im
Wechsel erscheinen, stellen Sie unter *Einstellungen → LED-Anzeige* ein.

- **Musik-Ansicht:** ▶ grün blinkend = spielt, ‖ gelb = pausiert, Note grau = bereit, rotes ✕ = Fehler.
  Der Balken am rechten Rand zeigt die Lautstärke. Mitte drücken zeigt den Titel als Laufschrift.
- **Steuerung-Ansicht:** Hier löst der Joystick **seine Aktionen** aus: kurz drücken = Aktion der
  Richtung (▲ ▶ ▼ ◀ ●). Zum Wechseln der Ansicht ◀ oder ▶ **lange** halten, zum Ein-/Ausschalten der
  Anzeige die Mitte lange halten. Welche Richtung welche Aktion hat, legen Sie auf der Seite
  *Würfel* im Block „Joystick“ fest (z. B. ▲ Lauter, ▼ Leiser, ● Pause/Weiter).
- **Kurzer Hinweis nach einer Würfelaktion:** erst die Seitenzahl, dann ein Häkchen (geklappt) oder
  Kreuz (fehlgeschlagen). Symbole blinken bei Verbindungsverlust, schwachem Akku und Fehlern.
- Die Seite **Anzeige** zeigt alles live im Browser und hat einen **virtuellen Joystick** zum
  Ausprobieren.

Hinweis: Bei gedrehter Anzeige (Einstellung *Drehung*) gelten die Joystick-Richtungen so, wie die
Software sie annimmt. **Dies wurde an echter Hardware nicht geprüft.**

## 8. Sensoren und Verlauf
Seite **Sense HAT** zeigt Temperatur, Luftfeuchtigkeit und Luftdruck (weitere Sensoren lassen sich
einschalten) live, mit Diagramm für 1 Stunde, 24 Stunden, 7 oder 30 Tage. Die Temperatur wird
neben dem Rohwert **korrigiert** angezeigt (der Raspberry Pi heizt das Sense HAT auf; Faktor und
Versatz stellen Sie in den Einstellungen ein). Jede Sensorgruppe hat ihr eigenes Messintervall und
lässt sich einzeln für den Verlauf ein- und ausschalten. Der Verlauf wird nach der eingestellten
Aufbewahrungszeit automatisch gelöscht.

## 9. Sichern, Wiederherstellen, Exportieren
**Einstellungen → Sichern & Wiederherstellen**

**Exportieren.** Wählen Sie die Bereiche (Musik, nur Playlists, Zuordnung, Sense HAT, Hue-Einstellungen)
und laden Sie die Export-Datei (`.json`) herunter. Sie enthält **keine Schlüssel oder Token** und nicht die
Liste der Würfel. So übertragen Sie Ihre Einrichtung auf ein anderes Gerät oder sichern sie.

**Importieren** in zwei Schritten:
1. Datei auswählen (der Inhalt erscheint im Textfeld, Sie können ihn auch einfügen) und *Datei prüfen*.
   Jeder Bereich wird gegen **diese** Installation geprüft und mit einer Zusammenfassung angezeigt. Bereiche,
   die nicht passen (z. B. eine Zuordnung für Würfel, die es hier nicht gibt), sind markiert und lassen sich
   nicht übernehmen.
2. Bereiche ankreuzen und *Ausgewählte Bereiche übernehmen*. **Alles oder nichts:** Schlägt etwas fehl,
   bleibt alles wie es war. Vorher wird die bisherige Konfigurationsdatei als `octapihub.toml.bak-<Zeit>`
   daneben gesichert (die neuesten fünf bleiben).

Besonderheiten: „Nur Playlists“ ergänzt vorhandene Playlists (gleiche Kennung = ersetzt) und entfernt
Gerätebezüge, die es hier nicht gibt (mit Hinweis). Pfade der Datenbank bleiben unverändert. Beim Import
der Hue-Einstellungen müssen Sie ggf. neu koppeln (der Schlüssel wird nie übertragen).

**Messdaten als CSV.** Wählen Sie Messgröße (alle oder eine), Zeitraum (1 h bis 30 Tage) und Genauigkeit
(Mittelwerte oder einzelne Messwerte bis 24 Stunden). Die Datei öffnet sich in Excel/LibreOffice (Zeiten in UTC;
Temperatur mit Rohwert und korrigiertem Wert).

**Logdateien.** Letzte Meldungen (alle oder nur Warnungen und Fehler) oder die Logdatei vom Gerät. Zugangsdaten
sind unkenntlich gemacht und Geräteadressen gekürzt.

## 10. Meldungen, Fehlercodes und Protokolle
Fehlermeldungen erscheinen in einem Kasten mit einer **Erklärung in normaler Sprache** und einem
**Fehlercode**. Der Code hilft bei der Suche.

| Code | Bedeutung |
|---|---|
| CH-CFG-001/002 | Konfiguration lässt sich nicht lesen / enthält ungültige Werte |
| CH-BLE-001…003 | Bluetooth/Würfel: Verbindung, unerwartete Daten, fehlende Funktion |
| CH-SENSE-001/002 | Sense HAT nicht ansprechbar / Funktion nicht verfügbar |
| CH-DB-001/002 | Datenbank (Aktionen, Verlauf) |
| CH-SEC-001 | Zugangsdatei hat unsichere Rechte oder ist ungültig |
| CH-HUE-001…005 | Hue: Bridge, Zertifikat, Kopplung (Taste), Schlüssel |
| CH-MUS-001 | Musik allgemein |
| CH-MUS-002 | Wiedergabegerät antwortet nicht (aus, nicht im Netz, nicht verbunden, mpv fehlt) |
| CH-MUS-003 | Quelle ungültig, nicht gefunden, außerhalb des Musikordners, Spotify |
| CH-MUS-004 | Musik aus, kein Gerät gewählt oder Playlist/Gerät nicht vorhanden |
| CH-MUS-005 | Es fehlt ein Programm oder Paket |
| CH-PLUG-001 | Eine Aktionsart ist nicht installiert |
| CH-WEB-003 / 004 | Kein / falsches Zugriffs-Token |
| CH-WEB-008 | Token zu kurz oder Platzhalter (der Dienst startet dann nicht) |
| CH-WEB-011 | Ungültige Auswahl |
| CH-WEB-014 | Musik ist in diesem Modus nicht verfügbar |

**Protokolle**
- Seite **System:** letzte Meldungen, filterbar nach Stufe.
- **Logdatei** auf dem Gerät: `/var/lib/octapihub/logs/octapihub.log` (rotierend, höchstens ca. 6 MB).
- Ausführlichkeit: `OCTAPIHUB_LOG_LEVEL` = `DEBUG`, `INFO`, `WARNING` oder `ERROR` (Dienst neu starten).
- **Diagnose-Paket** (System): ZIP ohne Geheimnisse, zum Weitergeben bei Problemen.

**So geht OctaPiHub mit Fehlern um**
- Ein Ausfall einer Komponente (z. B. Sense HAT fehlt, Lautsprecher aus) **stoppt nichts anderes**.
- Aktionen laufen über eine **Warteschlange** je Würfel in der richtigen Reihenfolge; vorübergehende Fehler werden
  mit Pausen wiederholt, abgelaufene und nicht behebbare nicht.
- Eine ungültige Konfiguration wird nie angewendet; die letzte gültige Datei bleibt unverändert.
- Wird die Konfiguration gespeichert, geschieht das **atomar** (ganz oder gar nicht).

## 11. Sicherheit und Datenschutz
- Alles läuft im Heimnetz. **Geben Sie keine Ports im Router frei.** Die Oberfläche hat keine
  Verschlüsselung (HTTP) und keine Benutzerkonten; Änderungen schützt das Token.
- Das Token ist nur so sicher wie Ihr Netz: Teilen Sie es nicht und nutzen Sie ein langes, zufälliges.
- Musikdateien werden nur aus dem Musikordner gelesen (auch Playlist-Dateien dürfen nicht hinaus verweisen).
  Google-Lautsprecher bekommen Dateien über zufällige, ablaufende Adressen.
- Hue-Schlüssel, Token und Hue-Bridge-Adresse werden in Protokollen und im Diagnose-Paket verborgen.
- Es werden keine Daten an Dritte gesendet, außer an die von Ihnen gewählten Dienste (Hue Bridge,
  Google-Lautsprecher, die von Ihnen eingetragenen Radio-/YouTube-Adressen).

## 12. Häufige Fragen
**Ein Würfel löst nichts aus.** Seite *Würfel*: Ist er „Bereit“? Ändert sich „zuletzt erkannte Seite“? Ist für die
Seite eine Aktion gespeichert? Probieren Sie *Jetzt auslösen*. Ist sie ausgelöst worden, aber nichts passiert,
zeigt die Meldung (System) den Grund.

**Die Musik startet nicht.** Seite *Musik*: Ist „Musik verwenden“ an? Funktioniert *Jetzt abspielen (Test)*?
Hat das Gerät den Zustand „Fehler“? Der Fehlercode steht daneben.

**Auf dem Google-Lautsprecher spielt Radio, aber keine Datei aus dem Musikordner.** Der Dienst läuft vermutlich
nur auf `127.0.0.1`. Setzen Sie `OCTAPIHUB_HOST` auf die LAN-Adresse (INSTALL.md, Abschnitt 8).

**Ich habe das Token vergessen.** Es steht in `/etc/octapihub/octapihub.env` auf dem Raspberry Pi.

**Wie sichere ich alles?** Export-Datei (Abschnitt 9) plus `tar` von `/etc/octapihub` und `/var/lib/octapihub`
(INSTALL.md, Abschnitt 10).

**Wie viele Würfel?** Bis zu drei, mit je neun belegbaren Zuständen (acht Seiten und die Halterung).
