# HANDBUCH – OctaPiHub auf dem Raspberry Pi 3 (Vorgehen von der SD-Karte bis zum Dauerbetrieb)

> ## ⚠️ EIGENES PROJEKT – NICHT TEIL VON OCTALEDGER
> Projektname **„OctaPiHub“**. Dieses Handbuch gehört zu `PFLICHTENHEFT_OctaPiHub-Raspberry-Pi.md` und `RECHERCHE_OctaPiHub-Raspberry-Pi.md` (Nachtrag 3, §18). Das eigene lokale Repository liegt außerhalb von OctaLedger; eine Veröffentlichung oder ein Push erfolgt nur mit ausdrücklicher Freigabe.
> Stand: 24.11.2026 · **Ehrlichkeitsvermerk:** Die Befehle sind aus Dokumentation und Fachwissen zusammengestellt und **nicht auf deiner Hardware ausgeführt**. Stellen mit **„Prüfen“** sind nicht verifiziert (BlueZ-Version auf Trixie, Sense HAT auf Trixie, Würfel-Details, Watchdog-Drop-in, Hue-Szenenaufruf). Bei Abweichungen gilt die offizielle Doku. Die eigentliche OctaPiHub-Software existiert noch nicht; Kapitel 8–12 beschreiben die Umgebung, in die sie später eingesetzt wird. Platzhalter in `<spitzen Klammern>` ersetzen.

---

## Kapitel 1 – Überblick und Einkaufsliste

**Ablauf in einem Satz:** System auf SD-Karte schreiben → per SSH einloggen → WLAN/Bluetooth/Sense HAT einrichten → Würfel, Hue und Musik einzeln per Testskript prüfen → OctaPiHub als systemd-Dienst starten → absichern und beobachten.

**Du brauchst**
| Teil | Hinweis |
|---|---|
| Raspberry Pi 3 | mit aufgestecktem Sense HAT (ggf. GPIO-Riser/Verlängerung) |
| Netzteil 5 V, ≥ 2,5 A | gutes Markennetzteil; Unterspannung = Fehlerquelle Nr. 1 |
| SD-Karte ≥ 16 GB (A1/A2, besser „High Endurance“) | plus Kartenleser am PC |
| **USB-Bluetooth-Dongle (BLE)** | empfohlen; Chipsatz mit Linux-Unterstützung (nicht geprüft; vor Kauf nach „Linux BlueZ BLE“ suchen) |
| (optional) USB-Verlängerungskabel | Dongle vom Pi/Netzteil absetzen |
| (optional) USB-DAC/aktive Lautsprecher | nur wenn der Pi selbst Musik ausgeben soll |
| Zugang zum Router | feste IPs reservieren |
| PC mit Raspberry Pi Imager, SSH-Client (Windows: PowerShell `ssh`) | |

**Vorab notieren:** WLAN-Name/-Passwort, IP der Hue-Bridge, Name/IP der Nest-Geräte, Bluetooth-Adressen der Würfel (zeigt später der Scan).

---

## Kapitel 2 – System auf die SD-Karte schreiben (Raspberry Pi Imager)

1. **Raspberry Pi Imager** vom PC aus installieren (raspberrypi.com/software).
2. Gerät: **Raspberry Pi 3**. Betriebssystem: *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**. Speicher: deine SD-Karte.
3. In den **Einstellungen/Anpassungen** (Zahnrad bzw. „Einstellungen bearbeiten“) setzen:
   - Hostname: `octapihub`
   - Benutzername/Passwort: eigener Benutzer (nicht `pi`), starkes Passwort
   - **WLAN:** SSID, Passwort, **WLAN-Land: CH**
   - Zeitzone `Europe/Zurich`, Tastatur `ch`
   - **SSH aktivieren**, bevorzugt **nur Public-Key** (eigenen Schlüssel einfügen; Erzeugen am PC: `ssh-keygen -t ed25519`)
4. Schreiben, SD-Karte in den Pi, Netzteil anstecken. Erster Start dauert einige Minuten.

