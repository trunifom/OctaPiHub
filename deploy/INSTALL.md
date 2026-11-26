# OctaPiHub auf dem Raspberry Pi installieren

> **Stand:** Diese Anleitung und die Dateien in `deploy/` wurden noch **nicht auf einem echten
> Raspberry Pi ausprobiert**. Sie sind aus der Dokumentation von systemd, BlueZ, mpv und
> pychromecast abgeleitet und durch Tests auf Konsistenz mit der Software geprüft
> (Befehlszeile, Beispielkonfiguration, Dienstdatei). Melden Sie Abweichungen, sobald Sie sie
> auf dem Gerät finden. Wie man die Hardware Schritt für Schritt prüft, steht in Abschnitt 11.

Die Anleitung ist so aufgebaut, dass Sie sie von oben nach unten abarbeiten können. Jeder
Abschnitt endet mit einer **Kontrolle**: Wenn sie nicht stimmt, gehen Sie zu Abschnitt 12.

## Inhalt
1. Was Sie brauchen
2. Betriebssystem vorbereiten
3. Benutzer und Ordner
4. Programm installieren
5. Würfel koppeln und Adressen finden
6. Konfiguration und Zugriffs-Token
7. Dienst einrichten und starten
8. Weboberfläche öffnen
9. Musik einrichten (Google Nest, Raspberry Pi, Bluetooth-Box)
10. Sichern, Aktualisieren, Entfernen
11. Hardware prüfen
12. Fehler suchen

## 1. Was Sie brauchen
| Teil | Hinweis |
|---|---|
| Raspberry Pi 3 oder neuer | Mit Raspberry Pi OS Lite (64 Bit empfohlen), mindestens Python 3.12 |
| 1 bis 3 Timeular-/ZEI-Würfel | Aufgeladen und in Reichweite (Bluetooth) |
| Sense HAT (optional) | Für LED-Anzeige, Joystick und Sensoren |
| Philips Hue Bridge (optional) | Für Lichtszenen |
| Google Nest / Home / Nest Hub (optional) | Im selben WLAN wie der Raspberry Pi |
| Lautsprecher am Raspberry Pi (optional) | 3,5-mm-Buchse, HDMI oder USB-Soundkarte |
| Bluetooth-Box (optional, eher selten) | Siehe Abschnitt 9.4, aufwendiger |

Der Raspberry Pi und die Geräte müssen im **selben Heimnetz** sein. OctaPiHub braucht keinen
Internetzugang für Würfel, Licht und lokale Musik; YouTube und Web-Radio brauchen ihn.

## 2. Betriebssystem vorbereiten
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3 python3-venv python3-pip git bluez
python3 --version        # muss 3.12 oder neuer sein
```
Hat Ihr System nur eine ältere Python-Version, installieren Sie Python 3.12 oder neuer
(z. B. aus einem neueren Raspberry-Pi-OS-Abbild) und verwenden Sie in den Befehlen unten dessen
Namen (z. B. `python3.12`).

**Sense HAT** (nur wenn vorhanden): I2C einschalten und das Paket installieren.
```bash
sudo raspi-config nonint do_i2c 0
sudo apt install -y sense-hat
```
**Musik** (nur wenn gewünscht):
```bash
sudo apt install -y mpv yt-dlp alsa-utils
```
`mpv` spielt Musik über den Raspberry Pi und Bluetooth-Boxen, `yt-dlp` liest YouTube-Links für
`mpv`, `alsa-utils` bringt `aplay -l` zum Auflisten der Tonausgänge mit.

**Kontrolle:** `bluetoothctl show` zeigt einen Adapter mit `Powered: yes`; mit Sense HAT zeigt
`ls /dev/i2c-1` eine Datei an.

## 3. Benutzer und Ordner
Der Dienst läuft nicht als Administrator, sondern als eigener, eingeschränkter Benutzer.
```bash
sudo useradd --system --create-home --home-dir /var/lib/octapihub --shell /usr/sbin/nologin octapihub
sudo usermod -aG bluetooth,i2c,input,audio octapihub
sudo install -d -o octapihub -g octapihub -m 0750 /etc/octapihub /var/lib/octapihub
sudo install -d -o root -g root -m 0755 /opt/octapihub
# Musikordner (nicht unter /home: der Dienst darf /home aus Sicherheitsgründen nicht lesen)
sudo install -d -o octapihub -g octapihub -m 0755 /srv/music
```
Kopieren Sie Ihre Musik später nach `/srv/music` (z. B. mit `scp` oder einem USB-Stick) und
legen Sie sie dort in Ordnern ab (`/srv/music/Jazz/…`).

**Kontrolle:** `id octapihub` zeigt die Gruppen `bluetooth`, `i2c`, `input` und `audio`.

## 4. Programm installieren
```bash
sudo git clone <Ihr-Repository> /opt/octapihub
sudo python3 -m venv /opt/octapihub/venv
sudo /opt/octapihub/venv/bin/pip install "/opt/octapihub[music]"
```
`[music]` installiert zusätzlich `pychromecast` für Google-Lautsprecher. Ohne Google-Geräte
genügt `pip install /opt/octapihub`. Auf einem Raspberry Pi dauert die Installation einige
Minuten.

**Kontrolle:** `/opt/octapihub/venv/bin/octapihub --help` zeigt die Befehlszeilenhilfe.

## 5. Würfel koppeln und Adressen finden
Ein Würfel muss dem Raspberry Pi bekannt sein. Würfel aufwecken (einmal kippen), dann:
```bash
sudo bluetoothctl
[bluetoothctl]# scan on          # warten, bis die Würfel erscheinen (der Name enthält meist "Timeular" oder "ZEI")
[bluetoothctl]# scan off
[bluetoothctl]# exit
```
Notieren Sie die Adresse jedes Würfels (Form `AA:BB:CC:DD:EE:FF`). Eine Bluetooth-Kopplung ist
nach unserem Kenntnisstand nicht nötig, OctaPiHub verbindet sich selbst (das ist ungeprüft; falls
der Würfel sich nicht verbindet, koppeln Sie ihn einmal mit `bluetoothctl pair <Adresse>`). Wenn Sie mit der Timeular-App am
Smartphone gekoppelt sind, trennen Sie den Würfel dort, denn ein Würfel spricht nur mit einem
Gerät.

## 6. Konfiguration und Zugriffs-Token
```bash
sudo install -o octapihub -g octapihub -m 0600 /opt/octapihub/deploy/octapihub.toml.example /etc/octapihub/octapihub.toml
sudo install -o octapihub -g octapihub -m 0600 /opt/octapihub/deploy/octapihub.env.example /etc/octapihub/octapihub.env
sudo nano /etc/octapihub/octapihub.toml      # Bluetooth-Adressen der Würfel eintragen
sudo nano /etc/octapihub/octapihub.env       # langes, zufälliges Token eintragen
```
In `octapihub.toml` ersetzen Sie die Beispieladressen durch Ihre 1 bis 3 Würfel (je ein Block
`[[cubes]]` mit `name`, `address`, `role`). Die **Rolle** (`light`, `music`, `general`) dient nur
der Beschriftung, jeder Würfel kann alles. Die Zuordnung Seite → Aktion machen Sie später in der
Weboberfläche.

Ein Token erzeugen Sie so; tragen Sie den Wert hinter `OCTAPIHUB_WEB_TOKEN=` ein:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```
Das Token schützt **alle Änderungen** in der Weboberfläche. Ein zu kurzes oder das Platzhalter-
Token aus der Beispieldatei verhindert den Start (Fehlercode `CH-WEB-008`). Die Datei
`secrets.env` (Hue-Schlüssel) legt OctaPiHub beim Koppeln selbst mit Rechten 0600 an. Dateien mit
offenen Rechten werden aus Sicherheitsgründen abgelehnt.

**Kontrolle:** `sudo -u octapihub /opt/octapihub/venv/bin/python -c "from octapihub.core.config import load_config; print(load_config('/etc/octapihub/octapihub.toml').cubes)"`
gibt Ihre Würfel aus. Bei einem Fehler nennt die Meldung Feld und Grund.