> Falls sich der Imager auf deiner Version anders bedient: Wichtig sind Hostname, Benutzer, WLAN+Land und SSH. Das lässt sich notfalls später per `raspi-config` bzw. `nmcli` nachholen.

---

## Kapitel 3 – Erster Login per SSH

```powershell
ssh <benutzer>@octapihub.local
```
- Klappt `octapihub.local` nicht: im Router die IP des Pi nachsehen und `ssh <benutzer>@<ip>` nutzen. Danach im Router **DHCP-Reservierung** (feste IP) anlegen.
- Erste Prüfungen:
```bash
cat /etc/os-release          # Debian-Version
uname -m                     # aarch64 = 64-bit
free -h                      # RAM
vcgencmd get_throttled       # 0x0 = keine Unterspannung
vcgencmd measure_temp
python3 --version
```

---

## Kapitel 4 – Grundkonfiguration

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```
Nach dem Neustart wieder einloggen.

```bash
sudo raspi-config
```
- *Localisation Options*: Zeitzone/Locale prüfen.
- *Interface Options*: **I2C** aktivieren (das `sense-hat`-Paket tut das laut Doku auch, doppelt schadet nicht). SSH ist an.
- *Performance Options*: nichts nötig. Desktop/VNC gibt es bei Lite nicht – das ist gewollt.

Zeit prüfen: `timedatectl` (NTP „active“ und „System clock synchronized: yes“).

Nützliche Pakete:
```bash
sudo apt install -y git python3-venv python3-dev build-essential \
    bluez avahi-daemon mosquitto mosquitto-clients ffmpeg jq curl
```
(`mosquitto` nur, falls MQTT gewünscht; `ffmpeg` nur für Audio-Verarbeitung.)

---

## Kapitel 5 – WLAN stabil machen

```bash
nmcli device status
nmcli connection show
```
Name der WLAN-Verbindung merken (oft `preconfigured`).

1. **Energiesparen aus** (weniger Verzögerungen):
```bash
sudo nmcli connection modify "<WLAN-Verbindungsname>" 802-11-wireless.powersave 2
sudo nmcli connection up "<WLAN-Verbindungsname>"
iw dev wlan0 get power_save      # Prüfen: sollte "off" sein
```
2. **Signal:** `iwconfig wlan0` bzw. `nmcli -f IN-USE,SSID,SIGNAL dev wifi`. Schlecht (< ca. 50 %)? Pi näher an den Router oder USB-WLAN-Dongle mit Antenne.
3. **Wichtig beim Pi 3:** 2,4 GHz (nur das Pi 3B; 3B+ auch 5 GHz). WLAN und Onboard-Bluetooth teilen sich Chip/Antenne → siehe Kapitel 6.
4. **Router:** DHCP-Reservierung für Pi, Hue-Bridge, Nest-Geräte. „AP-/Client-Isolation“ und Multicast-Filter ausschalten, sonst findet Cast keine Geräte.
5. Test: `ping -c 5 <hue-bridge-ip>`; `ping -c 5 8.8.8.8`.

---

## Kapitel 6 – Bluetooth einrichten (Onboard oder USB-Dongle)

```bash
bluetoothctl --version           # BlueZ-Version notieren; Bleak braucht >= 5.55 (Prüfen)
systemctl status bluetooth
bluetoothctl list                # zeigt Adapter (Controller)
```

**Variante A – Onboard-Bluetooth genügt (zuerst probieren):** Würfel direkt testen (Kapitel 9). Treten Aussetzer auf, Variante B.

**Variante B – USB-Dongle als Würfel-Adapter (empfohlen):**
1. Dongle einstecken, `bluetoothctl list` → zwei Controller (`hci0`, `hci1`). Welcher ist der Dongle: `hciconfig -a` bzw. `bluetoothctl show <MAC>` (Hersteller/Name).
2. Onboard-BT abschalten, damit es nicht stört: in `/boot/firmware/config.txt` am Ende ergänzen:
```
dtoverlay=disable-bt
```
und `sudo systemctl disable --now hciuart` (falls vorhanden). Danach Neustart. Der Dongle ist dann `hci0`. (Wenn du den Onboard-Chip nur *ignorieren* willst, lass das Overlay weg und wähle in OctaPiHub den Adapter `hci1`.)
3. Adapter einschalten und prüfen: `sudo bluetoothctl power on`, `bluetoothctl show | grep -i -E "powered|name"`.
4. **Nie** die Würfel in `bluetoothctl` koppeln/„pair“n – OctaPiHub verbindet direkt per Bleak. Ein gekoppelter, aber „verbunden“ angezeigter Zustand in BlueZ kann Verbindungen blockieren (siehe Kap. 15).

---

## Kapitel 7 – Sense HAT einrichten und testen

```bash
sudo apt install -y sense-hat
sudo reboot
```
Nach dem Neustart prüfen:
```bash
ls /dev/fb*                      # Sense-HAT-Framebuffer (oft /dev/fb1)
ls /dev/input/                   # Joystick = event*
i2cdetect -y 1                   # Geräte (Paket i2c-tools)
```
Testskript `~/test_sensehat.py`:
```python
from sense_hat import SenseHat