## Uhrzeit und Zeitzone
OctaPiHub **stellt die Uhr nicht selbst**: Das macht das Betriebssystem über das Internet (NTP). Prüfen Sie es einmal:
```bash
timedatectl status            # "System clock synchronized: yes" und "NTP service: active"
sudo timedatectl set-ntp true # falls NTP aus ist
sudo timedatectl set-timezone Europe/Zurich   # Zeitzone des Geräts (Liste: timedatectl list-timezones)
```
**Wichtig:** Ein Raspberry Pi hat **keine Batterie-Uhr**. Nach einem Stromausfall geht die Uhr falsch, bis das Netzwerk
wieder da ist und die Zeit abgeglichen wurde. Die Seite *System* zeigt, ob die Uhr abgeglichen ist; bis dahin können
Uhrzeiten in Verlauf und Protokoll abweichen. OctaPiHub zeigt Uhrzeiten standardmäßig in der **Zeitzone des Geräts**;
unter *Einstellungen → Uhrzeit und Zeitzone* können Sie eine andere wählen. Das gilt für die Oberfläche, die LED-Uhr und den
Nachtmodus. Gespeicherte Messwerte liegen intern immer in UTC und werden nur beim Anzeigen umgerechnet.

## 7. Dienst einrichten und starten
```bash
sudo cp /opt/octapihub/deploy/octapihub.service /etc/systemd/system/octapihub.service
sudo systemctl daemon-reload
sudo systemctl enable --now octapihub
sudo systemctl status octapihub
```
Ein **Watchdog** überwacht den Dienst: Reagiert das Programm nicht mehr (z. B. blockiert), startet systemd es nach
spätestens 60 Sekunden neu (`Type=notify`, `WatchdogSec=60`). Ein Teil mit Problemen (Bluetooth aus, Lampe nicht
erreichbar) löst **keinen** Neustart aus, er wird in der Oberfläche angezeigt. 

**Kontrolle:** `status` zeigt `active (running)`; `journalctl -u octapihub -n 30` zeigt keine
Zeile mit `ERROR`. Vor dem ersten Start prüft `/opt/octapihub/venv/bin/octapihub --check --config
/etc/octapihub/octapihub.toml --secrets /etc/octapihub/secrets.env` Einstellungen, Zugangsdatei und Token
(0 = in Ordnung, 1 = Warnungen, 2 = unbrauchbar). Nach dem Start zeigt
`curl http://127.0.0.1:8080/healthz` den Zustand aller Teile als JSON. Der Dienst startet künftig nach jedem Neustart von selbst.

## 8. Weboberfläche öffnen
Standardmäßig lauscht der Dienst nur auf dem Gerät selbst (`127.0.0.1`). Für den Zugriff im
Heimnetz tragen Sie in `/etc/octapihub/octapihub.env` die LAN-Adresse des Geräts ein
(`OCTAPIHUB_HOST=192.168.1.50`; die Adresse zeigt `hostname -I`), starten den Dienst neu
(`sudo systemctl restart octapihub`) und öffnen `http://192.168.1.50:8080`.
**Geben Sie keine Ports im Router frei.** Änderungen in der Oberfläche brauchen das Token; ohne
Token ist alles nur lesbar. Die Oberfläche läuft ohne Anmeldung und ohne Verschlüsselung im
Heimnetz: Wer im Netz ist, kann ansehen, aber ohne Token nichts ändern.

**Verschlüsselung (HTTPS, optional):** Ohne HTTPS laufen Token und Seiten unverschlüsselt durchs
Heimnetz. Wer das nicht möchte, hat zwei Wege:

1. *Direkt im Dienst:* Zertifikat und Schlüssel (PEM) auf dem Gerät ablegen, lesbar für den Benutzer
   `octapihub`, und in `/etc/octapihub/octapihub.env` eintragen:
   `OCTAPIHUB_SSL_CERT=/etc/octapihub/tls/cert.pem` und `OCTAPIHUB_SSL_KEY=/etc/octapihub/tls/key.pem`.
   Beide müssen gesetzt sein und zusammenpassen, sonst startet der Dienst nicht (`CH-WEB-022`).
   Prüfen mit `octapihub --check`. Danach `https://<Adresse>:8080` öffnen. Ein selbst erzeugtes
   Zertifikat (z. B. mit `openssl req -x509 -newkey rsa:3072 -nodes -days 825 -keyout key.pem -out cert.pem -subj "/CN=octapihub.local"`)
   löst im Browser eine Warnung aus, die Sie einmal bestätigen müssen.