sense = SenseHat()
sense.low_light = True                       # gedimmt, schont Strom und Augen
sense.clear(0, 0, 40)                        # dunkelblau
print("temp  :", sense.get_temperature(), "°C (zu hoch wegen CPU-Wärme!)")
print("humid :", sense.get_humidity(), "%")
print("press :", sense.get_pressure(), "mbar")
print("orient:", sense.get_orientation())
sense.show_message("OctaPiHub", scroll_speed=0.05)
```
```bash
python3 ~/test_sensehat.py
```
**Joystick testen:** `python3 -c "from sense_hat import SenseHat; s=SenseHat(); print(s.stick.wait_for_event())"` und Joystick drücken.

**Sense HAT v2 (Farbsensor)?** `python3 -c "from sense_hat import SenseHat; s=SenseHat(); print(s.colour.colour)"` – Fehler heißt: v1 oder nicht erkannt.

**Temperaturkorrektur (Prinzip, Werte selbst kalibrieren):**
```
temp_real ≈ temp_sense - ((cpu_temp - temp_sense) / FAKTOR)
```
`FAKTOR` (häufig um 5) mit einem Referenzthermometer einstellen. Besser: Sense HAT per Riser-Kabel vom Pi abheben.

**Hinweise:** Kalibrierungswerkzeug (`RTIMULibCal`) kann auf dem Pi 3 laut Doku scheitern. Nutze die IMU nur, wenn du sie wirklich brauchst. Bei I2C-Fehlern: Kabel/Aufsteckung prüfen, `dmesg | grep -i i2c`.

---

## Kapitel 8 – Benutzer, Ordner und Python-Umgebung

**Dienstbenutzer ohne Login, mit nötigen Gruppen:**
```bash
sudo useradd --system --create-home --home-dir /var/lib/octapihub \
     --shell /usr/sbin/nologin octapihub
sudo usermod -aG bluetooth,i2c,input,video,audio octapihub
```
**Ordner:**
```
/opt/octapihub/            # Programmcode (Git-Checkout), gehört root oder octapihub
/opt/octapihub/venv/       # virtuelle Umgebung
/etc/octapihub/config.toml # Konfiguration
/etc/octapihub/secrets.env # Geheimnisse, Rechte 0600
/var/lib/octapihub/        # Zustandsdatenbank, Verlauf, Backups
```
```bash
sudo mkdir -p /opt/octapihub /etc/octapihub
sudo chown -R octapihub:octapihub /opt/octapihub /var/lib/octapihub
sudo install -m 600 -o octapihub -g octapihub /dev/null /etc/octapihub/secrets.env
```
**Virtuelle Umgebung** (Trixie verbietet systemweites `pip`):
```bash
sudo -u octapihub python3 -m venv --system-site-packages /opt/octapihub/venv
sudo -u octapihub /opt/octapihub/venv/bin/pip install --upgrade pip
sudo -u octapihub /opt/octapihub/venv/bin/pip install bleak fastapi "uvicorn[standard]" \
     jinja2 httpx pychromecast paho-mqtt
```
`--system-site-packages` macht das per `apt` installierte `sense-hat` in der venv sichtbar. Prüfen:
`sudo -u octapihub /opt/octapihub/venv/bin/python -c "import sense_hat, bleak; print('ok')"`.

---

## Kapitel 9 – Würfel einzeln testen (vor jeder Software!)

**Schritt 1 – Würfel finden** (Würfel kurz bewegen, damit er wach ist). `~/scan.py`:
```python
import asyncio
from bleak import BleakScanner

async def main() -> None:
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
    for address, (device, adv) in devices.items():
        print(address, device.name, adv.rssi, adv.service_uuids)

asyncio.run(main())
```
```bash
/opt/octapihub/venv/bin/python ~/scan.py
```
Würfel erkennst du am Namen (z. B. mit „Timeular“/„ZEI“ – nicht verifiziert) oder am Dienst `c7e70010-c847-11e6-8175-8c89a55d403c`. Adresse notieren (Pro Würfel eine). Mit `adapter="hci1"` im `BleakScanner(adapter=...)` den Dongle wählen.

**Schritt 2 – Dienste ausgeben und Seitenwechsel mitlesen.** `~/cube_test.py`:
```python
import asyncio
import sys
from bleak import BleakClient

ORIENTATION = "c7e70012-c847-11e6-8175-8c89a55d403c"

def on_notify(_char, data: bytearray) -> None:
    # The first byte is the active side (1-8); 0 (or 10) means "no side / neutral".
    print("orientation:", data[0] if data else None, data.hex())

async def main(address: str) -> None:
    async with BleakClient(address, adapter="hci0") as client:
        for service in client.services:
            print("SERVICE", service.uuid)
            for char in service.characteristics:
                print("   ", char.uuid, char.properties)
        await client.start_notify(ORIENTATION, on_notify)
        print("Drehe den Würfel (60 s) ...")
        await asyncio.sleep(60)

asyncio.run(main(sys.argv[1]))
```
```bash
/opt/octapihub/venv/bin/python ~/cube_test.py <WÜRFEL-ADRESSE>
```
**Auswerten und notieren (für die offenen Fragen):**
- Erscheint ein Batteriedienst (`0000180f-...`)? Dann ist der Stand lesbar (`client.read_gatt_char("00002a19-0000-1000-8000-00805f9b34fb")`).
- Kommen Werte 1–8 beim Drehen? Was kommt beim Hinlegen auf die Kante (0/10)?
- **Zwei Clients:** Während der Test läuft, versuche, den Würfel am PC/Timeular-App zu verbinden – klappt es, oder meldet einer „nicht verbindbar“? Ergebnis notieren.
- **Zwei Würfel:** zweites Skript mit der zweiten Adresse gleichzeitig starten → laufen beide?
- Wenn „Device not found/Connection failed“: Würfel aufwecken, näher ran, am PC/Handy trennen, `bluetoothctl remove <adresse>` (Cache löschen), Dongle-Adapter prüfen.

---

## Kapitel 10 – Hue-Bridge (v2, BSB002) koppeln und Szene testen

Die Bridge wird **nur lokal** über HTTPS angesprochen (API v2). Sie hat ein von Signify signiertes Zertifikat; der Zertifikatsablauf/-wechsel ist ein bekanntes Risiko (Recherche Nachtrag 2).

1. **Link-Taste** auf der Bridge drücken und **innerhalb von etwa 30 Sekunden**:
```bash
curl -k -s -X POST https://<BRIDGE-IP>/api \
  -H "Content-Type: application/json" \
  -d '{"devicetype":"octapihub#pi","generateclientkey":true}'