2. *Vorgeschalteter Server* (z. B. Caddy oder nginx) auf demselben Gerät übernimmt HTTPS und
   leitet an `http://127.0.0.1:8080` weiter. Der Dienst bleibt dann auf `127.0.0.1`. Beachten Sie:
   Alle Geräte erscheinen dann dem Hub mit derselben Adresse (die Sperre nach Fehlversuchen
   gilt dann für alle gemeinsam), und der Server muss `/events/…` ohne Puffer durchlassen.

Eine Bedienungsanleitung für die Oberfläche finden Sie im
[Benutzerhandbuch](../src/octapihub/web/help/handbuch.md).

**Kontrolle:** Die Seite **Übersicht** erscheint; **Würfel** zeigt Ihre Würfel (Status
„Verbindet …“ oder „Bereit“).

## 9. Musik einrichten
Alles, was hier beschrieben ist, stellen Sie auf der Seite **Musik** ein. Schalten Sie dort
„Musik verwenden“ ein und tragen Sie den Musikordner `/srv/music` ein.

### 9.1 Google Nest / Home / Nest Hub
- Der Raspberry Pi und der Lautsprecher müssen im selben WLAN/Netz sein; Geräteisolation
  („Gast-WLAN“, „Client Isolation“) im Router muss aus sein.
- Seite **Musik** → „Google-Geräte suchen“ zeigt die Namen. Legen Sie ein Wiedergabegerät der Art
  „Google Nest / Home / Nest Hub“ an und tragen Sie den Namen **genau wie in der Google-Home-App**
  als Gerätenamen ein.
- **Dateien aus dem Musikordner** holt der Lautsprecher per HTTP vom Raspberry Pi. Dafür muss der
  Dienst im Heimnetz erreichbar sein (`OCTAPIHUB_HOST` auf die LAN-Adresse, Abschnitt 8) und die
  „Adresse dieses Geräts“ stimmen (wird meist automatisch erkannt). Die Dateien werden mit
  zufälligen, ablaufenden Adressen ausgeliefert und nur aus dem Musikordner. Radio-Adressen und
  YouTube-Links spielt der Lautsprecher dagegen selbst ab.
- Läuft der Dienst nur auf `127.0.0.1`, funktionieren Radio und YouTube auf Google-Geräten, aber
  keine lokalen Dateien.

### 9.2 Audioausgang des Raspberry Pi
- Legen Sie ein Wiedergabegerät der Art „Audioausgang dieses Raspberry Pi“ an.
- „Audioausgänge anzeigen“ listet die Namen auf. Leer lassen bedeutet: Standardausgang. Die
  Ausgabe wählen Sie sonst mit `sudo raspi-config` → System Options → Audio oder mit dem Namen
  (z. B. `alsa/plughw:CARD=Headphones,DEV=0`).
- Der Dienst-Benutzer braucht die Gruppe `audio` (Abschnitt 3).

### 9.3 YouTube und Radio
- Radio-Adresse: der direkte Stream-Link (`https://…/stream.mp3`).
- YouTube: Video- oder Playlist-Link. Auf dem Raspberry Pi übernimmt `yt-dlp` das Lesen
  (Abschnitt 2). Auf Google-Geräten wird die YouTube-Wiedergabe des Geräts verwendet; ein
  YouTube-Link nur mit Playlist (ohne Video) lässt sich dort nicht starten.
- Spotify-Links werden erkannt, aber **nicht abgespielt** (Fehler `CH-MUS-003`).