```
(`-k` heißt „Zertifikat nicht prüfen“ – **nur für diesen Einmaltest**, nicht für den Betrieb.) Antwort mit `"username"` = **App-Key**. Sicher in `/etc/octapihub/secrets.env` ablegen:
```
HUE_APP_KEY=<username>
```
Antwort `link button not pressed`: Taste drücken, Befehl wiederholen.

2. **Szenen/Räume ansehen:**
```bash
source /etc/octapihub/secrets.env      # nur zum Test; Datei hat Rechte 0600
curl -k -s -H "hue-application-key: $HUE_APP_KEY" https://<BRIDGE-IP>/clip/v2/resource/scene | jq '.data[] | {id, name: .metadata.name, group: .group.rid}'
curl -k -s -H "hue-application-key: $HUE_APP_KEY" https://<BRIDGE-IP>/clip/v2/resource/room | jq '.data[] | {id, name: .metadata.name}'
```
3. **Szene aktivieren (Prüfen: genauer Aufruf):**
```bash
curl -k -s -X PUT -H "hue-application-key: $HUE_APP_KEY" -H "Content-Type: application/json" \
  -d '{"recall":{"action":"active"}}' https://<BRIDGE-IP>/clip/v2/resource/scene/<SZENEN-ID>
```
4. **Reaktionszeit messen:** `time curl …` – für NF-3 notieren.
5. **Zertifikat für den Betrieb:** Verlauf notieren: `openssl s_client -connect <BRIDGE-IP>:443 -showcerts </dev/null 2>/dev/null | openssl x509 -noout -issuer -subject -dates`. Ziel für OctaPiHub: Prüfung gegen die Signify-CA (CA-Datei aus offizieller Hue-Entwicklerdoku, **nicht verifiziert**) statt `-k`.
6. Hue-Bridge im Router auf feste IP setzen. Rate: nicht mehr als wenige Anfragen/Sekunde.

---

## Kapitel 11 – Musik (Cast, lokal), MQTT

**Nest-Geräte finden und testen** (nur Test; in der Software später als Adapter):
```bash
/opt/octapihub/venv/bin/python - <<'EOF'
import pychromecast
chromecasts, browser = pychromecast.get_chromecasts()
for cc in chromecasts:
    print(cc.name, cc.model_name, cc.cast_info.host)
browser.stop_discovery()
EOF
```
Findet er nichts: Router-Einstellungen (Kapitel 5), `avahi-browse -art | grep -i cast` (Paket `avahi-utils`), Gerät per IP ansprechen.

**Medien-URL:** Cast-Geräte laden Musik per HTTP-URL. Lokale Dateien deshalb vom Pi aus bereitstellen (`python3 -m http.server 8000 --directory ~/musik`, später integrierter Server). Format MP3/AAC/FLAC werden von Cast-Geräten abgespielt; genaue Liste nicht verifiziert.

**Lokale Ausgabe am Pi (optional):** `sudo apt install -y mpv alsa-utils`, `aplay -l` (Karten), `mpv --no-video <datei>`; Ausgabegerät mit `--audio-device=alsa/<name>`.

**MQTT (optional, lokal):** `/etc/mosquitto/conf.d/octapihub.conf`:
```
listener 1883 127.0.0.1
allow_anonymous true
```
`sudo systemctl restart mosquitto`; Test: `mosquitto_sub -t 'octapihub/#' -v` und in zweitem Fenster `mosquitto_pub -t octapihub/test -m hallo`. Für Zugriff aus dem LAN (Home Assistant): Benutzername/Passwort einrichten (`mosquitto_passwd`) und `allow_anonymous false`.

---

## Kapitel 12 – OctaPiHub als Dienst: Autostart, Überwachung, Update

> Die Software gibt es noch nicht. Dieses Kapitel ist die Vorlage für die Unit-Datei. `octapihub` steht für das später gebaute Python-Paket (`python -m octapihub`).

**1. Konfigurationsdatei** `/etc/octapihub/config.toml` (Beispielgerüst, versioniert):
```toml
schema_version = 1

[bluetooth]
adapter = "hci0"            # hci1, falls Onboard-BT bleibt und der Dongle zweiter Adapter ist