### 9.4 Bluetooth-Box
Nutzen Sie das nur, wenn nötig. Der Raspberry Pi benötigt dafür einen Audio-Dienst (PipeWire oder
PulseAudio), der **für den Dienst-Benutzer** läuft. Auf einem Raspberry Pi OS Lite ohne
Benutzersitzung ist das nicht vorbereitet, deshalb ist diese Variante am aufwendigsten und
**ungeprüft**:
```bash
sudo apt install -y pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth
sudo loginctl enable-linger octapihub          # Benutzerdienste ohne Anmeldung starten
```
Koppeln Sie die Box einmalig mit `bluetoothctl` (`pair`, `trust`, `connect`) und tragen Sie ihre
Adresse beim Wiedergabegerät ein. OctaPiHub verbindet sie vor der Wiedergabe mit
`bluetoothctl connect`. Wenn kein Ton kommt, prüfen Sie zuerst, ob dieselbe Box mit
`mpv --audio-device=help` als Ausgabe erscheint. Als Dienst-Benutzer ist dafür unter Umständen
`XDG_RUNTIME_DIR=/run/user/<UID von octapihub>` in `octapihub.env` nötig. Zeigt diese Anleitung
bei Ihrem Gerät etwas anderes, melden Sie das bitte.

**Kontrolle für Abschnitt 9:** Auf der Seite **Musik** eine Playlist anlegen, speichern und auf
„Jetzt abspielen (Test)“ klicken. Es sollte Musik kommen und die Karte „Spielt“ zeigen.

## 10. Sichern, Aktualisieren, Entfernen
**Sichern:** Das Skript `deploy/octapihub-backup.sh` sichert und stellt wieder her (`sudo /opt/octapihub/deploy/octapihub-backup.sh backup /pfad`, `restore ARCHIV`). Auf der Seite **Einstellungen → Sichern & Wiederherstellen** laden Sie eine
Export-Datei mit Musik, Playlists, Zuordnung, Sense-HAT- und Hue-Einstellungen herunter. Die
Datei enthält keine Schlüssel. Zusätzlich sichern Sie auf dem Gerät:
```bash
sudo tar czf octapihub-backup-$(date +%F).tar.gz /etc/octapihub /var/lib/octapihub
```
(enthält Token, Hue-Schlüssel und Datenbanken; bewahren Sie das Archiv privat auf.)
Vor jedem Import legt OctaPiHub außerdem selbst eine Kopie `octapihub.toml.bak-<Zeit>` neben der
Konfigurationsdatei an (die neuesten fünf bleiben erhalten).

**Aktualisieren:**
```bash
sudo git -C /opt/octapihub pull
sudo /opt/octapihub/venv/bin/pip install "/opt/octapihub[music]"
sudo systemctl restart octapihub
```
Alte Konfigurationen werden beim Laden auf die neue Version gehoben; die Datei bleibt lesbar.

**Entfernen:**
```bash
sudo systemctl disable --now octapihub
sudo rm /etc/systemd/system/octapihub.service
sudo systemctl daemon-reload
```
Programm (`/opt/octapihub`), Einstellungen (`/etc/octapihub`) und Daten (`/var/lib/octapihub`)
löschen Sie nur, wenn Sie sie nicht mehr brauchen; sie bleiben sonst erhalten.

## 11. Hardware prüfen
**Zuerst der Selbsttest:** `sudo -u octapihub /opt/octapihub/venv/bin/octapihub --selftest --config /etc/octapihub/octapihub.toml --secrets /etc/octapihub/secrets.env --data-dir /var/lib/octapihub`
prüft in einem Durchgang Python, Gruppen des Benutzers, Einstellungen, Token, Datenordner, Port, Bluetooth-Adapter, I2C, Sense HAT und Joystick,
`mpv`, `yt-dlp`, Tonkarte, `pychromecast`, Musikordner, Erreichbarkeit von Hue Bridge, MQTT und Home Assistant sowie den Zeitabgleich. Zu jedem Hinweis
steht der nächste Schritt (`[WARN]` Hinweis, `[FEHL]` fehlt etwas Wichtiges, `[ -- ]` übersprungen). Rückgabewert 0 = alles bereit, 1 = Hinweise, 2 = Fehler.
Der Selbsttest prüft die **Voraussetzungen**; ob die Würfel und Geräte wirklich funktionieren, zeigt die folgende Tabelle.
Diese Prüfungen wurden von den Entwicklern **noch nicht** durchgeführt. Bitte arbeiten Sie sie
einmal ab und notieren Sie, was abweicht:
| Prüfung | So gehen Sie vor | Erwartung |
|---|---|---|
| Würfel verbinden | Seite Würfel öffnen, Würfel einmal kippen | Status „Bereit“, „zuletzt Seite N“ erscheint |
| Seitenzahl | Jede der 8 Seiten nach oben legen | Die angezeigte Nummer wechselt; die Nummer pro Seite notieren |
| Halterung | Würfel in die Halterung stellen | „Seite 0 (Halterung)“ |
| Akku | Seite Würfel | Prozentwert (wenn der Würfel ihn bereitstellt) |
| Joystick | Seite Anzeige, Ansicht „Steuerung“; Joystick bewegen | Richtungen stimmen; bei gedrehter Anzeige beachten |
| LED-Matrix | Seite Anzeige | Vorschau und echte LEDs stimmen überein |
| Hue | Seite Licht, Knopf drücken, koppeln, Szene testen | Licht schaltet |
| Google-Lautsprecher | Seite Musik, Test abspielen | Musik kommt |
| Raspberry-Pi-Ausgang | Dasselbe mit Pi-Ausgang | Musik kommt |
| Bluetooth-Box | Dasselbe mit Bluetooth-Ziel | Musik kommt |
| Neustart | `sudo reboot` | Dienst läuft danach von selbst |