[[cubes]]
name = "Licht"
address = "<ADRESSE-WÜRFEL-1>"
role = "light"

[[cubes]]
name = "Musik"
address = "<ADRESSE-WÜRFEL-2>"
role = "music"

[web]
bind = "0.0.0.0"            # nur LAN! Im Router kein Port nach außen
port = 8080
```

**2. systemd-Unit** `/etc/systemd/system/octapihub.service`:
```ini
[Unit]
Description=OctaPiHub - cube-driven smart home hub
Wants=network-online.target bluetooth.target
After=network-online.target bluetooth.target
StartLimitIntervalSec=0

[Service]
Type=notify
User=octapihub
Group=octapihub
WorkingDirectory=/opt/octapihub
EnvironmentFile=/etc/octapihub/secrets.env
ExecStart=/opt/octapihub/venv/bin/python -m octapihub --config /etc/octapihub/config.toml
Restart=always
RestartSec=5
# Service must call sd_notify("WATCHDOG=1") at least every 15 s while its main loop is healthy.
WatchdogSec=30
NotifyAccess=main
# Guard against memory runaway on a 1 GB board; systemd restarts the service afterwards.
MemoryMax=350M
# Mild hardening. PrivateDevices must stay off: I2C, input and framebuffer are needed.
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=yes
PrivateTmp=yes
StateDirectory=octapihub
SupplementaryGroups=bluetooth i2c input video audio

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now octapihub
systemctl status octapihub
journalctl -u octapihub -f              # Live-Log
```
`network-online.target` wartet nur, bis NetworkManager online meldet (kann bei WLAN-Ausfall zeitlich auslaufen). Die Software muss ohnehin mit fehlendem Netz klarkommen (Warteschlange, Wiederholen).

**3. Hardware-Watchdog (Systemhänger → Neustart):**
`/etc/systemd/system.conf.d/watchdog.conf`:
```ini
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
```
```bash
sudo mkdir -p /etc/systemd/system.conf.d
sudo reboot
systemctl show -p RuntimeWatchdogUSec          # Prüfen: nicht "0"
ls /dev/watchdog*
```
Zeigt `RuntimeWatchdogUSec` `0`, prüfe, ob unter `/usr/lib/systemd/system.conf.d/` bereits eine Raspberry-Pi-Datei (z. B. `40-rpi-enable-watchdog.conf`) existiert, die du übersteuern musst (Forenhinweis, **nicht verifiziert**). Der Pi-Chip verträgt etwa maximal 15–20 s; deshalb 15.

**4. Logs begrenzen** `/etc/systemd/journald.conf.d/octapihub.conf`:
```ini
[Journal]
Storage=persistent
SystemMaxUse=100M
```
`sudo mkdir -p /var/log/journal /etc/systemd/journald.conf.d && sudo systemctl restart systemd-journald`. Gespeichert werden nur *redigierte* Logs (keine Schlüssel).

**5. Automatische Sicherheitsupdates:** `sudo apt install -y unattended-upgrades` und `sudo dpkg-reconfigure -plow unattended-upgrades`. Kernel-Updates erfordern manuellen Neustart; Zeitpunkt wählen, in dem niemand Licht/Musik braucht.

**6. Eigenes Update:** (Vorlage)
```bash
sudo systemctl stop octapihub
cd /opt/octapihub && sudo -u octapihub git pull
sudo -u octapihub /opt/octapihub/venv/bin/pip install -e .
sudo systemctl start octapihub && journalctl -u octapihub -n 50
```
**Rollback:** vorherige Version mit `git checkout <alte-version>` und Neustart.

**7. Backup:** Konfigurationsdateien (ohne `secrets.env` im Klartext in Cloud-Ablagen!) und `/var/lib/octapihub/` regelmäßig auf den PC kopieren: `scp -r <benutzer>@octapihub.local:/etc/octapihub ./octapihub-backup/`. Zusätzlich SD-Karten-Abbild nach erfolgreicher Einrichtung ziehen (Win32DiskImager o. ä.), denn das ist der schnellste Neuaufbau.

---

## Kapitel 13 – Bedienung im Alltag

| Aufgabe | Weg |
|---|---|
| Status ansehen | Browser: `http://octapihub.local:8080` (später); SSH: `systemctl status octapihub`, `journalctl -u octapihub -n 100` |
| Szenen den Würfelseiten zuordnen | Weboberfläche → *Würfel* (Seite → Aktion), speichern |
| Licht/Musik prüfen | Würfel drehen; Sense HAT zeigt Szene/Titel |
| Neustart des Dienstes | `sudo systemctl restart octapihub` oder Knopf in der Oberfläche |
| Bluetooth zurücksetzen | `sudo systemctl restart bluetooth` oder Oberfläche |
| Nachtmodus der LED | Zeitplan in den Anzeige-Einstellungen; Joystick: Taste mittig = an/aus (Vorschlag) |
| Fernzugriff | nur über VPN/Tailscale (Kap. 14) |

**Sense-HAT-Anzeige (Kurzlegende, Vorschlag aus dem Pflichtenheft):** Rot blinkend „!“ = Fehler · volle Farbfläche + unterer Balken = Lichtszene/Helligkeit · Ziffernbild = Würfelseite · zwei Balken = Würfelbatterie · Equalizer/Pausesymbol = Musik · Randpixel grün/gelb/rot = WLAN, Hue, Cast, MQTT, Würfel 1, Würfel 2.

---

## Kapitel 14 – Sicherheits-Checkliste

- [ ] Standardbenutzer `pi` nicht vorhanden, eigener Benutzer mit starkem Passwort
- [ ] SSH nur mit Schlüssel (`/etc/ssh/sshd_config`: `PasswordAuthentication no`, danach `sudo systemctl restart ssh`; **vorher** testen, dass der Schlüssel-Login klappt)
- [ ] Dienst läuft als `octapihub` (nicht root)
- [ ] `/etc/octapihub/secrets.env` Rechte `0600`, nicht im Git, nicht in Logs
- [ ] Router: **kein** Port-Forwarding auf den Pi
- [ ] Weboberfläche nur im LAN; Token für schreibende Aktionen
- [ ] Firewall: `sudo apt install -y nftables` bzw. `ufw`; erlaubt: 22, 8080 (nur LAN), 1883 nur lokal/LAN nach Bedarf
- [ ] `unattended-upgrades` aktiv
- [ ] Hue-Zertifikat nicht dauerhaft ungeprüft (`-k` nur im Test)
- [ ] Fernzugriff nur per Tailscale/WireGuard (Anleitung der jeweiligen Anbieter folgen)

---

## Kapitel 15 – Fehlersuche (Symptom → Ursache → Maßnahme)