## 12. Fehler suchen
**Wo sehe ich Meldungen?**
- `journalctl -u octapihub -f` (Zugangsdaten sind unkenntlich gemacht).
- Seite **System** (Zustand, letzte Meldungen, Diagnose-Paket) und **Sichern** (Logdatei laden).
- In der Oberfläche unter **Hilfe → Fehlercodes**: Bedeutung und Abhilfe jedes `CH-…`-Codes.
- Logdatei: `/var/lib/octapihub/logs/octapihub.log` (rotierend, höchstens etwa 6 MB).
- Ausführlichere Meldungen: in `octapihub.env` `OCTAPIHUB_LOG_LEVEL=DEBUG`, Dienst neu starten.

| Beobachtung | Mögliche Ursache und Lösung |
|---|---|
| Dienst startet nicht, `CH-WEB-008` | Token fehlt, ist zu kurz oder noch der Platzhalter (Abschnitt 6) |
| Dienst startet nicht, `CH-CFG-…` | Konfiguration ungültig: Meldung nennt das Feld; Datei prüfen |
| Würfel bleibt „Verbindet …“ | Würfel mit Smartphone gekoppelt (trennen), außer Reichweite, Akku leer, falsche Adresse |
| Bluetooth ohne Zugriff | Gruppen des Benutzers `octapihub` prüfen (`id octapihub`) |
| Sense HAT fehlt | I2C eingeschaltet? `ls /dev/i2c-1`; Seite System/Anzeige nennt den Grund |
| Hue: „Taste nicht gedrückt“ | Runde Taste an der Bridge drücken, dann innerhalb von 30 s koppeln |
| Musik: `CH-MUS-004` | Musik ist aus, kein Gerät gewählt oder Playlist/Gerät gelöscht |
| Musik: `CH-MUS-003` | Quelle nicht gefunden, außerhalb des Musikordners oder Spotify |
| Musik: `CH-MUS-002` | Gerät erreicht? WLAN, Lautsprecher an, mpv installiert, Box in Reichweite |
| Musik: `CH-MUS-005` | Es fehlt ein Paket (z. B. `pychromecast`, Abschnitt 4) |
| Lautsprecher findet Datei nicht | Dienst nur auf `127.0.0.1`? `OCTAPIHUB_HOST` setzen; Adresse in „Musik“ prüfen |
| Datei im Musikordner „nicht gefunden“ | Ordner unter `/home`? Nach `/srv/music` verlegen; Rechte prüfen |
| Seiten laden, Speichern geht nicht | Token fehlt oder falsch (`CH-WEB-003`, `CH-WEB-004`) |
| Dienstdatei fehlerhaft | `systemd-analyze verify /etc/systemd/system/octapihub.service` |

Wenn nichts hilft: Seite **System → Diagnose-Paket** herunterladen. Es enthält keine
Zugangsdaten und keine vollständigen Adressen und lässt sich gefahrlos weitergeben.