| Symptom | Mögliche Ursache | Maßnahme |
|---|---|---|
| Pi bootet nicht / startet ständig neu | Netzteil zu schwach, defekte SD | anderes Netzteil, `vcgencmd get_throttled`, SD neu beschreiben |
| `ssh: could not resolve octapihub.local` | mDNS auf PC/Router nicht verfügbar | IP aus Router nutzen |
| Würfel wird nicht gefunden | Würfel schläft, zu weit, an anderem Gerät verbunden, falscher Adapter | wecken, nähern, PC/Handy trennen, `adapter="hci1"` testen |
| `Operation already in progress` / `Device busy` | parallele Verbindungsversuche, hängender BlueZ-Zustand | Verbindungen nacheinander, `sudo systemctl restart bluetooth`, Adapter aus/ein |
| „verbunden“, aber keine Seitenwechsel | veraltetes Verbindungsobjekt in BlueZ | Wachhund trennt und verbindet neu; `bluetoothctl disconnect <adresse>`, `bluetoothctl remove <adresse>` |
| Würfel-Aussetzer bei Musik/Streaming | WLAN/BT-Koexistenz Onboard-Chip | USB-BLE-Dongle (Kap. 6), Pi/Dongle abrücken, 2,4-GHz-Kanal des Routers wechseln |
| Hue `link button not pressed` | Taste nicht gedrückt/zu spät | wiederholen |
| Hue Zertifikatsfehler | CA fehlt/Zertifikat gewechselt | Zertifikat/Aussteller prüfen (Kap. 10), CA aktualisieren, nicht blind `-k` |
| Hue HTTP 429 | zu viele Anfragen | Rate senken, Aktionen bündeln |
| Cast findet nichts | mDNS blockiert, Isolation | Router-Einstellung, feste IP, gleiches Netz |
| Musik startet nicht auf Nest | Gerät schläft, falsche URL | Wecken/Wiederholen, URL vom Handy im LAN testen |
| Sense HAT zeigt nichts / Fehler | I2C aus, Aufsteckproblem, Paket fehlt | `i2cdetect -y 1`, `sudo apt install sense-hat`, Neustart |
| Temperatur falsch (zu warm) | CPU-Wärme | Kapitel 7 Korrektur/Riser |
| Dienst startet nicht | Fehler in Konfiguration/Code | `journalctl -u octapihub -n 100 --no-pager`, Konfiguration prüfen |
| Dienst startet dauernd neu | `MemoryMax` oder Watchdog zu knapp | Log lesen; `free -h`; Limit/Intervalle prüfen |
| Weboberfläche nicht erreichbar | Port/Firewall/Bind-Adresse | `ss -tlnp | grep 8080`, Firewall prüfen |
| System langsam | Swap-Nutzung, SD langsam | `free -h`, `top`, bessere SD/USB-SSD |
| Nach Stromausfall fehlerhaft | SD-Dateisystem beschädigt | `dmesg`, SD ersetzen, Backup einspielen |

**Nützliche Befehle:** `journalctl -u octapihub -b` (seit Boot), `systemctl list-units --failed`, `bluetoothctl show`, `btmon` (BLE-Mitschnitt, Paket `bluez`), `htop`, `vcgencmd get_throttled`, `dmesg -T | tail -50`.

---

## Kapitel 16 – Abnahme-Checkliste (nach Pflichtenheft §9)

- [ ] Frische SD → Kapitel 2–12 ohne Monitor durchlaufen
- [ ] Stecker ziehen/wieder einstecken → alles wieder grün (NF-1)
- [ ] Dienst per `kill -9` beenden → startet in Sekunden neu (NF-2)
- [ ] Würfel trennen/zurückbringen; Bluetooth-Dienst stoppen → Selbstheilung (F-W3/F-W4)
- [ ] WLAN trennen (Router aus) → Aktionen in Warteschlange, später abgearbeitet/verworfen
- [ ] Hue-Bridge vom Strom → Fehler angezeigt, Hub läuft, danach Erholung
- [ ] Nest-Gerät aus → Meldung, kein Absturz
- [ ] Sense HAT abziehen/stören → Hub läuft weiter (F-D7/F-S8)
- [ ] 7 Tage Dauerlauf, RAM-Verlauf dokumentiert (NF-4/NF-5)
- [ ] Sicherheits-Checkliste (Kap. 14) abgehakt
- [ ] Offene Punkte der Recherche §18.11 beantwortet und Dokumente aktualisiert

---

## Kapitel 17 – Was als Nächstes (Reihenfolge)
1. **P0 Spike** (Pflichtenheft §10): Kapitel 1–11 ausführen, alle „Prüfen“-Antworten notieren.
2. Entscheiden: Weboberfläche (empfohlen FastAPI/HTMX) vs. Streamlit anhand der RAM-Messung.
3. Eigenes lokales Repository für OctaPiHub verwenden (getrennt von OctaLedger); Lizenz klären, **bevor** etwas veröffentlicht wird.
4. Danach schrittweise Pflichtenheft-Phasen P1 → P5, jeweils mit Tests und Doku.

*Quellen und Hintergründe: `RECHERCHE_OctaPiHub-Raspberry-Pi.md` §15, §16, §17, §18.11.*
