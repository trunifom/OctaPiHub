# ⚠️ EIGENES, ANDERES PROJEKT – „OctaPiHub“, NICHT Teil von OctaLedger

> **Dieses Dokument gehört NICHT zum Repository OctaLedger.**
> Es beschreibt ein **separates, künftiges Projekt** (Raspberry-Pi-Hub für Smart Home und Musik, gesteuert über
> Timeular-/ZEI-Würfel). Es **kann teilweise von OctaLedger abgeleitet werden** (Code, Konzepte), ist aber eine eigene
> Software mit eigenem Repository, eigenen Zielen und eigener Lizenzfrage.
>
> * Speicherort: eigenes lokales Repository bewusst **ausserhalb** des Git-Repos `OctaLedger`
>   (`...\GitHub\Timeular\OctaPiHub\`). Der Ordner `Timeular` ist selbst kein Git-Repository.
>   Nicht in OctaLedger einordnen; Veröffentlichung oder Push nur mit ausdrücklicher Freigabe.
> * Stand: 2026-11-23. Erstellt als reine Recherche/Beratung – **es wurde kein Code geschrieben oder geändert.**
> * Ehrlichkeitshinweis: Die Recherche stützt sich auf Websuche (Blogs, Foren, GitHub, Herstellerdokus). Vieles ist
>   **nicht an echter Hardware geprüft**. Jede Aussage trägt ein Vertrauenslabel: **[belegt]** (Primärquelle/Doku),
>   **[Community]** (Forum/Blog, Einzelbericht), **[Annahme]** (eigene Schlussfolgerung/Vorwissen, vor Nutzung prüfen).

> **Nachtrag 2 (2026-11-23, gilt vor allem anderen):** Deine Hue Bridge ist eine **v2 (BSB002, Firmware 6.5.1978688000 = aktuell)**, **nicht v1**.
> Alle „Hue-v1“-Aussagen in §16.6, §16.7, §16.10–§16.12 sind **überholt**; die Details stehen in **§17 ganz am Ende**.
>
> **Nachtrag 1 (2026-11-23):** Antworten auf deine Rückfragen (Pi 4 vorhanden, pi-topPULSE direkt am Pi, Sense HAT, WLAN/Dongles,
> Nest Hubs, **Hue Bridge v1**, Matter, „Was ist Home Assistant?“, „Warum Pi 3 und Musik?“) stehen **ab §16 am Ende**.
> **Wo §16 einem früheren Abschnitt widerspricht, gilt §16.** Wichtigste Korrekturen: Hue-Bridge-v1 ist ein Problem für den
> geplanten Hue-Weg (§16.6); der Pulse und der Pi-3-Bluetooth-Chip kollidieren möglicherweise (§16.2); „Pi 3 zu schlecht für Musik“
> war **zu pauschal** formuliert (§16.9).

---

## 0. Kurzfassung (Empfehlung in 12 Zeilen)

1. **Hub-Rolle: Raspberry Pi 3** (einziger deiner Pis mit eingebautem BLE). Pi 1 und Pi 2 haben **kein Bluetooth**
   [belegt für Pi 2, Pi 1 Annahme] und eignen sich als **Zusatzgeräte** (Audio-Endpunkt, Anzeige, evtl. BLE-Relais mit USB-Dongle).
2. **Architektur: „Schlanker eigener Python-Dienst + MQTT“ (Variante D unten)**, kein vollständiges Home Assistant auf dem Pi 3.
   Der Dienst ist **headless** (systemd, kein GUI), Steuerung per **CLI + kleine lokale REST-API + optionale Mini-Weboberfläche**,
   Fernzugriff per **Tailscale** (SSH/VNC/Web) statt Portfreigabe.
3. **Node-RED / Home Assistant nicht als Kern, sondern als optionale Nachbarn:** Der Dienst spricht MQTT (mit
   Home-Assistant-Discovery). Läuft irgendwo (später) ein HA, sieht er die Würfel automatisch; ohne HA funktioniert alles weiter.
4. **Licht:** Hue **direkt** über die lokale CLIP-API v2 (Szenen aktivieren, Event-Stream) [belegt] – OctaLedger hat dafür bereits einen Adapter.
5. **Google-Seite ist der unsichere Teil:** Für **Nest-Lautsprecher/-Hub als Audioausgabe** gibt es einen robusten Weg (Google Cast via
   `pychromecast`), für **Google-Home-Szenen/Routinen auslösen** gibt es **keine** einfache öffentliche lokale API → nur über Home Assistant
   oder Umwege (siehe §6.3). Das ist die wichtigste offene Entscheidung.
6. **Musik:** Auf einem Pi 3 ist **Music Assistant offiziell zu schwach** (Doku: Pi 4+, 64 Bit) [belegt]. Empfehlung: **MPD (oder mpv)
   lokal + Cast-Ausgabe + optional Spotify Connect (librespot)**, hinter einer eigenen Player-Abstraktion.
7. **Pi-Top-Hardware (PULSE/Hub)** ist nett als Statusanzeige, aber **Softwareunterstützung ist alt** (README: nur Pi 3, Root nötig, Legacy-Treiber)
   [belegt/Community]. Auf aktuellem Raspberry Pi OS **zuerst einen Machbarkeitstest** machen; Anzeige immer als **optionales Plug-in** bauen.
8. **Grösster fachlicher Stolperstein – BLE-Reichweite:** Die Würfel müssen **physisch in BLE-Reichweite (~10 m) des Pi** sein.
   „Würfel unbrauchbar, wenn ich nicht daheim bin“ löst ein Pi daheim **nur**, wenn die Würfel **auch daheim** bleiben (Pi läuft
   24/7, PC darf aus). Willst du die Würfel **unterwegs** benutzen, braucht es einen **mobilen Hub** (siehe §3.4). Bitte klären!
9. **Stabilität zuerst:** ein BLE-Besitzerprozess, serialisierte Verbindungsaufbauten, Watchdog, der BlueZ neu startet (§5).
10. **Sicherheit:** kein Portforwarding, Tailscale, MQTT mit Login, Geheimnisse nicht im Repo.
11. **Wiederverwendung aus OctaLedger:** hoch für BLE-Protokoll, Zustandsautomaten, Orientierungslogik, Batterie, Hue/MQTT/HA/Webhook-Adapter,
    Outbox; ersetzen: Windows-Teile (Tray, Toast, Autostart, Idle), Streamlit-Dashboard, evtl. Audio-Engine (§9).
12. **Vorgehen:** erst ein winziger **Spike** auf dem Pi 3 (BLE-Verbindung zum Würfel + Hue-Szene), dann Ausbau in Phasen (§11).

---

## 1. Ausgangslage und Ziele (so wie ich sie verstanden habe)

| Thema | Verständnis |
|---|---|
| Heute | OctaLedger läuft auf dem privaten Windows-PC (Agent/Bleak → SQLite → API → Streamlit). |
| Problem | PC aus/abwesend → Würfel nutzlos. |
| Wunsch | Eine kleine, dauerhaft laufende **Smart-Home- und Musikstation** auf Raspberry Pi. |
| Würfel 1 | **Lichtszenen** (Philips Hue). |
| Würfel 2 | **Audioplayer/-steuerung** (lokal auf Pi-Lautsprechern oder auf Google Nest Hub/Speakers; Quelle lokal oder Streaming). |
| Anbindungen | Philips Hue; „Google Home Assistant“ (**siehe offene Frage Q1**: Google Home/Assistant oder Home Assistant?). |
| Bedienung | Wahrscheinlich ohne GUI/Bildschirm; VNC nur im Notfall; Konsole oder Hintergrunddienst. |
| Hardware | Raspberry Pi **1, 2, 3**, **pi-topPULSE** (LED-Matrix/Audio), **pi-topHUB** (Hub mit Akku-/Anzeigefunktion). |
| Anzeige-Wunsch | RGB-LEDs: Lampenfarbe, Würfelstatus, Würfelbatterie, Titel/Track usw. |
| Offen | Kern selbst schreiben vs. Open-Source-Smarthome (Node-RED) mit eigenem Teilprogramm. |

---

## 2. Hardware-Realität deiner Geräte

### 2.1 Raspberry Pi 1 / 2 / 3

| Modell | Bluetooth/WLAN | RAM | CPU/Architektur | Rolle |
|---|---|---|---|---|
| **Pi 3 Model B** | BCM43438: WLAN + **Bluetooth 4.1 inkl. BLE** onboard [belegt] | 1 GB [belegt] | 64-Bit-fähig (ARMv8) | **Hub** (BLE + Logik) |
| **Pi 2 Model B** | **keins** (nur Ethernet) [belegt] | 1 GB [belegt] | ARMv7 (v1.2: BCM2837, 64-Bit-fähig [Annahme]) | Audio-Endpunkt, Anzeige, **BLE-Relais mit USB-Dongle** |
| **Pi 1 (B/B+)** | **keins** [Annahme, Suche lieferte es nicht] | 256/512 MB [Annahme; B+ = 512 MB belegt] | ARMv6, 32 Bit | nur einfache Aufgaben (Snapcast-Client, LED-Anzeige) |

Konsequenzen:

* **Nur der Pi 3 kann die Würfel ohne Zusatzhardware lesen.** Seine Bluetooth-Antenne teilt sich den Funkchip mit dem WLAN
  [belegt: gleicher Chip]; für maximale Stabilität **per LAN-Kabel** anbinden und ggf. einen **USB-Bluetooth-5-Dongle** mit
  gut unterstütztem Linux-Treiber nutzen [Annahme – konkrete Dongle-Modelle vor dem Kauf auf BlueZ-Kompatibilität prüfen].
* Pi 1/Pi 2 mit **USB-BT-Dongle** können als **zweiter BLE-Standort** dienen (Reichweite in einem anderen Zimmer), ein Würfel pro Gerät.
  Dann braucht es eine **Hub-Kommunikation** (z. B. MQTT zwischen den Pis) – das spricht ebenfalls für MQTT als Rückgrat.
* **Home Assistant** hat die Installationsarten *Supervised/Core* und **32-Bit-Systeme (armhf/armv7)** abgekündigt (Meldung 05/2025,
  gültig ab 2025.12) [Community/Sekundärquellen, Primärquelle nicht abgerufen]; der Pi 3 wurde laut Forum aus der empfohlenen
  Hardwareliste entfernt (Speicher) [Community]. → **HA auf Pi 1/2 ausgeschlossen, auf Pi 3 nur knapp**.
* **Raspberry Pi OS Trixie** (Debian 13) wurde laut Raspberry-Pi-Ankündigung bis zurück zum Pi 1 getestet [belegt, mit Nutzerkommentar „auf Pi 3 gut nutzbar“ als Community].

### 2.2 pi-topPULSE und pi-topHUB

* **pi-topPULSE:** HAT mit **7×7-RGB-LED-Matrix**, Mikrofon und I²S-Audio/Lautsprecher-Anbindung; Python-Bibliothek `ptpulse`
  (Module `configuration`, `microphone`, `ledmatrix`). Das Haupt-Repo sagt: **nur Raspberry Pi 3 unterstützt**; die Bibliothek benötigt
  **Root**; Installation über Raspbian-Pakete mit Abhängigkeit `pt-device-manager` [belegt, README ~6 Jahre alt].
* **pi-topHUB v1:** separate Python-Bibliothek `pthub` (Paket `pt-hub`) zur Kommunikation mit dem Hub (u. a. **Akkustand des pi-top-Geräts**,
  *nicht* der Würfel) [belegt, Community-Repo].
* Das neuere **pi-top Python SDK** nennt PULSE-Unterstützung für **pi-top[4]/Laptops/CEED**, nicht für den alten pi-top-v1-Hub [belegt].
* **Risiko:** Alte Treiber (`pt-device-manager`, Overlays, Root-Zugriff) auf **Bookworm/Trixie** (andere Boot-Pfade `/boot/firmware`, Kernel,
  I²S/GPIO-Handling) sind **ungeprüft** [Annahme]. **Pflicht-Spike:** auf frischem Pi OS Lite testen, ob die 7×7-Matrix ansteuerbar ist.
  Fällt das durch: Fallback = **Pi OS Bookworm-Legacy/Bullseye-Image** nur für dieses Gerät, oder die Matrix **direkt über I²C/SPI**
  ansteuern (Chip/Adresse muss erst aus Schaltplan/Quellcode ermittelt werden [Annahme: nicht recherchiert]) oder Ersatz durch
  **WS2812-LED-Streifen/Unicorn-HAT**.
* Der **Akku des pi-top-Hubs** macht den Pi 3 im pi-top-Gehäuse zum **potenziell mobilen Gerät** (relevant für §3.4) [Annahme].

---

## 3. Die Kernfrage: wie ist das System geschnitten?

### 3.1 Anforderungen, die die Architektur bestimmen

1. **BLE zuverlässig 24/7** (Würfel schlafen, werben selten, verbinden neu).
2. **Wenig RAM/CPU** (Pi 3, 1 GB).
3. **Headless** und fernwartbar.
4. **Mehrere Ausgabeziele**: Hue, Cast-Lautsprecher, lokaler Ton, evtl. Home Assistant, LED-Anzeige.
5. **Erweiterbar**: später Würfel, Ziele, Szenen ohne Neubau.
6. **Offline-fähig** (Hue lokal; Streaming braucht Internet).

### 3.2 Varianten im Vergleich

| | Beschreibung | Pro | Contra | Pi-3-Eignung |
|---|---|---|---|---|
| **A** Vollständiges **Home Assistant** (OS oder Container) auf dem Pi 3 + eigene Integration | HA übernimmt Hue, Cast, UI, Automationen | riesiges Ökosystem, Hue/Cast/Google-Assistant-SDK vorhanden | Pi 3 laut Quellen **an der Grenze** [Community]; als Container **keine Add-ons** [Community]; HA-OS ist 64 Bit; Timeular-Würfel **keine** fertige Integration [Recherche: nichts gefunden] → eigene Integration nötig | knapp |
| **B** **Node-RED** als Kern | Flows verbinden MQTT/Hue/HA | visuell, ~80–120 MB RAM idle [Community] | BLE-Würfel-Anbindung in Node.js unter aktuellem BlueZ ist ein Fremdkörper [Annahme]; komplexe Logik (Entprellung, Modi, Batterie) in Flows unhandlich; Tests schwer | ok |
| **C** **Eigener schlanker Python-Dienst** (aus OctaLedger abgeleitet) | BLE + Logik + Adapter in einem Prozess, headless | volle Kontrolle, geringster Footprint, **viel bestehender Code**, testbar, ein BLE-Besitzer | alles Eigenbau (Hue/Cast-Details, Pflege) | **gut** |
| **D** = C **+ MQTT (Mosquitto) + optionales HA/Node-RED als Nachbar** | C veröffentlicht Würfel/Status per MQTT inkl. HA-Discovery; kann Befehle empfangen | HA/Node-RED **optional und austauschbar**, mehrere Pis koppelbar, Anzeige-Pi kann nur MQTT lesen | ein Dienst mehr (Mosquitto, ~wenig RAM [Annahme]) | **sehr gut** |
| **E** **BLE-Proxy**: ESP32/ESPHome (oder `btproxy`/`bleproxy` auf Linux) vermitteln BLE ins HA | HA macht die BLE-Verbindung über Proxys | billige ESP32 überall im Haus; ESPHome-Proxy bis **3 gleichzeitige aktive Verbindungen** [belegt]; `btproxy` läuft laut Projekt auf Linux/Pi, ein Forumsnutzer meldet Pi 3 [Community] | **braucht HA** und eine **eigene HA-Integration für den Würfel** (kein Fertigbaustein gefunden); Latenz/Stabilität ungeprüft | als **Erweiterung** interessant |

**Empfehlung: D, begonnen als C.** Begründung in einem Satz: *Der Würfel-Teil (BLE → Bedeutung) ist der Teil, den es nirgends fertig
gibt und den du schon hast; alles andere (Hue, Cast, MQTT, HA) sind austauschbare Ausgabekanäle.* So bleibt die Entscheidung
„Open-Source-Smarthome ja/nein“ **reversibel**.

Warum nicht „nur Node-RED + Teilprogramm“? Es ist machbar, aber dein Teilprogramm **wäre** der Kern (BLE, Zustandslogik, Musiksteuerung);
Node-RED würde nur Kabel dazwischen legen. Dann ist es einfacher, die Kabel (MQTT/HTTP) gleich im Dienst zu haben und Node-RED
**optional** daneben zu betreiben, falls du visuell verknüpfen willst.

### 3.3 Zielbild (Variante D)

```text
 Würfel A (Licht)      Würfel B (Audio)             (BLE, nur lesen – wie in OctaLedger)
        \                 /
         v               v
 +-------------------------------------------------------------------------------+
 | Raspberry Pi 3  –  Dienst "octapihub" (systemd, headless, 1 BLE-Besitzer-Loop)   |
 |                                                                               |
 |  BLE-Schicht -> Entprellung/Zustand -> Regel-Engine (Seite -> Aktion, Modi)    |
 |                                          |                                    |
 |        +---------------+-----------------+---------------+-------------+      |
 |        v               v                 v               v             v      |
 |   Hue-Adapter     Player-Dienst      MQTT-Adapter    Anzeige-Plugin   REST/CLI|
 |   (CLIP v2)       (MPD/mpv +         (Mosquitto,     (pi-topPULSE,    (lokal,|
 |                    Cast + Connect)   HA-Discovery)    LED, Log)        Token) |
 +-------------------------------------------------------------------------------+
        |                  |                 |                              |
        v                  v                 v                              v
   Hue Bridge     Pi-Lautsprecher /     Home Assistant /            SSH/Tailscale,
                  Nest Hub/Speaker     Node-RED (optional)          Mini-Web-UI
```

### 3.4 Das „Wenn ich nicht zuhause bin“-Problem ehrlich betrachtet

* BLE hat Reichweite von etwa **10 m** [Annahme/Allgemeinwissen]; die Würfel müssen beim Pi sein.
* **Fall 1 – Würfel bleiben daheim:** Pi 24/7 → PC darf aus. **Gelöst.** Fernsteuerung von unterwegs läuft dann über **App/Web/Telegram/…**,
  nicht über den Würfel.
* **Fall 2 – Würfel reisen mit dir:** Daheim-Pi ist unerreichbar per BLE. Optionen: (a) **mobiler Mini-Hub** (Pi 3 im pi-top-Gehäuse mit Akku,
  Handy-Hotspot, ruft per Tailscale/MQTT den Daheim-Pi oder Cloud-APIs); (b) Würfel → **Smartphone-App** (eigene Android-App, BLE) – aufwändig;
  (c) ESP32-Relais am Zielort.
* **Fall 3 – Reichweite im Haus:** weitere BLE-Standorte (Pi 2 + Dongle oder ESP32-Proxy) per MQTT zusammenschalten.
* Hue-Fernzugriff offiziell nur über das **Hue-Konto/Cloud** (Internet nötig) [Community/Anleitungen]; sauberer ist **Tailscale zum Daheim-Pi**
  und dort lokal schalten.

---

## 4. Betriebssystem und Betrieb

* **Raspberry Pi OS Lite 64 Bit** (Bookworm oder Trixie) auf dem Pi 3; Pi 1 → 32-Bit-Lite. Ohne Desktop (spart RAM). Wenn VNC nötig: bei Bedarf
  Desktop-Paket oder `wayvnc`/RealVNC-Cloudzugang später ergänzen [Annahme].
* **BlueZ ≥ 5.55** ist für Bleak Pflicht [belegt, Bleak-Quelltext].
* **Prozessmodell:** ein systemd-Dienst, `Restart=always`, `WatchdogSec` (sd_notify) und Hardware-Watchdog des Pi [Annahme: üblich, vor Einsatz testen].
* **Docker?** Für den BLE-Kern **nicht empfohlen** (D-Bus/BlueZ-Zugriff aus dem Container umständlich, mehr RAM) [Annahme]; für Mosquitto/HA/Node-RED ok.
* **SD-Karte schonen:** Logs begrenzen (journald), SQLite im WAL-Modus, ggf. `log2ram`, besser **USB-SSD/gute Karte** und sauberes Netzteil [Annahme/Praxiswissen].
* **Zugriff:** SSH + **Tailscale** (Pi und Client im Tailnet, kein Portforwarding; bei Headless „Key-Expiry“ deaktivieren, sonst Ablauf nach 180 Tagen)
  [Community/Anleitungen]. VNC-Viewer funktioniert über die Tailscale-IP [Community].
* **Zeit:** NTP; intern weiter UTC (wie OctaLedger-Regel).
* **Updates:** unbeaufsichtigte Sicherheitsupdates, aber BlueZ/Kernel-Updates **testen** (BLE-Verhalten kann sich ändern).

---

## 5. BLE-Zuverlässigkeit auf Linux (die Fallen)

Aus Bleak-Issues und Praxisberichten [Community]:

1. **Nur ein Prozess** besitzt den Adapter; mehrere Prozesse → `le-connection-abort-by-local` (Bleak #1858). OctaLedger hat schon „ein Loop, alle BLE-Clients“ – beibehalten.
2. **Verbindungsaufbau serialisieren** (nicht zwei Würfel gleichzeitig verbinden/scannen); parallele Befehle können BlueZ bis zum Neustart verwirren.
3. **Hängender „Connected“-Zustand** (Stale State) nach abgebrochenem Connect → **Watchdog**: nach N Fehlversuchen Gerät `remove`n,
   Adapter `power off/on` oder `systemctl restart bluetooth`.
4. **Benachrichtigungen nach jedem Reconnect neu abonnieren**; Bleak legt pro Sitzung eine neue D-Bus-Verbindung an wegen eines BlueZ-Verhaltens.
5. **Wiederholungen mit Backoff und Timeouts**; manche Geräte brauchen mehrere Versuche (Bleak #1240).
6. Pi-3-Eigenheit: ältere Berichte über BLE-Probleme bei Pi 3B+/4 mit `bluez-firmware` (2020) [Community, alt] → aktuelles Raspberry Pi OS nutzen, Funk-Firmware aktuell halten.
7. **Würfel werben nur nach Bewegung/Knopfdruck**: Das Verhalten wurde in OctaLedger bereits unter Windows beobachtet („einmal drehen oder Knopf drücken“, `BLE_PROTOCOL.md`).
   Unter Linux entfällt die Windows-spezifische „scan before connect“-Notlösung wahrscheinlich, **muss aber getestet werden** [Annahme].
8. Protokoll weiter **nur lesen/abonnieren**, keine Schreibzugriffe auf unbekannte Characteristics (Regel aus OctaLedger übernehmen).
9. **Nicht hardwarevalidiert**: Das Würfelprotokoll (Service `c7e70010-…`, Characteristic `c7e70012-…`, Werte 1–8, 0/10 neutral) stammt aus Community-Referenzen; OctaLedger
   hat es nur an **einem** Würfel unter Windows beobachtet. Für OctaPiHub gleiche Vorsicht.

---

## 6. Anbindungen im Detail

### 6.1 Philips Hue (Würfel 1: Lichtszenen)

* **Lokale CLIP-API v2** (`https://<bridge>/clip/v2`) mit **Event-Stream (SSE)** – kein Polling nötig [belegt/Community]. Bridge v1 wird von HA seit 2023.1 nicht mehr unterstützt [Community] → **Bridge v2 vorausgesetzt** (Q3).
* Python-Bibliothek **`aiohue`** unterstützt v2 mit Events (`bridge.subscribe`) [belegt]; Szenen-Controller vorhanden (genaue Aktivierungs-API vor Einsatz im Quelltext prüfen).
* OctaLedger hat bereits einen **eigenen Hue-Adapter** (Zertifikat-Pinning per TOFU, Lampe/Raum/Szene, Retry-Regeln) – **ohne** Farbe und Event-Stream, **nicht gegen echte Bridge getestet**.
  Für OctaPiHub reicht das als Start; Farbe/Szenen-Liste/Event-Stream als Ausbau (Statusanzeige „aktuelle Lampenfarbe“ braucht Event-Stream oder Polling).
* **8 Würfelseiten → 8 Szenen** pro Raum/Modus, plus „neutral“ (in Halterung) z. B. = „alles aus“ oder „nichts tun“ (konfigurierbar, mit Entprellzeit gegen Zwischenlagen 0x09/0x0B/0x0D wie in OctaLedger beobachtet).
* Dynamische Szenen und Übergangszeiten (`transition`) sind in v2 möglich [Community].

### 6.2 Home Assistant (falls gewünscht)

* Ankopplung **über MQTT-Discovery**: Würfel erscheinen als Sensor/`event`-Entitäten; HA-Automationen können darauf reagieren. Der Dienst kann auch HA per **REST/WebSocket mit Long-Lived-Token** rufen (OctaLedger-Adapter vorhanden).
* HA auf dem **Pi 3 selbst** nur, wenn du bewusst auf Add-ons verzichtest (Container, 64 Bit) und wenig Integrationen hast [Community]. Besser: HA auf stärkerer Hardware (Pi 4/5, Mini-PC) – dann Pi 3 nur BLE/Musik.
* **Optional später:** HA-Bluetooth-Proxy-Weg (Variante E) mit ESP32 als **Reichweitenerweiterung**.

### 6.3 Google (Nest Hub / Nest Speaker / „Google Home Assistant“)

Hier muss man **drei verschiedene Dinge** auseinanderhalten:

1. **Nest/Cast-Geräte als Audio-Ausgabe** – **machbar und robust**: Google Cast, Python `pychromecast` (spielt eine **HTTP-URL** auf dem Gerät ab) [belegt/Community]. HA nutzt dasselbe (`media_player.play_media`).
   * Die URL muss **im LAN erreichbar** sein (Hostname/IP vom Cast-Gerät auflösbar) [Community].
   * Berichte über Probleme: Nest Hub spielt Gong, aber kein Sprachaudio; abgeschnittener Anfang bei Piper-TTS auf Hub/Hub Max; Quick-Play schlägt fehl, wenn Gerät im Idle/Schlaf ist (zweiter Versuch klappt);
     **kein Fortsetzen** der Musik nach einer Durchsage auf Google-Geräten; Nest-Audio-Sonderfälle mit Zertifikaten [alles Community, anekdotisch].
   * → Player-Dienst braucht **Retry**, „Gerät wecken“, eigene HTTP-Auslieferung (z. B. kleiner Webserver/Icecast auf dem Pi).
2. **Google-Home-Szenen/Routinen oder Geräte steuern** – **kein offizieller einfacher lokaler Weg** [Annahme, nicht abschliessend recherchiert]. Realistische Wege:
   * **über Home Assistant** (Integration „Google Assistant SDK“ kann Textbefehle an den Assistant senden [Annahme: aus Vorwissen, **vor Entscheidung prüfen**]);
   * **Matter**: Würfel-Hub als Matter-Brücke/-Gerät, das Google Home sieht (hoher Aufwand, **nicht recherchiert**);
   * Google-Home-APIs für Entwickler (Cloud-/App-basiert, **nicht recherchiert**).
   → **Empfehlung:** Google-Home-Steuerung zunächst **weglassen** und **nur Cast-Audio** unterstützen; Google-Geräte, die auch Hue sehen, bedient man ohnehin über Hue direkt.
3. **Sprachassistent (Google Assistant) als Eingabe** – nicht Teil des Würfel-Hubs; Hue ist bei Google Home meist direkt verknüpft.

### 6.4 MQTT als Rückgrat

* Mosquitto lokal auf dem Pi 3 (Login, nur LAN/Tailscale). Topics z. B. `octapihub/<würfel>/side`, `…/battery`, `…/connection`, `octapihub/player/state|cmd`, `octapihub/lights/scene`.
* Vorteile: HA/Node-RED optional, Pi 1/2 als **Anzeige/Audio-Clients** ohne eigene Logik, mehrere BLE-Standorte, einfaches Debuggen (`mosquitto_sub`).
* OctaLedger hat einen MQTT-Adapter (Publish); für OctaPiHub kommen **Subscribe/Befehle** und **Discovery** hinzu.

---

## 7. Musik und Audio (Würfel 2)

### 7.1 Ausgabewege

| Weg | Wie | Bemerkung |
|---|---|---|
| **Lokal am Pi 3** | ALSA/PipeWire → USB-DAC/HAT/pi-topPULSE | Der 3,5-mm-Ausgang des Pi ist klanglich schwach [Annahme/Praxiswissen] → USB-DAC oder I²S-HAT; PULSE-Lautsprecher-Pfad nur nach Machbarkeitstest (§2.2). |
| **Google Nest/Cast** | `pychromecast` + HTTP-Stream | siehe §6.3; Ein-/Ausschalten, Lautstärke, Gruppen unterstützt Cast (Details prüfen). |
| **Multiroom mit Pi 1/2** | **Snapcast** (Server auf Pi 3, `snapclient` auf Pi 1/2) | Snapcast verteilt nur Audio, ist **keine Quelle**; Quelle (MPD/Mopidy) füttert es per FIFO/Loopback [Community]. Nest-Geräte sind **keine** Snapcast-Clients. |
| **Spotify Connect** | `librespot`/Raspotify macht den Pi zum Connect-Ziel | braucht Spotify Premium [Annahme]; Steuerung der Wiedergabe per Spotify-Web-API. OctaLedger hat bereits einen Spotify-Adapter (Details dort prüfen). |

### 7.2 Software-Stacks (Pi 3 tauglich?)

| Stack | Eignung auf Pi 3 | Anmerkung |
|---|---|---|
| **Music Assistant** | **nein/unsicher** | Doku-Minimum: SBC **Pi 4+**, **64-Bit-OS**; Pi-3B+-Nutzer berichten Abstürze [belegt/Community]. Chromecast/AirPlay/DLNA-Player sind eingebaut. Gute Option, **falls** später stärkere Hardware (HA-Pi 4/5) kommt. |
| **MPD** (+ `mpc`/Python-`python-mpd2`) | **sehr gut** | Leichtgewichtig, lokale Bibliothek, Playlists; Streaming-Quellen nur per URL. |
| **Mopidy** | gut | Python, viele Erweiterungen (55 laut Quelle) [Community]; Spotify/YouTube-Plugins **in ihrem aktuellen Pflegezustand prüfen** [Annahme]. Läuft auf gemischter Hardware. |
| **Volumio / moOde / piCorePlayer** | als Komplettsystem | Eigene Images, eigener Webserver; passt schlecht zu „eigener Dienst auf Pi OS“ und teilt sich den Pi nicht gut mit BLE-Hub [Annahme]. Volumio hat laut Quelle die langsamste Boot-Zeit im Vergleich [Community]. |
| **mpv/ffmpeg + yt-dlp** | gut | Für Streaming-URLs (z. B. YouTube-Playlists wie in OctaLedger AP-65): **Rechts-/Stabilitätsrisiko** (Nutzungsbedingungen, Extraktor bricht regelmässig) [Annahme]. |

**Empfehlung:** **MPD oder mpv** als lokale Engine **hinter einer eigenen `PlayerBackend`-Schnittstelle** (lokal / Cast / Connect), damit man später Music Assistant oder Snapcast
einstecken kann, ohne die Würfel-Logik anzufassen. Cast-Wiedergabe = Pi liefert den Stream (transcodiert per ffmpeg, wenn nötig) und `pychromecast` startet ihn.

### 7.3 Würfel → Musik (Vorschlag, aus OctaLedger-Musikmodus abgeleitet)

Mögliche Belegung (alles konfigurierbar, **ohne** Rotationswinkel – der Würfel liefert nur **Seite 1–8 + Neutral** [Community-Protokoll]):

| Seite | Aktion (Beispiel) |
|---|---|
| 1 | Play/Pause |
| 2 | Nächster Titel |
| 3 | Vorheriger Titel |
| 4 | Lauter (Schrittweise) |
| 5 | Leiser |
| 6 | Quelle/Playlist wechseln (lokal → Cast → Connect) |
| 7 | Shuffle/Repeat umschalten |
| 8 | Sleep-Timer/Stopp |
| Neutral | z. B. pausieren oder ignorieren |

Hinweis: Eine **kontinuierliche** Lautstärke per „Drehen“ gibt das Protokoll nicht her; Lautstärke = Stufen oder Seitenhalten/Wiederholen [Annahme].

---

## 8. Bedienung headless (GUI ja/nein?)

* **Kein Streamlit auf dem Pi 3** als Dauerlösung: Streamlit ist für 1 GB RAM vermutlich unnötig schwer [Annahme, nicht gemessen]. In OctaLedger ist es ein reiner Client der API – also leicht ersetzbar.
* Empfohlener Dreiklang:
  1. **CLI** (`octapihub status|map|scene|play|…`), Hauptwerkzeug über SSH.
  2. **Lokale REST-API** (FastAPI, Token, nur LAN/Tailscale) – direkt aus OctaLedger übernehmbar.
  3. **Mini-Weboberfläche** (statische Seite oder HTMX, ein paar Hundert Zeilen) für Zuordnung Seite→Aktion, Status, Pairing (Hue-Knopf), Logs.
* Optional: **TUI** (Textual) für SSH; **Telegram-/Matrix-Bot** für Fernsteuerung von unterwegs.
* VNC nur, wenn du ein Desktop-GUI willst – nicht nötig.
* Konfiguration in **einer TOML-Datei** + Secrets getrennt (`/etc/octapihub/secrets…`, Rechte 600); nichts davon ins Repo.

---

## 9. Was aus OctaLedger wiederverwendbar ist (Ableitungs-Karte)

Grundlage: Verzeichnisstruktur `src/octaledger/…` und Dokumente (`ARCHITEKTUR.md`, `BLE_PROTOCOL.md`). **Keine Codeänderung**, nur Bewertung.

| OctaLedger-Baustein | Wiederverwendung in OctaPiHub | Anmerkung |
|---|---|---|
| `infrastructure/ble/` (Bleak, Scanner, Client, FakeTransport, Protokoll) | **hoch** | Bleak ist plattformübergreifend; Windows-Eigenheit „scan_before_connect“ auf Linux prüfen. FakeTransport ermöglicht Tests ohne Hardware. |
| `domain/orientation.py`, `state_machine.py`, `device_identity.py` | **hoch** | Entprellung, Neutralwerte, Verbindungsautomat. |
| `domain/battery.py`, `application/battery_*` | **hoch** | Würfel-Batterie für Anzeige/Warnung. |
| `agent/supervisor.py` (Worker je Würfel, ein Loop) | **hoch** | Kern der BLE-Stabilität. Ergänzen: BlueZ-Watchdog (§5). |
| `application/outbox.py`, `integration_service.py`, `infrastructure/integrations/*` (Hue, MQTT, HA, Webhook) | **hoch** | Warteschlange, Wiederholung, Schritte/Bedingungen (`steps.py`, `conditions.py`). |
| `domain/music*.py`, `playlist.py`, `application/music_service.py`, `player_service.py` | **mittel–hoch** | Musik-Logik; Ausgabe-Backend neu (MPD/Cast). |
| `infrastructure/audio/` (miniaudio + ffmpeg) | **mittel** | Funktioniert prinzipiell unter Linux, aber ARM-Wheels/ALSA-Verhalten ungeprüft [Annahme]; für Pi evtl. durch MPD/mpv ersetzen. |
| `infrastructure/storage/` (SQLite, Migrationen) | **mittel** | Für Konfiguration/Ereignisprotokoll sinnvoll; **Zeiterfassungs-Teile entfallen**. |
| `api/` (FastAPI, Token), `core/` (Errors mit Codes, Logging mit Redaktion, Settings) | **hoch** | Konzept „stabile Fehlercodes“ und Log-Redaktion behalten. |
| `agent/tray.py`, `infrastructure/system/toast.py`, `autostart.py`, `idle.py`, `file_dialog.py` | **entfällt** | Windows-spezifisch; Ersatz: systemd, journald. |
| `dashboard/` (Streamlit), `dashboard_client/` | **entfällt/ersetzt** | Siehe §8. |
| Zeiterfassung (Tätigkeiten, Projekte, Reports, Workday, Budget, ICS) | **entfällt** | Nicht Zweck von OctaPiHub. |
| Arbeitsweise (`CLAUDE.md`: Tests, ruff, mypy strict, Doku, Ehrlichkeit) | **übernehmen** | Gleiche Qualitätsregeln für das neue Repo. |

**Rechtliches/Organisatorisches:**
* OctaLedger hat **noch keine Lizenz** (offener Punkt im Projekt). Vor Übernahme von Code in ein **neues, evtl. öffentliches Repo** erst Lizenz klären; Abhängigkeiten mit (L)GPL beachten (z. B. `pystray`, `fpdf2`, `zeroconf` laut `pyproject.toml`) – in OctaPiHub nur nutzen, was gebraucht wird.
* **Nicht von Timeular/EARLY** abgeleitet oder unterstützt; Markenname „Timeular“ nicht im Projektnamen verwenden (wie bei OctaLedger).
* Die Wiederverwendung kann **als Bibliothek** (gemeinsames Paket) oder **als Kopie mit Herkunftsvermerk** erfolgen. Empfehlung: zuerst **Kopie**, später ggf. gemeinsames Kernpaket (`cubecore`) abspalten, wenn beide Projekte stabil sind.

---

## 10. Anzeige: pi-topPULSE / LED / Hub

**Anzeige-Plug-in-Schnittstelle** (nur Idee): `DisplayBackend.render(state)` mit Backends `Null`, `Log`, `MQTT`, `PulseMatrix`, `WS2812`. Dann ist die Hardware austauschbar.

Mögliche Darstellungen auf der **7×7-Matrix** (49 Pixel, RGB) [belegt: 7×7]:

* **Lampenfarbe** der aktiven Hue-Szene als Vollfläche/Rahmen.
* **Würfelstatus:** je Würfel ein Eck-Pixel/Balken: verbunden/Suche/Fehler (Farbe), aktuelle Seite als Ziffer/Punktmuster 1–8.
* **Batterie:** Balken (z. B. 7 Spalten = Prozent), rot < 15 % (Schwellen aus `battery.py` ableiten).
* **Musik:** Play/Pause-Symbol, Lautstärkebalken; **Titelnamen** passen kaum auf 7×7 → Lauftext sehr klein oder nur Symbole; Titel besser auf Web/CLI.
* **Ruhemodus/Helligkeit:** nachts dimmen/aus (Lichtstörung Schlafzimmer).

Grenzen/Risiken: Treiberstand (§2.2), Root-Pflicht, nur Pi 3, Matrix-Updaterate und Stromverbrauch (Helligkeit begrenzen). Der **Hub-Akku** ist die Akkuanzeige **des Hubs**, nicht der Würfel.

---

## 11. Umsetzungsvorschlag in Phasen (analog zum AP-Prinzip in OctaLedger)

| Phase | Inhalt | Prüfkriterium |
|---|---|---|
| **P0 Spike (1 Abend)** | Pi OS Lite 64 Bit auf Pi 3, `bleak` liest **einen** Würfel (Seitenwechsel im Terminal); Hue-Szene per HTTPS auslösen; pi-topPULSE-Test (Matrix an?) | Würfel-Seiten + Hue-Szene reproduzierbar; Entscheidung PULSE ja/nein |
| **P1 Kern** | Dienst (systemd), Konfig-TOML, Zuordnung Seite→Aktion, Hue-Adapter, Logging, CLI, BlueZ-Watchdog | 24 h Dauerlauf ohne manuellen Eingriff |
| **P2 Zwei Würfel** | Beide Würfel, Modi (Licht/Audio), Batterie, Reconnect-Tests | Stromausfall/BT-Neustart/Würfel aus → Selbstheilung |
| **P3 Audio lokal** | MPD/mpv, Playlists, Würfel-Steuerung, USB-DAC | Bedienung nur per Würfel |
| **P4 MQTT (+HA-Discovery)** | Mosquitto, Publish/Subscribe, Würfel als HA-Entitäten | Sichtbar in HA/`mosquitto_sub` |
| **P5 Cast-Ausgabe** | `pychromecast`, HTTP-Auslieferung, Retry/Wecken | Nest Hub/Speaker zuverlässig steuerbar |
| **P6 Anzeige** | LED/PULSE-Plug-in, Pi 1/2 als Anzeige/Audio-Client | Status sichtbar |
| **P7 Fernzugriff & Härtung** | Tailscale, Mini-Web-UI, Backups, Doku | Fernwartung ohne Portfreigabe |
| **P8 Optional** | Streaming (Spotify Connect/YouTube), Snapcast-Multiroom, ESP32-BLE-Proxys, Matter | je nach Bedarf |

Jede Phase: Tests (mit FakeTransport), Doku, Hardware-Test **ehrlich als „gemessen“ oder „nur simuliert“ kennzeichnen**.

---

## 12. Risiken und Gegenmassnahmen

| Risiko | Wirkung | Gegenmassnahme |
|---|---|---|
| BLE-Instabilität (BlueZ) | Würfel reagieren nicht | Ein Prozess, serialisieren, Watchdog, USB-Dongle (§5) |
| Pi 3 Funk teilt WLAN/BT | Aussetzer | LAN-Kabel, USB-BT-Dongle |
| Würfelprotokoll nur Community, nur 1 Würfel getestet | Falsche Seiten/Neutralwert | Kalibrierung, Rohwerte loggen, beide Würfel testen |
| PULSE/Hub-Treiber veraltet | keine Anzeige | Spike P0, Fallback WS2812, Plug-in-Design |
| Cast/Nest-Eigenheiten | Wiedergabe schlägt fehl/Gong ohne Ton | Retry, wecken, eigene URL, Logs; Fallback lokal |
| YouTube/Streaming-Extraktoren brechen | Musik fällt aus | Streaming optional, lokale Musik als Basis |
| Google-Home-Steuerung ohne offizielle lokale API | Funktion fehlt | Nur Cast + Hue direkt; HA als Brücke später |
| SD-Karten-Verschleiss | Datenverlust | WAL, Log-Limit, SSD/hochwertige Karte, Backups der TOML+DB |
| Sicherheit (Tokens, Hue-Key, MQTT) | Fremdsteuerung | Tailscale statt Portforwarding, Auth überall, Secrets 600, Log-Redaktion |
| Unklare Lizenz/Abhängigkeiten | rechtlich | Lizenz vor Veröffentlichung klären (§9) |
| Pi 3 überlastet (HA+MA+Dienst) | Ruckeln/Abstürze | Rollen trennen: Pi 3 = BLE+Logik+Audio, schwere Dienste auf anderes Gerät |

---

## 13. Offene Fragen an dich (bestimmen die Detailentscheidung)

* **Q1:** Meinst du mit „Google Home Assistant“ **Google Home/Assistant** (Sprachassistent, Nest-Geräte) oder **Home Assistant** (die Open-Source-Software) – oder beides? Läuft Home Assistant bereits irgendwo?
* **Q2:** Sollen die Würfel **daheim bleiben** (Pi 24/7) oder **mitreisen**? (entscheidet über §3.4: normaler Hub vs. mobiler Hub)
* **Q3:** Welche **Hue Bridge** (v2 „eckig“ mit Ethernet, oder Bridge v1)? Wie viele Räume/Szenen?
* **Q4:** Wie wichtig ist **Streaming** (Spotify/YouTube) gegenüber **lokalen Dateien**? Hast du Spotify Premium?
* **Q5:** Soll die Musik primär auf **Nest** laufen oder auf **Pi-Lautsprechern**? Welche Lautsprecher/DAC sind vorhanden?
* **Q6:** Wo stehen Würfel, Pi und Nest-Geräte (Distanzen, Wände)? → BLE-Reichweite, ggf. zweiter BLE-Standort.
* **Q7:** Soll das Projekt **öffentlich** werden? (Lizenz, Namens-/Markenprüfung, Doku-Sprache)
* **Q8:** Muss der Pi 3 weitere Dienste tragen (HA, Node-RED, Pi-hole …)? → RAM-Planung.

---

## 14. Was ich **nicht** verifizieren konnte (Transparenz)

* Ob jemand den **Timeular/ZEI-Würfel bereits mit Home Assistant/ESPHome** verbunden hat – Suche ohne Treffer.
* **Pi 1:** Bluetooth/RAM-Angaben aus Vorwissen, nicht aus der Suche.
* **Konkrete Treiberlage von `ptpulse`/`pt-device-manager` unter Bookworm/Trixie.**
* Aktueller **Pflegezustand** von Mopidy-Spotify/-YouTube und der Hue-Szenen-API in `aiohue`.
* Offizielle **Home-Assistant-Aussage** zur Pi-3-Unterstützung (nur Forum/Sekundärquellen); Deprecation-Primärquelle (22.05.2025) nicht abgerufen.
* **Google Home APIs / Matter**-Wege (nicht recherchiert).
* **Speicherbedarf** von Streamlit vs. Mini-Web-UI auf dem Pi 3 (nicht gemessen).
* Messwerte zu **BLE-Reichweite/Latenz** (nur Allgemeinwissen).

---

## 15. Quellen (Websuche vom 2026-11-23)

BLE/Bleak/BlueZ
* Bleak-Issue #1858 (mehrere Prozesse, Abort): https://github.com/hbldh/bleak/issues/1858
* Bleak-Issue #1240 (unzuverlässige Verbindung): https://github.com/hbldh/bleak/issues/1240
* Bleak BlueZ-Client (Quelltext, BlueZ ≥ 5.55): https://bleak.readthedocs.io/en/latest/_modules/bleak/backends/bluezdbus/client.html
* Stale-„Connected“-Zustand (Beispielprojekt): https://github.com/KristianP26/ble-scale-sync/issues/472
* Gleichzeitige BLE-Befehle auf dem Pi: https://scalablehuman.com/2026/06/26/how-to-fix-concurrent-ble-commands-on-raspberry-pi/

Home Assistant / ESPHome / Proxys
* ESPHome Bluetooth Proxy: https://esphome.io/components/bluetooth_proxy/
* `btproxy` (Linux/Pi): https://github.com/yuzi-co/btproxy
* `bleproxy` (Rust): https://github.com/fiam/bleproxy
* Forum: externer Pi als BLE-Scanner: https://community.home-assistant.io/t/using-external-raspberry-pi-to-scan-for-bluetooth-devices/891366
* Pi 3 + Home Assistant (Einschätzung): https://electricalflux.com/mcu-projects/home-assistant-raspberry-pi-3-setup-limits
* HA-Abkündigung Core/Supervised/32 Bit (DietPi-Diskussion): https://github.com/MichaIng/DietPi/issues/7544
* Pi 2 → Pi 3 / 64 Bit (HA-Forum): https://community.home-assistant.io/t/rpi-2-32bit-os-to-rpi-3-64bit-os/906027
* HA in Docker auf dem Pi: https://darkwolfcave.de/home-assistant-raspberry-pi-docker/
* Node-RED + HA: https://raspberry.tips/en/smart-home/node-red-home-assistant-raspberry-pi

Raspberry Pi OS / Hardware
* Trixie-Ankündigung: https://www.raspberrypi.com/news/trixie-the-new-version-of-raspberry-pi-os/
* Pi 3 Model B: https://www.raspberrypi.com/products/raspberry-pi-3-model-b/
* Pi 3 Specs/Benchmarks: https://magazine.raspberrypi.com/articles/raspberry-pi-3-specs-benchmarks
* Vergleichstabelle: https://community.element14.com/products/raspberry-pi/w/documents/17004/raspberry-pi-comparison-chart

Hue
* aiohue (v2-Beispiel): https://sources.debian.org/src/python-aiohue/4.8.0-1/examples/v2_bridge.py
* aiohue Szenen-Controller: https://sources.debian.org/src/python-aiohue/4.8.0-1/aiohue/v2/controllers/scenes.py
* HA Hue-Integration: https://home-assistant.io/integrations/hue
* Hue-Fernzugriff (Anleitung): https://www.imore.com/how-control-your-philips-hue-lights-when-youre-away-home

Cast/Nest/TTS
* HA Google Cast: https://www.home-assistant.io/components/cast
* pychromecast-Beispiel (BubbleUPnP/Quick-Play): https://sources.debian.org/src/pychromecast/14.0.9-1/examples/bubbleupnp_example.py
* HA-Forum Nest-Hub-TTS-Probleme: https://community.home-assistant.io/t/tts-on-nest-hub/389074 ,
  https://community.home-assistant.io/t/node-red-tts-messages-truncated-on-google-nest-hub/796506 ,
  https://community.home-assistant.io/t/wth-is-there-no-option-to-resume-media-after-a-tts-announcement/468339/47

Musik
* Music Assistant – Installation/Anforderungen: https://www.music-assistant.io/installation
* Music Assistant Server (GitHub): https://github.com/music-assistant/core
* Snapcast-Überblick: https://pidiylab.com/multi-room-audio-with-snapcast-on-raspberry-pi/
* Vergleich Snapcast/Volumio: https://www.libhunt.com/compare-snapcast-vs-Volumio2
* Mopidy (PyPI): https://pypi.org/project/Mopidy/

pi-top
* pi-topPULSE: https://github.com/pi-top/pi-topPULSE
* pi-topHUB v1 (Community): https://github.com/GarrettSocling/pi-topHUB-v1
* pi-top Python SDK: https://github.com/pi-top/pi-top-Python-SDK/blob/master/docs/overview.rst

Fernzugriff
* Tailscale + Raspberry Pi (SSH/VNC): https://itsfoss.com/tailscale-raspberry-pi-ssh/
* Tailscale + Home Assistant: https://www.tailscale.com/blog/remotely-access-home-assistant

OctaLedger (intern, nur zur Ableitung gelesen)
* `docs/ARCHITEKTUR.md`, `docs/BLE_PROTOCOL.md`, `pyproject.toml`, Modul-Docstring `infrastructure/integrations/hue.py`


---
---

# NACHTRAG 1 (2026-11-23) – Antworten auf Rückfragen

Vertrauenslabels wie oben: **[belegt]**, **[Community]**, **[Annahme]**. Auch dieser Nachtrag ist reine Recherche, **nichts davon wurde an
deiner Hardware ausprobiert**.

## 16. Antworten und Korrekturen

### 16.1 Du hast auch einen Raspberry Pi 4 – ändert das etwas?

* Der **Pi 3 bleibt die richtige Wahl für den Hub-Dienst** (BLE-Logik, Hue, Musiksteuerung): geringe Last, eingebautes BLE, und der Pi 4 ist
  gebunden. Das ist kein Nachteil für dieses Projekt.
* Wäre ein Pi 4 (≥ 2 GB, 64 Bit) später frei, wäre er der **bessere Ort für schwere Zusatzdienste**: Home Assistant (offizielle Doku: Pi 4/5
  mit mindestens 2 GB RAM) [belegt] und Music Assistant (Doku-Minimum Pi 4) [belegt]. Die Architektur D (§3.2) erlaubt diese spätere
  Trennung: **Pi 3 = Würfel + Logik**, **Pi 4 = optionale schwere Dienste**, gekoppelt über MQTT.
* **Heute nötig? Nein.** Nichts in der Empfehlung verlangt den Pi 4.

### 16.2 pi-topPULSE direkt am Raspberry Pi – geht das ohne Zusatzhardware?

**Kurz: Ja, im Grundsatz. Er ist ein HAT und steckt auf die 40-polige GPIO-Leiste, einen pi-top-Hub braucht man nicht. Es gibt aber zwei Stolpersteine.**

Belegtes:
* Das Raspberry-Pi-Magazin schreibt in seinem Test, der PULSE funktioniere als **HAT auch ohne pi-top** direkt auf dem Pi, man müsse aber
  **zuerst eine Stiftleiste auf seine Unterseite stecken**; nach **einer Softwareinstallation** laufe er unter Raspberry Pi OS. Die Passform sei
  eng, die Anschlüsse überstehen den Pi [belegt, Test ist alt].
  → **Prüfe an deinem Gerät, ob unten bereits eine Buchsenleiste sitzt.** Falls nein, ist eine 2×20-Buchsenleiste die einzige „Zusatzhardware“.
* Belegte Anschlüsse laut GitHub/pinout.xyz: **I²C (GPIO 3/5, Steuerchip auf Adresse 0x24), UART (Pins 8/10), I²S (Pins 12/35/40)**.
  Der **Lautsprecher läuft über I²S**, **LED-Matrix und Mikrofon über UART** [belegt].
* Installation unter Raspberry Pi OS: Paket **`pt-pulse`** per `apt`, danach **Neustart** [belegt, README]. Python-Module `ptpulse`
  (`configuration`, `microphone`, `ledmatrix`); Root-Rechte nötig (§2.2).
* Das README nennt den PULSE nur für **Raspberry Pi 3** unterstützt (UART-Taktproblem früherer Modelle) [belegt]. Der Pi 2 scheidet für den Pulse also aus.

**Stolperstein 1 – Bluetooth gegen UART (wichtig, weil die Würfel BLE brauchen):**
* Auf dem **Pi 3** hängt der eingebaute Bluetooth-Chip standardmässig an der „guten“ UART (PL011). Die UART an den GPIO-Pins ist dann die
  **Mini-UART**, deren Baudrate am CPU-Takt hängt [belegt, allgemeine Pi-Anleitungen].
* Der **Pulse nutzt genau diese UART** für Matrix und Mikrofon. Mögliche Auflösungen [Community, **für den PULSE nicht getestet**]:
  1. **Bluetooth behalten**, Mini-UART stabilisieren: `dtoverlay=pi3-miniuart-bt` und `core_freq=250` in `/boot/firmware/config.txt`
     (kann das Bluetooth-Maximaltempo senken). Vermutlich der vom pi-top-README vorgesehene Weg [Annahme].
  2. **Onboard-Bluetooth abschalten** (`dtoverlay=disable-bt`) und einen **USB-Bluetooth-Dongle** für die Würfel nehmen. Das gibt die gute UART
     frei und trennt den Würfel-Funk vom Onboard-Chip. **Meine Favoritenvariante**, weil du Dongles besitzt [Annahme].
* → Teil des Spikes P0: beides kurz ausprobieren; Erfolg = Matrix leuchtet **und** die BLE-Verbindung zum Würfel hält > 1 h.

**Stolperstein 2 – Pflege/Treiber:** `pt-pulse`/`pt-device-manager` sind alt (§2.2). Ob das Paket unter **Trixie/Bookworm** noch installierbar ist,
konnte ich nicht belegen. Fallback: älteres Pi-OS-Image nur für dieses Gerät, oder Pulse nur als Lautsprecher/Matrix über direkten Zugriff.

**Was der Pulse im Projekt leisten kann:**

| Teil | Nutzung | Grenzen |
|---|---|---|
| 7×7-RGB-Matrix | Statussymbole, Würfelseite, Batterie-Balken, Lampenfarbe | nur 49 Pixel; Ziffern 1–8 gehen, Text nicht |
| Lautsprecher (I²S) | Rückmeldetöne („Seite erkannt“), Durchsagen, Wecker | kleiner Lautsprecher → **für Alltagsmusik nur eingeschränkt** [Annahme, Klangtest fehlt] |
| Mikrofon | spätere Sprachfunktionen | nicht Teil des Plans; Datenschutz beachten |

### 16.3 Sense HAT (8×8 RGB-Matrix, Joystick, Sensoren) – und Pulse + Sense HAT zusammen?

* **Sense HAT:** offizielles Raspberry-Pi-HAT mit **8×8-RGB-Matrix, Joystick, Gyroskop/Beschleunigung/Magnet, Druck-, Temperatur-, Feuchtesensor**;
  **offiziell unterstützte Python-Bibliothek** (`sense-hat` per `apt`, aktiviert I²C und installiert Abhängigkeiten); der Joystick ist ein normales
  Eingabegerät (evdev/pygame) [belegt, Raspberry-Pi-Doku].
* **Trixie-Kompatibilität** des Pakets konnte ich nicht bestätigen (weder positive noch negative Treffer) → in P0 testen.
* **Vorteile für OctaPiHub:** 8×8 ist **besser als 7×7**: Ziffern 1–8 pro Würfelseite gut lesbar, kleine Symbole/Balken; der **Joystick ist zusätzliche
  Bedienung** (Lautstärke/Skip ohne Würfel); Umweltsensoren als Bonus (die Temperatur wird typischerweise von der Pi-Abwärme verfälscht [Annahme]).
* **Beides gleichzeitig auf einem Pi?** Schwierig:
  * Jedes HAT hat ein **ID-EEPROM** an derselben Adresse; laut Foren ist **nur ein „echtes“ HAT** mit Auto-Konfiguration möglich; Stapeln ginge nur mit
    Tricks (hohe Stiftleiste, EEPROM-Pins nicht durchverbinden) [Community, alt]. Ohne Auto-Konfiguration kann man Treiber **manuell** laden [Annahme].
  * **Pinbelegung:** Pulse = GPIO 3/5 (I²C), 8/10 (UART), 12/35/40 (I²S). Das Sense HAT nutzt **I²C** (ebenfalls GPIO 3/5; Adressen vermutlich ≠ 0x24 [Annahme])
    und eventuell Zusatzpins für den Joystick-Interrupt [Annahme, nicht belegt → Schaltplan prüfen]. Elektrisch also **wahrscheinlich verträglich**,
    **mechanisch** (beide wollen auf die Leiste) **eher nicht** ohne Verlängerungskabel/Stapelleiste.
* **Praktische Lösung (passt zur MQTT-Architektur):** **zwei Anzeige-Orte** statt eines überladenen Pi:
  * **Variante A:** **Pi 3 = Hub + Pulse** (Matrix + Lautsprecher am Hub), **Pi 2 = Anzeige-Knoten mit Sense HAT** (liest MQTT; Pi 2 per Ethernet).
  * **Variante B (einfachster Start):** **Pi 3 = Hub + Sense HAT** (offiziell unterstützt, keine Bluetooth-UART-Kollision), Pulse **später** nach erfolgreichem Test.
* **Empfehlung:** **B zuerst** (geringstes Risiko, bestes Display), **Pulse in P0 testen**; klappt er (Treiber + Bluetooth), wechsle zu **A**,
  falls du den Lautsprecher am Hub möchtest. Die Anzeige bleibt dank `DisplayBackend` (§10) austauschbar.

### 16.4 Brauche ich WLAN? Brauche ich Dongles?

* **Der Pi 3 hat WLAN und Bluetooth eingebaut** [belegt]. **WLAN-Dongle: nicht nötig.**
* **Besser: LAN-Kabel.** Internet (Streaming, Updates, Tailscale) und lokales Streaming zu Nest laufen genauso über Ethernet; das Kabel ist stabiler und
  entlastet den **gemeinsam genutzten WLAN/BT-Funkchip** (§2.1) [Annahme: Praxiswissen]. Streaming braucht kein „besonderes WLAN“, nur Netzwerk.
* **Nest/Cast und Hue müssen im selben Netz (Subnetz) wie der Pi sein**, mDNS/Multicast darf nicht blockiert sein (Gäste-WLAN/VLAN meiden) [Community].
* **Bluetooth-Dongle: ja, sinnvoll** – stabilere BLE-Verbindung, bessere Reichweite durch freie Position (USB-Verlängerung), und er macht den
  Onboard-Chip für den Pulse frei (§16.2). Auf BlueZ-Kompatibilität des Chipsatzes achten [Annahme].
* **Pi 1/2:** haben Ethernet, aber kein WLAN – ein WLAN-Dongle ist dort nur nötig, wenn kein Kabel liegt.

### 16.5 Kann man auf den Nest Hubs Musik abspielen?

* **Ja.** Nest Hubs (Display) und Nest Speaker sind **Google-Cast-Ziele**: Audio lässt sich per Cast abspielen; das ist derselbe Weg, den Home Assistant
  (`media_player.play_media`) und `pychromecast` nutzen [belegt, HA-Cast-Doku/Google-Support]. **Für OctaPiHub zwei Wege:**
  1. **Eigener Stream:** Der Pi liefert eine **HTTP-URL** (lokale Dateien oder aufbereiteter Stream), `pychromecast` startet sie auf dem Hub/Speaker.
  2. **Dienst-Konto-Weg:** je Dienst (z. B. Spotify-Steuerung des Zielgeräts), nur wenn der Dienst Steuerung von aussen erlaubt [Annahme].
* **Besonderheiten:** Bilder/Videos verhalten sich auf Hubs uneinheitlich [Community]; Gruppen (Lautsprecher + Hub) sind möglich, Details prüfen [Annahme].
  Nutzer melden: Gong ohne Sprachausgabe, abgeschnittener Anfang, ein schlafendes Gerät nimmt den ersten Versuch nicht an, kein automatisches Fortsetzen nach
  Durchsagen (§6.3) [Community].

### 16.6 Hue Bridge v1 (rund, weiss) – das ist wichtig

**Eine runde, weisse Bridge ist die erste Generation (v1). Die aktuelle Generation (v2) ist quadratisch** [belegt].

Fakten:
* Signify hat die **v1 am 30.04.2020 aus dem Support genommen**: keine Updates mehr, die offizielle Hue-App funktioniert nicht mehr mit ihr,
  die v1-App wurde 2022 eingestellt [belegt, Signify-Policy/Presse].
* **Nur noch lokal steuerbar.** Cloud/Fernzugriff und **Sprachassistenten (Google Home, Alexa)** funktionieren mit der v1 **nicht mehr** [belegt].
  → Das erklärt vermutlich, warum du bei Google Home unsicher bist.
* **Kein Matter, kein HomeKit** bei der v1 (die v2 hat beides) [belegt, Vergleichstabelle].
* Die **lokale v1-API läuft für Drittanbieter weiter** (z. B. unterstützt `homebridge-hue` v1 und v2) [Community]; neue Funktionen (z. B. dynamische Szenen) gibt es
  laut Signify nur in der **v2-API**, die v1-API werde langfristig entfallen [Community, openHAB-Doku]. Ob sie **Stand heute** noch aktiv ist, habe ich
  **nicht belegt** – in P0 prüfen (`http://<bridge-ip>/api/config` im LAN erreichbar?).

**Folgen für OctaPiHub:**
* Mein Plan (§6.1: **CLIP API v2**, Event-Stream, `aiohue`) **funktioniert mit der v1 nicht**. Auch der **vorhandene OctaLedger-Hue-Adapter** ist laut Modul-Docstring
  auf **CLIP v2** ausgelegt und wäre für die v1 **nicht nutzbar**.
* **Zwei realistische Wege:**

| Weg | Aufwand | Vor-/Nachteile |
|---|---|---|
| **v1 weiterbetreiben**, v1-REST-Adapter schreiben (Szenen/Gruppen über die lokale v1-API, ähnlich `phue`) | klein | läuft lokal; **unverschlüsseltes HTTP im LAN**, keine neuen Funktionen, API kann verschwinden, **kein Matter/Google-Home-Zugriff** |
| **Bridge v2 kaufen** (Preis nicht recherchiert) | Kauf + Umzug der Lampen | CLIP v2 + Event-Stream, **Matter**, Google-Home-Anbindung (inkl. **lokaler Matter-Steuerung** über Nest Hub (2. Gen)/Hub Max, Update Oktober 2026) [belegt, 9to5Google/Philips-Supportseite], weiter Updates |

* **Empfehlung:** **Zum Start mit v1 testen** (P0: v1-Adapter-Prototyp), **mittelfristig Bridge v2**, wenn dir Hue wichtig ist. Eine Adapter-Schnittstelle
  (`LightsBackend`: `HueV1`, `HueV2`, später `Matter`) hält dich unabhängig.
* Beim Wechsel auf v2 müssen die Lampen übertragen/neu angelernt werden [Annahme; Vorgehen laut Philips-Anleitung prüfen].

### 16.7 Matter – wird das unterstützt? Kann ich nicht alles über Matter steuern?

**Was Matter ist (vereinfacht):** ein **gemeinsamer Standard**, damit Geräte und Steuerzentralen verschiedener Hersteller zusammenpassen. Es ist **kein Programm**,
das „alles automatisch steuert“, sondern definiert **Rollen** [belegt, HowToGeek/Fachseiten]:

| Rolle | Aufgabe | Beispiele |
|---|---|---|
| **Device** | ist ein Matter-Gerät | Lampe, Steckdose, Schalter |
| **Controller** | nimmt Geräte auf, bedient sie, führt Automationen aus | Google Home auf Nest Hub (2. Gen)/Hub Max, Home Assistant, Apple Home, Alexa |
| **Bridge** | zeigt Nicht-Matter-Geräte als Matter-Geräte | **Hue Bridge v2** (Zigbee → Matter) |
| **Thread-Border-Router** | verbindet Thread-Geräte mit dem Heimnetz | Nest Hub, HomePod, … |

* Google nennt den **Nest Hub (2. Gen)** u. a. als **Matter-Controller** im Google-Home-Ökosystem [belegt].
* **Nicht alles ist Matter:** Matter deckt **Gerätetypen** ab (Lampen, Schalter, Sensoren, Thermostate …). **Musikwiedergabe auf Nest** läuft **über Google Cast**, nicht über
  Matter [Annahme; die Recherche ergab keinen Treffer zu Audio über Matter]. Einen einzelnen Standard, über den „alle Devices“ in deinem Szenario laufen, gibt es also **nicht**.

**Drei mögliche Rollen für OctaPiHub – keine ist Pflicht:**
1. **OctaPiHub als Matter-*Controller*** (steuert Matter-Lampen direkt): technisch möglich, aber **schwer** (Fabric-Verwaltung, Commissioning, Matter-Server).
   Der klassische `python-matter-server` ist **eingestellt (letzte Version 8.1.2)**, Nachfolger ist der **matter.js-basierte „Matter Server“** (Home Assistant „Matter Server app 9.0“);
   die Add-on-Version unterstützt **kein 32 Bit** [belegt, GitHub/HA-Blog]. → Sinnvoll nur **über Home Assistant**, nicht selbstgebaut.
2. **OctaPiHub als Matter-*Gerät/Bridge*** (Würfel erscheinen als „Generic Switch“ in Google Home, sodass „Würfel-Seite 3“ eine Automation auslösen kann):
   Der Gerätetyp **Generic Switch** meldet Tasten-Ereignisse (kurz/lang/mehrfach); ein Gerät kann **mehrere Endpunkte** haben (8 Würfelseiten = 8 „Tasten“)
   [belegt, Matter Device Library 1.2; Espressif-Beispiel]. Home Assistant hat dafür **Event-Entitäten**; der Fork **home-assistant-matter-hub** veröffentlicht HA-Entitäten
   (u. a. GenericSwitch) als Matter-Geräte für Google Home/Apple/Alexa [belegt, Projektseiten; das Original wurde Januar 2026 eingestellt, der Fork wird gepflegt].
   **Einen Matter-Geräte-Stack selbst in Python zu bauen**, dafür fand ich **keinen reifen Weg** [Annahme/nicht abschliessend recherchiert]; praktikabler sind ein **ESP32 mit `esp-matter`**
   oder der Umweg über Home Assistant. Ob **Google Home** Generic-Switch-Ereignisse als Automations-Auslöser nutzt, ist **nicht belegt**.
3. **Hue v2 → Matter → Google Home**, ohne dass OctaPiHub beteiligt ist: läuft mit der **Hue Bridge v2** automatisch [belegt]; **mit deiner v1 nicht**.

**Fazit Matter:** Gute **spätere Erweiterung**, aber **kein Ersatz** für Hue-API und Cast. Für die Kernfunktionen (Würfel → Hue-Szene, Würfel → Musik auf Nest) ist es
**nicht nötig und nicht der einfachste Weg**. In der Architektur als **optionales Backend (`MatterBackend`)** vorsehen, aber **nicht als Phase 1**.

### 16.8 Was ist Home Assistant? Warum habe ich „Home Assistant oder Node-RED daneben“ geschrieben? Braucht es das?

**Home Assistant (HA)** ist eine **kostenlose Open-Source-Software für Heimautomation**, die du selbst betreibst (Pi, Mini-PC, NAS). Man kann sie sich als **Zentrale mit „Treibern“** vorstellen:
* hunderte **Integrationen** (Hue, Google Cast, Matter, MQTT, Sonos, Wetter, Steckdosen …); jedes Gerät erscheint als Entität,
* **Oberfläche/App**, Verlauf, **Automationen** („wenn X, dann Y“),
* **Add-ons** (nur bei der HA-OS-Variante): fertige Zusatzdienste, z. B. Mosquitto, Node-RED, Matter-Server [belegt].
* **Wichtig:** Home Assistant ist **nicht Google Home**. Google Home ist Googles App/Cloud-Dienst für Nest/Google-Geräte. Hast du Home Assistant nie selbst installiert,
  hast du es sehr wahrscheinlich **nicht** und nutzt **nur Google Home** [Annahme; sag es mir, falls du einen eigenen Server hast].

**Node-RED** ist ein **grafischer Editor, um Ereignisse zu verdrahten** („wenn MQTT-Nachricht → rufe URL auf“). Es ist keine Zentrale, nur ein Werkzeug für Abläufe.

**Warum „daneben“?** Weil sie **Zusatznutzen** bringen *können*, nicht weil OctaPiHub sie braucht:

| Du willst … | HA/Node-RED hilfreich? |
|---|---|
| Würfelseite → Hue-Szene, Würfel → Musik auf Nest | **nein**, OctaPiHub macht das allein |
| Handy-App/Dashboard für alle Geräte | ja (HA) |
| Matter-Controller, viele Hersteller, Google-Assistant-Anbindung | ja (HA) |
| Zeit-/Sensor-Automationen („20 Uhr → Licht dimmen“) | ja (HA oder Node-RED) |
| Grafisches Verdrahten statt Code | ja (Node-RED) |

**Braucht es das? Nein, nicht für die Grundfunktionen.** Es ist eine **spätere, optionale Brücke**. MQTT als Rückgrat hat nur den Grund: **Falls** du HA irgendwann
(z. B. auf dem Pi 4) einsetzt, **sieht** es die Würfel sofort, ohne OctaPiHub umzubauen. Ohne HA lässt man es weg – das spart auch RAM auf dem Pi 3.

### 16.9 Warum ist der Raspberry Pi 3 „für Musik zu schlecht“? Reicht die Rechenleistung nicht? Und der Pulse?

**Das war in meinem ersten Text zu pauschal. Präzise:**
* **Musik abspielen ist für den Pi 3 kein Problem.** Dekodieren von MP3/FLAC/AAC ist leicht; MPD gilt als sehr schlank und läuft auch auf deutlich schwächeren Geräten
  [Annahme + Community: ein MPD-Nutzer meldet < 15 % CPU bei FLAC-Ausgabe auf schwächerer Hardware, nur indikativ].
* **Zu schwach/unpassend ist der Pi 3 für *Music Assistant* als Server:** offizielles Minimum **Pi 4**, **64-Bit**, bei manueller Installation Python 3.14+ und ffmpeg ≥ 6.1;
  ein Pi-3B+-Nutzer meldet Abstürze [belegt/Community]. Gründe (teils Annahme): grosse Bibliotheksdatenbank, viele parallele Streams, Transcoding je Player, Metadatenabrufe –
  und das **zusätzlich** zu BLE und Hue.
* **Echte Engpässe auf dem Pi 3:**
  1. **Transcoding in Echtzeit** (z. B. Stereo-MP3/AAC für Cast/Icecast): Berichte über **hohe CPU-Last**, bei Spitzen (> 100 %) brachen Streams ab (Pi 3B+ mit tvheadend);
     Mono ging, Stereo war grenzwertig [Community, andere Anwendungen, nur indikativ].
  2. **Streaming-Extraktion** (z. B. `yt-dlp`, TLS, Python-Start): kurze CPU-Spitzen beim Titelwechsel [Annahme].
  3. **Bibliotheks-Scan** grosser Sammlungen und Cover-Verarbeitung [Annahme].
* **Gegenmassnahmen:** Quellen **ohne Neukodierung** weiterreichen (Cast spielt gängige Formate direkt [Annahme – Formatliste prüfen]); wenn Transcoding nötig,
  **Bitrate/Kanäle reduzieren** oder **FLAC/WAV im LAN**; **MPD statt Music Assistant**; **Streaming optional**.
* **Pulse und Rechenleistung:** Der Pulse **bringt keine Rechenleistung**, er ist nur **Ausgabe/Anzeige** (Lautsprecher, Matrix, Mikrofon) und hilft nicht gegen den
  Transcoding-Engpass. Sein Nutzen für Musik: **lokale Ausgabe ohne USB-DAC** (kleiner Lautsprecher, für Rückmeldetöne/leise Hintergrundmusik); für **Musik in guter Qualität**
  bleiben **Nest oder ein externer Lautsprecher/DAC** die bessere Wahl [Annahme].
* **Zusammenfassung:** **Pi 3 = ja für MPD + Cast-Streaming kompatibler Dateien; nein für Music Assistant/HA-Komplettlösung.**

### 16.10 Überarbeitete Empfehlung (ersetzt die Kurzfassung §0 bei Widersprüchen)

1. **Hub = Pi 3**, per **LAN-Kabel**, **USB-Bluetooth-Dongle** für die Würfel (Onboard-Bluetooth ggf. abschalten, §16.2). Der Pi 4 bleibt unberührt.
2. **Software = eigener schlanker Python-Dienst (Variante C/D), MQTT als Rückgrat**; **Home Assistant/Node-RED optional und später** (§16.8).
3. **Licht:** zuerst **Hue v1** per v1-Adapter (Abstraktion `LightsBackend`), **Bridge v2 mittelfristig empfehlenswert** (Matter, Google Home, CLIP v2) (§16.6).
4. **Musik:** **MPD (oder mpv)** + `pychromecast` für Nest; lokale Dateien als Basis, Streaming optional; **kein Music Assistant auf dem Pi 3** (§16.9).
5. **Anzeige:** Start mit **Sense HAT (8×8 + Joystick)** auf dem Pi 3; **Pulse im Spike testen** (Lautsprecher + Bluetooth-UART-Konflikt), bei Erfolg Variante A (§16.3).
6. **Matter:** als **späteres optionales Backend/Brücke**, nicht Phase 1 (§16.7).
7. **Fernzugriff:** Tailscale (SSH/VNC/Web), keine Portfreigabe.

### 16.11 Angepasster Spike P0 (ein Abend, mit Pi 3)

| Schritt | Test | Erfolg |
|---|---|---|
| 1 | Pi OS Lite 64 Bit; LAN; USB-BT-Dongle; BlueZ-Version prüfen | `bluetoothctl show` zeigt den Dongle |
| 2 | `bleak` liest Würfel A (und B) | Seitenwechsel im Terminal (Rohwerte loggen) |
| 3 | `http://<bridge>/api/config` (v1 erreichbar?) | Antwort mit Bridge-Daten; Szene per v1 auslösbar |
| 4 | Sense HAT: `apt install sense-hat`, Matrix + Joystick | Ziffer 1–8 sichtbar, Joystick-Ereignis |
| 5 | Pulse (nur Pi 3): `pt-pulse`, Matrix + Lautsprecher, dann **zusammen mit** BLE | Matrix + Ton + BLE gleichzeitig stabil |
| 6 | `pychromecast` findet Nest Hub/Speaker, spielt eine MP3-URL | Ton auf dem Gerät; Verhalten im Schlafzustand notieren |
| 7 | CPU/RAM mit allem gleichzeitig messen (`htop`) | Ergebnis ins Projektprotokoll |

### 16.12 Weitere offene Punkte nach den Antworten

* **Q9:** Hast du je einen **Home-Assistant-Server/eine App** installiert? (vermutlich nein → nur Google Home) (§16.8)
* **Q10:** Bridge-v2-Kauf okay, oder soll es bei der **v1** bleiben? (§16.6)
* **Q11:** Hat dein Pulse **unten eine Buchsenleiste** zum Aufstecken? Wenn nein: 2×20-Buchsenleiste nötig (§16.2).
* **Q12:** Soll die **Sense-HAT-Sensorik** (Temperatur/Lage) eine Rolle spielen oder nur Anzeige/Joystick?
* **Q13:** Welche **Streamingdienste** hast du (Spotify/YouTube Music/…), und gibt es lokale Musik (Format/Menge)?

### 16.13 Zusätzliche Quellen (Nachtrag, Websuche 2026-11-23)

pi-topPULSE / Sense HAT / UART
* pi-topPULSE (README: Pins, `pt-pulse`, nur Pi 3): https://github.com/pi-top/pi-topPULSE
* pinout.xyz pi-topPULSE: https://pinout.xyz/pinout/pi_toppulse
* Raspberry-Pi-Magazin – Pi-topPULSE-Test: https://magazine.raspberrypi.com/articles/pi-toppulse-review
* Bluetooth/UART auf dem Pi 3 (Mini-UART, `pi3-miniuart-bt`, `disable-bt`): https://github.com/bipropellant/bipropellant-hoverboard-firmware/wiki/Using-Raspberry-Pi-3-GPIO-UART ,
  https://docs.bitscope.com/pi-serial/ , https://forums.raspberrypi.com/viewtopic.php?t=261753
* Sense HAT (offizielle Doku): https://www.raspberrypi.com/documentation/accessories/sense-hat.html
* HATs stapeln / EEPROM-Konflikt (Forum): https://forums.raspberrypi.com/viewtopic.php?t=340069 ,
  https://forums.raspberrypi.com/viewtopic.php?t=177544 , https://forums.raspberrypi.com/viewtopic.php?t=372508

Hue
* Philips Hue – End-of-Support-Policy: https://www.philips-hue.com/en-au/support/legal/end-of-support-policy
* v1-Support-Ende (Presse): https://www.imore.com/philips-hue-bridge-v1-support-ending-april-2020 ,
  https://gizmodo.com/philips-hue-is-dropping-support-for-v1-bridge-heres-wh-1842156601
* Unterschied v1/v2 (Matter/HomeKit): https://www.homeowner.com/smart-home/difference-between-philips-hue-bridge-1-and-2/
* openHAB Hue-Binding (v1 wird langfristig entfallen): https://www.openhab.org/addons/bindings/hue
* homebridge-hue (v1 + v2): https://github.com/ebaauw/homebridge-hue/wiki/Getting-Started
* Google Home: lokale Matter-Steuerung für Hue (Okt. 2026): https://9to5google.com/2026/10/14/google-home-app-gets-local-matter-control-over-philips-hue-lights/
* Hue und Matter (Philips): https://www.philips-hue.com/id-id/support/article/philips-hue-and-matter-complete-setup-and-support-guide/000012

Matter
* Matter erklärt (Controller/Bridge/Border Router): https://www.howtogeek.com/838377/matter-explained-controllers-bridges-border-routers-and-more/
* Google: unterstützte Matter-Geräte/Hubs: https://developers.home.google.com/matter/supported-devices
* Google Home – Matter-Geräte einrichten: https://support.google.com/googlehome/answer/13127223
* Matter Device Library 1.2 (Generic Switch): https://csa-iot.org/wp-content/uploads/2023/10/Matter-1.2-Device-Library-Specification.pdf
* esp-matter Generic-Switch-Beispiel: https://github.com/espressif/esp-matter/blob/main/examples/generic_switch/README.md
* HA Matter-Integration / Matter Server: https://www.home-assistant.io/integrations/matter/ ,
  https://www.home-assistant.io/blog/2026/06/23/the-matter-upgrade-youve-been-waiting-for/
* `python-matter-server` (eingestellt, Nachfolger matter.js): https://github.com/matter-js/python-matter-server
* Matter-Hub-Fork (HA → Matter): https://github.com/RiDDiX/home-assistant-matter-hub

Home Assistant / Hardware
* Welche Hardware für HA: https://www.home-assistant.io/faq/what-hardware-do-i-need/
* HA auf Raspberry Pi: https://home-assistant.io/docs/installation/raspberry-pi/

Musik/Last
* MPD-Streaming (Last-Hinweis FLAC): https://archlinuxarm.org/forum/viewtopic.php?p=25157
* Audio-Transcoding mit dem Pi (Last-Hinweise): https://medium.com/@nowhereman77/audio-transcoding-with-a-raspberry-pi-b1d75c5848c5 ,
  https://lists.xiph.org/pipermail/icecast/2019-August/014750.html , https://old.tvheadend.org/boards/5/topics/35797

### 16.14 Nicht verifiziert (Ergänzung zu §14)

* Ob `pt-pulse` unter aktuellem Raspberry Pi OS (Bookworm/Trixie) installiert und läuft; welcher UART-Weg für den Pulse **nachweislich** funktioniert.
* Exakte **Sense-HAT-Pinbelegung/I²C-Adressen**; ob Sense HAT + Pulse mit Verlängerung gleichzeitig nutzbar sind.
* Ob die **v1-Hue-API Stand heute** noch aktiv ist; Details der v1-Szenen-Aktivierung (nur Vorwissen).
* Preis und Umzugsprozess der **Hue Bridge v2**.
* Ob **Google Home** Matter-Generic-Switch-Ereignisse als Automations-Auslöser verarbeitet.
* Audioformate, die Nest Hub/Speaker per Cast **direkt** abspielen (zur Vermeidung von Transcoding).
* Klangqualität/Lautstärke des Pulse-Lautsprechers (kein Test).


---
---

# NACHTRAG 2 (2026-11-23) – Deine Hue Bridge ist eine v2 (BSB002), nicht v1

**Dieser Nachtrag korrigiert §6.1, §6.2, §16.6, §16.7, §16.10 und §16.11.** Wo diese Abschnitte „Hue v1“ voraussetzen, **gilt jetzt dieser Nachtrag**.
Labels wie oben: **[belegt]** (Philips-/Google-Seite oder offizielle Doku), **[Community]**, **[Annahme]**.

## 17. Hue Bridge v2 (BSB002) – was ich genau herausgefunden habe

### 17.1 Identifikation deiner Bridge
* **BSB002 = Bridge der 2. Generation („v2“), quadratisch mit abgerundeten Ecken, weiss** [belegt: Modell-ID-Liste des Indigo-Plugins: BSB001 = rund/v1, BSB002 = quadratisch/v2, BSB003 = Bridge Pro].
  Damit ist die **frühere Annahme „v1 (rund)“ falsch** – gut für das Projekt.
* **„Version 2.1“:** Diese Bezeichnung habe ich **nicht belegt gefunden** (keine Treffer zu einer „Hardware-Revision 2.1“). Philips nennt das Gerät „Hue Bridge v2“ bzw. „Bridge (2nd gen)“ [belegt].
  Ob „2.1“ von einem Aufkleber oder aus einer App-Anzeige stammt, kann ich nicht klären → **nicht relevant für die Software**, entscheidend sind **Modell-ID BSB002** und die **Firmware**.
* **„Keine V3, die neueste“:** Die neueste Bridge ist die **Hue Bridge Pro (BSB003, 2025)**, **nicht** „v3“ genannt [belegt]. Unterschiede zur BSB002: mehr als 150 Lampen/50 Zubehör (BSB002: **50 Lampen, 12 Zubehör**; eine US-Datenblattangabe nennt 10 Zubehör),
  schnellerer Prozessor, mehr Speicher, **Wi-Fi zusätzlich zu Ethernet**, USB-C-Netzteil, **MotionAware** [belegt/Community: Coolblue, Hueblog, Philips]. **Für OctaPiHub bringt die Pro nichts Notwendiges**: dieselbe lokale API-Familie.

### 17.2 Deine Firmware **6.5.1978688000**
* **Das ist die neueste Bridge-v2-Firmware (Veröffentlichung 12.11.2026)** [belegt: Philips-Release-Notes, abgerufen am 2026-11-23]. Philips schreibt, sie werde „in den nächsten Tagen“ ausgerollt. → **Deine Bridge ist aktuell.**
* **Inhalt von 6.5.1978688000** [belegt, Release-Notes]: Unterstützung für **1-/2-Kanal-An/Aus-Module** (zusätzliche Gerätemodi); Fehlerkorrektur: **Zeitpläne/Automationen konnten nach Änderungen der Sommerzeitregeln zur falschen Zeit laufen**;
  „kleine Änderungen im Hintergrund“. (Der Text sagt an einer Stelle „Bridge Pro“, steht aber auf der Bridge-v2-Seite – offenbar ein Textfehler.)
* **Versionsschema:** Am **24.09.2026** wurde die Bridge-Version auf **„6.1“ umbenannt** („für ein einheitlicheres Erlebnis mit der App und zur Unterstützung kommender Funktionen“) [belegt]. Davor liefen Nummern wie 1.78.xxx; die **lange Zahl (1978688000)** ist der **Build-Zähler**.
  Die Bridge Pro hat **eigene Nummern** (z. B. 6.5.2071602000) [belegt] – nicht verwechseln.
* **Frühere Stationen:** 6.3.1978545030 (22.10.2026), 6.1.1978418030 (24.09.2026), 1.78.1978293000 (27.08.2026, „bereitet neue Produkte vor“), 1978074000 (02.07.2026, neue Produkte: Wired Wall Switches/Modules, Play-Lampen, Candle-Bulbs), 1977138000 (04.06.2026), 1977090000 (07.05.2026) [belegt].
* **Wichtig für Entwickler:** Die 2026er Release-Notes der v2 enthalten **keine API-, Matter- oder Sicherheitsangaben** [belegt]. Ältere Einträge:
  * **22.05.2025:** „Selbstsignierte Zertifikate wurden durch **richtige HTTPS-Zertifikate** ersetzt“ [belegt] → **betrifft jeden lokalen Client**, siehe §17.4.
  * **14.09.2023:** Bridge „unterstützt Matter“; 11.07.2024: Matter-Interoperabilitätsfix; 05.11.2025: Matter-Pairing-Fehler durch Drittgeräte behoben [belegt].
* **Supportzusage:** Philips gibt beiden aktuellen Bridges **mindestens 36 Monate Support ab Kauf**; für die BSB002 ist **kein Support-Ende angekündigt**, sie bekommt weiter Firmware [belegt/Community: Hueblog, MakeUseOf]. (Spekulation zum Ende um 2030: nur Meinung, **nicht belegt**.)
  Die Hue-Dienste unterstützen laut Quellen nur die **drei neuesten Software-Versionen** – also Auto-Update an lassen [Community].

### 17.3 Technische Eckdaten der BSB002 [belegt/Community]
| Eigenschaft | Wert |
|---|---|
| Funk | **Zigbee** (Hue-Lampen/Zubehör), Anschluss ans Netz per **Ethernet** (nicht WLAN) |
| Kapazität | **50 Lampen**, **12 Zubehör** (Datenblatt-Abweichung: 10) |
| Matter | **ja**, Bridge fungiert als **Matter-Bridge** (seit 09/2023); Matter 1.4.1 laut Hueblog-Zusammenfassung [Community] |
| Apple HomeKit | ja |
| Lokale API | **CLIP v1 und CLIP v2** (HTTPS) |
| Ethernet-Geschwindigkeit, CPU, RAM | in den Quellen **nicht** genannt |
| Neue Lampen-Funktion (Okt. 2026) | **Zigbee + Matter-über-Thread gleichzeitig** – das ist **Lampen-Firmware** (1.164.2), nicht die Bridge [Community: Hueblog] |

### 17.4 Konsequenzen für die lokale API (CLIP v2) – **jetzt voll nutzbar**
* **CLIP v2** (`https://<bridge-ip>/clip/v2/...`) läuft auf der BSB002. **Anmeldung:** Taste an der Bridge drücken, `POST /api` mit `{"devicetype":"octapihub#pi","generateclientkey":true}`; liefert einen **Application Key**, der künftig im Header **`hue-application-key`** mitgeht [Community: openHAB, node-hue/Postman].
  Ein alter v1-„Username“ funktioniert auch als v2-Key [Community].
* **Ereignisstrom (SSE):** `GET https://<ip>/eventstream/clip/v2` mit `Accept: text/event-stream` – liefert Änderungen als Ereignisse (add/update/delete/error); **kein Polling nötig** [Community/belegt-ähnlich]. → für **„aktuelle Lampenfarbe/Szene auf der Matrix anzeigen“** ideal.
* **Szenen auslösen (v2):** `PUT /clip/v2/resource/scene/<id>` mit `{"recall":{"action":"active"}}` [Annahme aus Vorwissen, **in P0 prüfen**]. Szenen/Räume lassen sich per `GET /clip/v2/resource/scene` bzw. `/room` auflisten.
* **Ratenbegrenzung:** Richtwerte: **~10 Befehle/s an Lampen, ≤ 1/s an Gruppen** (alte v1-Empfehlung von Philips-Entwicklern); für v2 nennt ein openHAB-Beitrag **~20 Anfragen/s**; weitere Quellen nennen 2/s als „sicher“ [Community, **widersprüchlich**].
  → Der Dienst braucht eine **Warteschlange/Drossel** (OctaLedger hat die Outbox-Idee dafür). **Szenenaufruf ist 1 Anfrage** und damit unkritisch; Dimm-Schleifen (Lautstärke-ähnliche Helligkeit) müssen gedrosselt werden.
* **Entertainment-API** (Streaming von Lichteffekten per DTLS, PSK aus `generateclientkey`) – nicht für Szenen nötig [Community].
* **Zertifikat – wichtige Neuerung:** Seit **Mai 2025** besitzt die Bridge ein **von Signify ausgestelltes HTTPS-Zertifikat statt selbstsigniert** [belegt]. Folgen:
  * Der **OctaLedger-Hue-Adapter** pinnt per **TOFU den SHA-256-Fingerabdruck** des Bridge-Zertifikats (laut seinem Modul-Docstring) [belegt, interner Quelltext]. **Wird das Bridge-Zertifikat erneuert/rotiert, bricht ein Fingerabdruck-Pin** [Annahme: Laufzeit und Erneuerungsrhythmus der Signify-Zertifikate **nicht recherchiert**].
    → **Für OctaPiHub besser:** gegen die **Signify-CA** validieren (statt Fingerabdruck), mit Fallback „neu pinnen nach Nutzerbestätigung“. **Vorher prüfen**, welches Zertifikat/welche CA deine Bridge heute ausliefert (`openssl s_client -connect <ip>:443`).
  * Der Zugriff per **IP-Adresse** passt nicht zu einem Zertifikat, das auf eine Bridge-ID/Domain ausgestellt ist [Annahme] → Hostname-Prüfung bewusst behandeln.
* **v1-API:** Auf der BSB002 weiter vorhanden (HTTP). Signify kündigt (ältere Entwicklerseite) an, **v1 zu sperren, sobald alle Bridges Signify-Zertifikate haben** und dass **v2 kein HTTP unterstützt** [Community/älter, **kein Datum**]. → **Nur v2 verwenden.** Neue Funktionen (z. B. dynamische Szenen) gibt es **nur in v2** [belegt].
* **Fernzugriff:** **Hue-Konto/Remote-API (OAuth)** funktioniert mit der v2 [belegt]; **Tailscale** zum Heimnetz ist die datenärmere Alternative.
* **Google Home:** Mit der v2 verbindet Google Home die Bridge **lokal per Matter** (Update Oktober 2026), Nest Hub (2. Gen)/Hub Max sind Matter-Controller [belegt, 9to5Google/Philips-Supportseite].
  Die Bridge kann also **gleichzeitig** von OctaPiHub (CLIP v2) und Google Home (Matter) genutzt werden [Annahme: Parallelbetrieb – kein Konflikt dokumentiert].

### 17.5 Korrigierte Empfehlung Licht (ersetzt §16.6/§16.10 Punkt 3)
1. **Hue v2 / CLIP v2 direkt** (Application Key, Event-Stream, Szenen-Recall). **Kein v1-Adapter nötig. Kein Bridge-Kauf nötig.**
2. **Vorhandener OctaLedger-Hue-Adapter ist grundsätzlich wiederverwendbar** (CLIP v2) – aber: **kein Farbe, kein Event-Stream, TOFU-Pinning überdenken** (§17.4), **nicht gegen echte Bridge getestet** [belegt, Docstring].
3. **Matter für Licht ist nicht nötig**; Google Home sieht die Lampen selbst. OctaPiHub bleibt bei der **direkten API** (mehr Funktionen: Szenen, dynamische Szenen, Räume).
4. **Mittelfristig ist die Bridge Pro nicht nötig** (nur bei > 50 Lampen/Bedarf an MotionAware).
5. **Spike P0 (Schritt 3) ändert sich:** statt „v1-Erreichbarkeit“ → (a) `GET https://<ip>/clip/v2/resource/bridge` mit Key, (b) Szene aktivieren, (c) SSE-Stream 10 min mitschneiden, (d) Zertifikat prüfen.

### 17.6 Welche früheren Aussagen jetzt **falsch/überholt** sind
| Stelle | Alt | Neu |
|---|---|---|
| §6.1, §13 Q3 | „Bridge v1 wird von HA nicht mehr unterstützt, v2 vorausgesetzt“ | **Passt jetzt** (BSB002) |
| §16.6 | v1 ohne Support, ohne Cloud/Google Home/Matter, v1-Adapter schreiben | **Gegenstandslos**: v2 hat Cloud, Matter, Google-Home-Anbindung, CLIP v2 |
| §16.6 Tabelle „Bridge v2 kaufen“ | Empfehlung mittelfristig | **Nicht nötig** |
| §16.7 Punkt 3 | „Hue v2 → Matter → Google Home läuft mit deiner v1 nicht“ | **Läuft mit deiner v2** |
| §16.10 Punkt 3 | zuerst v1-Adapter | **CLIP v2 direkt** |
| §16.11 Schritt 3 | v1-Test | **v2-Test** (§17.5 Punkt 5) |
| §16.12 Q10 | v2 kaufen? | **Entfällt** |
| §12 Risikotabelle | Hue-Risiken v1 | neu: **Zertifikatsrotation/Pinning**, **Ratenlimit**, **Key-Verwaltung** |

### 17.7 Neue offene Fragen
* **Q14:** Wie kommst du auf „2.1“ (Aufkleber, App-Anzeige)? Kannst du den **Hue-Firmware-Hinweis** in der App (Einstellungen → Bridge) bestätigen? (nur zur Kontrolle, nicht kritisch)
* **Q15:** Wie viele Lampen/Räume/Szenen hast du? (≤ 50 Lampen ist für die BSB002 ok)
* **Q16:** Soll die **Hue-Cloud/Remote-API** genutzt werden (Hue-Konto-Zugang) oder lieber **Tailscale**?

### 17.8 Zusätzliche Quellen (Nachtrag 2, Abruf 2026-11-23)
* Philips Hue – Release Notes Hue Bridge (v2): https://www.philips-hue.com/en-us/support/release-notes/bridge
* Philips Hue – Release Notes Bridge Pro: https://www.philips-hue.com/en-us/support/release-notes/bridge-pro
* Hueblog – Bridge/Bridge-Pro-Firmware: https://hueblog.com/2026/01/16/new-firmware-update-for-hue-bridge-and-hue-bridge-pro/ , https://hueblog.com/2026/06/04/new-firmware-update-for-the-hue-bridge-and-hue-bridge-pro/
* Hueblog – Zigbee + Matter gleichzeitig (Lampen-Firmware): https://hueblog.com/2026/06/23/hue-bulbs-will-soon-connect-simultaneously-via-thread-and-zigbee/
* Hueblog – Bridge Pro FAQ: https://hueblog.com/2025/09/04/hue-bridge-pro-almost-all-the-answers-to-the-most-important-questions/
* Modell-IDs BSB001/002/003 (Indigo-Plugin): https://github.com/kw123/Hue-Lights-Indigo-plugin
* Bridge vs. Bridge Pro: https://www.coolblue.be/en/advice/compare-the-philips-hue-bridge-pro-to-the-bridge.html , https://synced.com.au/learn/ecosystems/philips-hue/bridge-vs-bridge-pro
* Bridge-Datenblatt (US): https://www.assets.lighting.philips.com/is/content/PhilipsLighting/046677458478-pss-en_us
* Supportzeitraum/Retirement-Einordnung: https://www.makeuseof.com/will-philips-retire-second-gen-hue-bridge/
* Signify Developer – „New Hue API“ (v2 kein HTTP, v1 wird gesperrt): https://developers.meethue.com/new-hue-api/
* CLIP v2 (Auth, Eventstream): https://www.openhab.org/addons/bindings/hue/doc/readme_v2.html , https://github.com/rodney42/node-hue ,
  https://community.openhab.org/t/philips-hue-clip-2-api-v2-discussion-thread/142111
* Rate-Limit-Hinweise: https://developers.meethue.com/?p=497 , https://community.home-assistant.io/t/philips-hue-bridge-api-requests-throttling/106888
* Entertainment-API-Überblick: https://iotech.blog/posts/philips-hue-entertainment-api/
* Google Home lokal über Matter für Hue (Okt. 2026): https://9to5google.com/2026/10/14/google-home-app-gets-local-matter-control-over-philips-hue-lights/

### 17.9 Nicht verifiziert (Ergänzung zu §14/§16.14)
* Die Bezeichnung „2.1“; Prozessor/RAM/Ethernet-Tempo der BSB002.
* **Zertifikatsdetails** (Aussteller, Laufzeit, Hostname, Rotation) der Signify-Zertifikate seit Mai 2025.
* **Exakte Szenen-Recall-Anfrage** der CLIP v2 (nur Vorwissen) und Details der **offiziellen** Ratenlimits (nur Community-Zahlen).
* Ob/wann die **v1-API** auf der BSB002 tatsächlich gesperrt wird (nur ältere Ankündigung ohne Datum).
* Parallelbetrieb OctaPiHub (CLIP v2) + Google Home (Matter) im Alltag.


---

# NACHTRAG 3 (24.11.2026) – Entscheidung für den Raspberry Pi 3 im Wohnzimmer: Betriebssystem, WLAN, GUI, Sense HAT, Würfel, Autostart

> **Eigenes Projekt, nicht Teil von OctaLedger.** Dieser Nachtrag gilt zusammen mit den beiden neuen Dateien im selben Ordner:
> `PFLICHTENHEFT_OctaPiHub-Raspberry-Pi.md` (Soll-Anforderungen) und `HANDBUCH_OctaPiHub-Raspberry-Pi.md` (Schritt-für-Schritt-Vorgehen).
> Wo dieser Nachtrag früheren Aussagen widerspricht, gilt dieser Nachtrag. Nicht Überprüftes ist in §18.11 gesammelt.

## 18.1 Entscheidungen im Überblick

| Frage | Empfehlung | Kurzbegründung |
|---|---|---|
| Hardware | **Raspberry Pi 3** (Pi 4 ist anderweitig verbaut) | Reicht für Würfel lesen, Hue, Cast, MQTT, Sense HAT, Weboberfläche. Engpass ist der Arbeitsspeicher (1 GB), nicht die Rechenleistung. |
| Betriebssystem | **Raspberry Pi OS Lite, 64-bit** (Debian 13 „Trixie“-Basis) | Offizielles System, beste Treiber-/Sense-HAT-Unterstützung, kein Desktop = mehr freier RAM. |
| Netzwerk | **WLAN** (wie gewünscht), feste IP per Router-Reservierung | Funktioniert; Besonderheiten siehe §18.3 (Bluetooth-Koexistenz, Energiesparmodus, mDNS). |
| Bluetooth | **USB-Bluetooth-Dongle (BLE 4.x/5.x)** statt Onboard-Chip, falls WLAN und Würfel zusammen Probleme machen | Auf dem Pi 3 teilen sich WLAN und Bluetooth einen Funkchip und die Antenne. Der Dongle ist die billigste Stabilitätsversicherung. |
| GUI | **Kein Desktop.** Weboberfläche auf dem Pi + Sense HAT als lokales Display + Kommandozeile | Kein Monitor nötig, von Handy/PC bedienbar, wenig RAM. |
| Web-Framework | **FastAPI + Jinja2 + HTMX + Server-Sent-Events** (kein Streamlit im Dauerbetrieb) | Streamlit rendert das Skript pro Sitzung neu und braucht pro Sitzung viel RAM; auf 1 GB riskant. Streamlit bleibt für Experimente erlaubt, siehe §18.4. |
| Würfel | Ein Prozess besitzt beide BLE-Verbindungen | Ein Würfel verbindet sich nur mit einem Client gleichzeitig (Annahme, am Gerät zu testen). |
| OctaLedger-Teile | Nur Protokoll, Agent-Kern, Outbox/Regeln, Hue/MQTT-Adapter, Fehler/Logging | Zeiterfassung (Einträge, Journal, Statistik, Berichte, CSV, Projekte) wird **nicht** gebraucht. |
| Autostart | **systemd** (`Type=notify`, `WatchdogSec`, `Restart=always`) + Hardware-Watchdog + Health-Endpunkt | Standardweg unter Linux; startet beim Boot, überwacht und startet bei Hängern neu. |

## 18.2 Betriebssystem im Detail

**Kandidaten**

| System | Eignung | Anmerkung |
|---|---|---|
| **Raspberry Pi OS Lite (64-bit)** | **Empfohlen** | Offiziell, aktuelle Basis Debian 13 „Trixie“, Raspberry-Pi-Kernel mit Sense-HAT-/I2C-/Framebuffer-Treibern, `raspi-config`, Imager mit Vorab-Konfiguration (Hostname, WLAN, SSH-Schlüssel). |
| Raspberry Pi OS Lite (32-bit) | Möglich | Etwas weniger RAM-Verbrauch; ältere Python-Pakete/Wheels meist über piwheels. Für neue Projekte ist 64-bit die Standardwahl; der Pi 3 kann 64-bit. |
| Raspberry Pi OS mit Desktop | Nicht empfohlen | Frisst RAM, wird nicht gebraucht. Wer doch einmal einen Desktop will: Raspberry Pi Connect oder nachträglich installieren. |
| DietPi | Alternative | Sehr schlank, aber eigene Konfigurationswerkzeuge; Sense-HAT/PULSE-Doku weniger verbreitet. |
| Ubuntu Server | Alternative | Läuft, Sense HAT dokumentiert (Ubuntu-Blog), aber weniger Pi-spezifische Pflege. |
| Home Assistant OS | **Abgelehnt** | Ersetzt das ganze System, Pi 3 ist dafür knapp, eigene Software läge nur als Add-on vor. Passt nicht zur Variante D. |
| Alte Raspbian-/Buster-Images | Nein | Veraltet, Bluetooth-Stack zu alt für Bleak. |

**Wichtige Systemfakten (Stand der Recherche)**
- Raspberry Pi OS basiert auf Debian 13 „Trixie“; systemd wechselt laut Debian-Release-Notes von 252 (Debian 12) auf 257 (Debian 13). Die Debian-Version bringt auch eine neuere BlueZ-Version mit; Bleak braucht BlueZ ≥ 5.55. **Die genaue BlueZ-Version auf Trixie wurde nicht gefunden – im Handbuch wird sie per Befehl geprüft.**
- WLAN wird seit Bookworm über **NetworkManager** verwaltet (`nmcli`), nicht mehr über `wpa_supplicant.conf`/`dhcpcd`.
- Konfigurationsdateien liegen unter `/boot/firmware/` (`config.txt`, `cmdline.txt`).
- Python in Trixie ist „externally managed“: Pakete nur in einer virtuellen Umgebung (`venv`) installieren, nicht systemweit mit `pip`.
- Hinweis zur Datumsangabe: Eine Suche lieferte widersprüchliche Veröffentlichungsdaten der Trixie-Images (Oktober 2025 vs. 2026-Einträge auf der Download-Seite). Immer die aktuelle Version auf raspberrypi.com/software prüfen.

**Empfehlung zu Datenträger und Strom**
- Die **SD-Karte** ist der häufigste Ausfallgrund bei Dauerbetrieb. Markenkarte (A1/A2, mindestens 16 GB, besser „High Endurance“), wenig schreiben (Logs begrenzen, SQLite im WAL-Modus, Schreibintervalle drosseln).
- Option später: Start von USB-Stick/SSD. Pi 3B+ bootet von USB; beim Pi 3B ist dafür ein einmaliges Freischalten nötig (**genaues Vorgehen nicht verifiziert – vor dem Einsatz in der offiziellen Doku nachlesen**).
- **Netzteil:** gutes 5-V-Netzteil mit mindestens 2,5 A. Unterspannung führt zu Instabilität (Funk, USB-Dongle, LED-Matrix). Kontrolle per `vcgencmd get_throttled` (Wert `0x0` = ok).
- Ein sauberes Herunterfahren ist im Alltag nicht vorgesehen (Stecker ziehen). Darum: Dateisystem robust halten (`ext4` mit Journal, keine großen Schreiblasten, optional später Overlay-Dateisystem mit getrennter Datenpartition).

## 18.3 WLAN im Heimnetz (Pi 3 im Wohnzimmer)

**Antwort auf „brauche ich überhaupt WLAN?“ aus Nachtrag 1, jetzt konkret:** Der Pi braucht eine Netzwerkverbindung für Hue (lokales HTTPS zur Bridge), Google Cast (lokal, Pi → Nest), MQTT/Weboberfläche (lokal) und Streaming/Internet. WLAN reicht dafür. Ethernet wäre stabiler, ist im Wohnzimmer aber oft unpraktisch – daher WLAN mit Gegenmaßnahmen.

**Besonderheiten des Pi 3**
1. **Gemeinsamer Funkchip:** WLAN (2,4 GHz, nur b/g/n – **kein 5 GHz** beim Pi 3B; das Pi 3B+ kann 5 GHz) und Bluetooth teilen sich Chip und Antenne. Gleichzeitiges Streamen/Cast + BLE-Würfel kann zu Aussetzern führen. Gegenmaßnahme: **USB-Bluetooth-Dongle** als `hci1` für die Würfel, Onboard-Bluetooth abschalten (`dtoverlay=disable-bt`) oder ignorieren. Alternativ USB-WLAN-Dongle für das Netz und Onboard-BLE für die Würfel – eine der beiden Funkarten auslagern genügt.
2. **WLAN-Energiesparmodus** abschalten (verursacht Latenz und verzögerte Pakete): `nmcli connection modify <Name> 802-11-wireless.powersave 2`.
3. **Feste IP:** im Router DHCP-Reservierung für den Pi, damit Hostname/IP stabil bleiben (Weboberfläche, MQTT, Lesezeichen).
4. **mDNS/Multicast:** Google Cast findet Geräte per mDNS. Manche Router/Mesh-Systeme haben „Client-/AP-Isolation“ oder filtern Multicast – dann Geräte nicht finden. Gegenmaßnahme: Cast-Geräte per fester IP statt Suche ansprechen, im Router Isolation aus, Pi und Nest im selben Netz/VLAN.
5. **Hue-Bridge** hängt per Kabel am Router; Zugriff ist rein lokal per IP (`https://<bridge-ip>`). Feste IP für die Bridge reservieren.
6. **Reichweite BLE ↔ Würfel:** Das ist ein anderes Thema als WLAN: Der Würfel ist ein BLE-Gerät mit ca. 10 m Reichweite im Freien, im Wohnraum durch Wände weniger. Pi im selben Raum wie die Würfel, Dongle an einem USB-Verlängerungskabel, **weg vom Netzteil und von Metall**, USB-3-Geräten (stören 2,4 GHz) und der WLAN-Antenne.
7. **Länder-/Regulatorik:** WLAN-Ländercode setzen (Schweiz = `CH`), sonst bleibt der Funk gesperrt. Der Imager erledigt das bei der Vorkonfiguration.

**WLAN-Fehlerbehandlung (Soll):** Verliert der Pi das WLAN, muss er sich selbst erholen (NetworkManager verbindet neu). Die Software darf dabei nicht abstürzen: Hue/Cast/MQTT-Aktionen gehen in die Warteschlange (Outbox) und werden nach der Wiederverbindung nachgeholt oder nach Ablaufzeit verworfen. Ein Netzwerk-Watchdog startet bei dauerhaftem Ausfall (z. B. > 10 min) `NetworkManager` neu und zuletzt den Pi.

## 18.4 Braucht die Software ein GUI? Streamlit oder Webseite?

**Kurzantwort:** Kein Desktop-GUI, aber eine **kleine Weboberfläche** auf dem Pi ist sehr sinnvoll. Der Pi hat keinen Bildschirm; bedient wird per Handy/PC-Browser (`http://octapihub.local:8080`), per Kommandozeile und per Sense-HAT-Joystick.

**Was die GUI können muss (Auswahl):** Zustand der Würfel (verbunden, Seite, Batterie, Signalstärke), Zuordnung Seite → Aktion (Lichtszene, Musik), Hue-Kopplung (Knopfdruck-Anleitung), Cast-Geräte wählen, Sensorwerte (Sense HAT) live/Verlauf, Sense-HAT-Anzeige konfigurieren, Logs/Fehler, Systemstatus (CPU-Temperatur, RAM, Spannung, Uptime), Neustart-Knöpfe.

**Vergleich der Optionen**

| Option | RAM/CPU auf Pi 3 | Aufwand | Echtzeit | Fazit |
|---|---|---|---|---|
| **Streamlit** (wie OctaLedger) | Hoch: Skript läuft bei jeder Interaktion neu, pro Browser-Sitzung eigener Zustand. In Foren werden ~300 MB Speicher für *eine* Sitzung mit kleinem Datensatz genannt (kein Pi-Messwert). | Gering (bekannt, Code wiederverwendbar) | Polling/Rerun | Für Entwicklung/Experimente OK, **für den Dauerbetrieb auf 1 GB nicht empfohlen**. |
| **FastAPI + Jinja2 + HTMX + SSE** | Niedrig (ein Python-Prozess, Seiten serverseitig gerendert, kaum JavaScript) | Mittel | Gut (Server-Sent-Events für Live-Werte) | **Empfohlen.** Kein Build-Schritt, keine Node-Toolchain, API und Seiten im selben Prozess. |
| FastAPI + fertiges JS-Frontend (React/Vue) | Niedrig auf dem Pi, aber Build-Toolchain nötig | Hoch | Sehr gut | Übertrieben für das Vorhaben. |
| NiceGUI / Flet / Reflex | Mittel; eigener Webserver, Python-only | Mittel | Gut | Interessant, aber zusätzliche Abhängigkeit; ungetestet auf Pi 3 (Annahme). |
| Home-Assistant-Dashboard | – | Erfordert HA | – | Nur wenn HA ohnehin läuft. |
| Reine Konsole/TUI (`textual`, `rich`) | Sehr niedrig | Mittel | Gut | Gut als Ergänzung für SSH-Wartung. |
| Nur Sense HAT | Minimal | Gering | – | Zu wenig zur Konfiguration, aber ideal als Statusanzeige. |

**Empfehlung:** Variante „Dienst + Weboberfläche“: Der Hintergrunddienst (`octapihub`) ist die Wahrheit; die Weboberfläche ist nur ein Fenster darauf (gleicher Prozess oder eigener schlanker Prozess über lokale API). Fällt die Oberfläche aus, laufen Würfel und Szenen weiter. Dazu ein CLI `octapihubctl` (`status`, `scenes`, `logs`, `pair-hue`, `restart`).

**Antwort auf „vielleicht doch Streamlit?“:** Ja, wenn dir die Wiederverwendung des OctaLedger-Dashboards wichtiger ist als RAM. Dann: nur *ein* Browser offen, `st.fragment`/lange Polling-Intervalle, kein Cache großer Daten, Speicher des Prozesses messen (`ps -o rss`), Swap vergrößern (`dphys-swapfile`/zram). **Nicht gemessen**: Ob Streamlit auf dem Pi 3 dauerhaft stabil bleibt, ist unbekannt; in Phase 0 des Pflichtenhefts als Messaufgabe vorgesehen. Das Raspberry-Pi-Magazin zeigt außerdem, dass allein der Verzicht auf den Desktop auf einem 1-GB-Pi ca. 700 MB RAM freihält (Messung am Pi 5 mit 1 GB).

## 18.5 Sense HAT vollständig nutzen

**Hardware (Sense HAT v1; v2 siehe unten)**
- 8×8-RGB-LED-Matrix (über Framebuffer `RPi-Sense FB`, Python `set_pixel(s)`, `show_message`, `show_letter`, `set_rotation`, `low_light`).
- Sensoren: Temperatur und Luftfeuchtigkeit (HTS221), Luftdruck und Temperatur (LPS25H), IMU (LSM9DS1: Gyroskop, Beschleunigung, Magnetometer; Lage/Orientierung berechnet über RTIMULib).
- 5-Wege-Joystick (als Eingabegerät unter `/dev/input`, in Python `stick.get_events()` bzw. Callbacks).
- **Sense HAT v2** (seit 2023): zusätzlich Farb-/Helligkeitssensor (TCS34725). **Ich weiß nicht, welche Version du hast – bitte Platine prüfen/`sense.colour` testen.** Mit v2 lässt sich die LED-Helligkeit automatisch an das Raumlicht anpassen (sehr passend fürs Wohnzimmer).

**Installation:** `sudo apt install sense-hat` (laut Raspberry-Pi-Doku aktualisiert das Paket Kernel-Abhängigkeiten, aktiviert I2C und installiert Abhängigkeiten), danach Neustart. In einer venv mit `--system-site-packages` nutzbar, damit das apt-Paket `python3-sense-hat` gefunden wird.

**Was für den Hub nutzbar und sinnvoll ist**

| Funktion | Zweck im Hub | Hinweis |
|---|---|---|
| Temperatur, Luftfeuchtigkeit, Druck | Raumklima im Dashboard, Verlauf, MQTT/Home Assistant | **Temperatur wird durch die CPU-Wärme zu hoch gemessen** (Platine sitzt direkt über dem Pi). Korrektur anhand der CPU-Temperatur (`vcgencmd measure_temp`) oder Sense HAT per Verlängerungskabel (GPIO-Riser) abheben. Vorher/Nachher mit Referenzthermometer vergleichen. |
| Orientierung/Beschleunigung | Erkennen, ob der Pi bewegt/gekippt wird; Spielerei | Keine Pflicht. |
| Magnetometer | Kompass | Braucht Kalibrierung; auf Pi 3 laut offizieller Doku kann das Kalibrierwerkzeug scheitern (Firmware übernimmt I2C0). |
| LED-Matrix | Statusanzeige (siehe unten) | Schreiben nur aus *einem* Thread/Task, nie im BLE-Callback. |
| Joystick | Lokale Bedienung: Helligkeit/Lautstärke, nächste/vorherige Szene, Seite wechseln | Als Ereignisquelle in die Regel-Engine, nicht fest verdrahtet. |
| Farb-/Helligkeitssensor (nur v2) | Automatische Matrix-Helligkeit | Nachts dunkel/aus. |

**Anzeigekonzept der 8×8-Matrix (Vorschlag)** – Mehrere „Ansichten“, wechselnd oder priorisiert:

| Ansicht | Darstellung | Priorität |
|---|---|---|
| Fehler/Warnung | Rotes „!“ oder X, blinkend; Code als Lauftext auf Anforderung | Höchste, überlagert alles |
| Lichtszene | Matrix in der Hauptfarbe der Szene (aus Hue-Szenenfarbe), Helligkeitsbalken in der untersten Zeile | Hoch (nach Würfel-Wechsel 3 s) |
| Würfelseite | Zahl 1–8 als 5×7-Ziffer oder Muster; Würfel 1 und 2 durch linke/rechte Hälfte | Hoch (kurz nach Seitenwechsel) |
| Würfelbatterie | Zwei Balken (je Würfel), grün/gelb/rot | Mittel, periodisch |
| Musik | Equalizer-Balken, Pause-/Play-Symbol, Lautstärke als Balken; Titel als Lauftext (`show_message`, blockierend → in eigenem Task mit Abbruch) | Mittel |
| Verbindungsstatus | Je ein Pixel für: WLAN, Hue, Cast, MQTT, Würfel 1, Würfel 2 (grün/gelb/rot) | Immer in Ecke/Randzeile |
| Uhr/Standby | Gedimmte Punktuhr oder aus (Nachtmodus) | Niedrig |

**Regeln:** Zeitplan „Nachtmodus“ (Helligkeit minimal oder aus), Entprellung (keine hektischen Wechsel), Lauftext nur auf Anfrage, Display-Thread fängt Fehler (I2C-Fehler dürfen den Hub nicht stoppen: Anzeige deaktivieren, weitermachen, Warnung loggen). **Stromverbrauch:** voller weißer Bildschirm zieht deutlich Strom; Standardhelligkeit niedrig (`low_light=True`).

**Konflikt mit dem pi-topPULSE:** Beide sind HATs auf demselben GPIO-Header (I2C, Pin-Belegung). Gleichzeitig würde Stapeln nur mit Verlängerung/Riser funktionieren und kollidiert an I2C/ID-EEPROM (siehe Nachtrag 1 §16.2/16.3). Empfehlung: **Phase 1 nur Sense HAT**; PULSE (Lautsprecher/Matrix) erst als Test nach Phase 1, nur wenn der Treiber auf Trixie läuft (**nicht verifiziert**).

## 18.6 Würfel (Timeular/ZEI) – nochmals zusammengefasst für den Hub

Nach aktuellem Wissensstand (Community-Referenzen und OctaLedger, **nicht hardwareverifiziert**):
- BLE-Gerät; Service `c7e70010-c847-11e6-8175-8c89a55d403c`, Orientierungs-Characteristic `c7e70012-c847-11e6-8175-8c89a55d403c`. Werte 1–8 = Seite; 0 (und je nach Firmware 10) = neutral/unbestimmt/auf der Seite liegend.
- Der Hub **liest nur** und abonniert Benachrichtigungen (Notify). Er schreibt nichts auf unbekannte Characteristics.
- Batteriestand: Aus dem Standard-GATT-Batteriedienst (0x180F) lesbar – **für diesen Würfel nicht verifiziert**; das Handbuch enthält einen Test (`bleak`-Dienstliste ausgeben).
- Ein Würfel ist typischerweise nur mit **einem** Client verbunden. Deshalb: OctaLedger auf dem PC und der Hub auf dem Pi dürfen nicht gleichzeitig denselben Würfel nutzen. Vor dem Wechsel Bluetooth am PC/Timeular-App trennen/stoppen (Annahme, zu testen).
- Zwei Würfel gleichzeitig am Pi: BlueZ kann mehrere LE-Verbindungen halten; das Verbinden **nacheinander** (nicht parallel) ist stabiler. Jeder Würfel bekommt einen eigenen Worker mit Wiederverbindungsschleife (Backoff 1 s → 60 s, Jitter), Wachhund gegen „angeblich verbunden, aber stumm“ (kein Event > x Minuten → Trennen/Neu verbinden), Neu-Abonnieren nach jedem Reconnect.
- Rolle der Würfel: **Würfel 1 = Licht** (Seite → Hue-Szene), **Würfel 2 = Musik** (Seite → Playlist/Aktion; neutral = Pause/nichts). Zuordnung **nicht fest codiert, sondern in einer Konfigurationsdatei**, per Weboberfläche bearbeitbar.
- Identifikation: Würfel per Bluetooth-Adresse + frei wählbarem Namen; Adressen werden in Logs maskiert.
- Gedächtnis: Letzter Zustand wird gespeichert, damit nach einem Neustart nicht blind die alte Szene erneut ausgelöst wird (konfigurierbar: „Szene beim Start wiederherstellen“ ja/nein).

## 18.7 Was von OctaLedger gebraucht wird – und was nicht

Du schreibst, die Zeiterfassung brauche die neue Software „ganz bestimmt keine“. Das ist richtig.

| OctaLedger-Baustein | Für OctaPiHub? | Anmerkung |
|---|---|---|
| BLE-Protokoll/Decodierung der Würfelseite | **Ja** | Kern, 1:1 übernehmbar (gleiches Muster: Notify → Ereignis) |
| Agent mit einem Asyncio-Loop und Worker je Gerät | **Ja** | Prinzip behalten; auf Linux/BlueZ anpassen (Adapter-Wahl `hci1`, Watchdog) |
| Outbox/Warteschlange, Schritte, Bedingungen (Regelwerk) | **Ja** | Für „Seite → Szene/Musik“ mit Wiederholung und Ablaufzeit |
| Hue-Adapter (CLIP v2) | **Ja, anpassen** | Zertifikats-Pinning prüfen (siehe Nachtrag 2 §17) |
| MQTT-/Webhook-Adapter | **Ja** | Für Sense-HAT-Daten und Home Assistant |
| Home-Assistant-/Spotify-Adapter | Optional | |
| Musikwiedergabe (miniaudio/ffmpeg) | **Nein/ersetzen** | Auf dem Pi stattdessen `mpd`/`mpv` + `pychromecast` |
| Fehlerhierarchie mit stabilen Codes, Logging mit Redaktion | **Ja** | Direkt übernehmen (Prinzip) |
| Konfiguration, Versionierung von Dateiformaten, Backup/Restore | Teilweise | Einfaches versioniertes YAML/TOML genügt |
| Zeiteinträge, Aktivitäten, Projekte, Journal, Statistik, Berichte, CSV-Export | **Nein** | Entfällt vollständig |
| Token-API (FastAPI) | Teilweise | Eigene schlanke API mit Token + LAN-Bindung |
| Streamlit-Dashboard | Nein (siehe §18.4) | Eigene Oberfläche |
| Windows-spezifisches (platformdirs, Windows-BLE-Eigenheiten) | Nein | Linux-Pfade (`/etc`, `/var/lib`), D-Bus/BlueZ |

Empfehlung: **Kopieren und gezielt anpassen, nicht als Abhängigkeit einbinden** (eigenes Projekt, eigenes Repository, eigene Lizenzentscheidung; vor Veröffentlichung Lizenz klären).

## 18.8 Autostart und Fehlerbehandlung („robust im Wohnzimmer“)

**Schichten der Ausfallsicherheit**

| Schicht | Maßnahme |
|---|---|
| 1. Code | Eigene Fehlerhierarchie mit stabilen Codes (`CH-BLE-001` …), jede Fremdbibliothek (Bleak, requests, mpd, I2C) an der Grenze abfangen und übersetzen; Tasks überwachen (kein stiller Tod), Timeouts an jedem Netzwerkaufruf, Wiederholung mit Backoff, Circuit-Breaker je Adapter (Hue weg ≠ Hub tot). |
| 2. Prozess | systemd-Unit: `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec=0`, `Type=notify`, `WatchdogSec=30` (Dienst meldet alle ≤15 s „lebe“ per `sd_notify`, nur wenn die Haupt-Schleife wirklich läuft). |
| 3. Abhängigkeiten | `After=network-online.target bluetooth.target`; Bluetooth-Dienst-Watchdog: bei dauerhaftem BLE-Fehler `bluetooth.service` neu starten, dann Adapter aus/ein (`bluetoothctl power off/on`), zuletzt Dienstneustart. |
| 4. System | Hardware-Watchdog des Pi (`RuntimeWatchdogSec`, kernelseitig `bcm2835_wdt`): bei Systemhänger automatischer Neustart. Auf Trixie kann eine Konfigurationsdatei unter `/usr/lib/systemd/system.conf.d/` die Eingabe in `/etc/systemd/system.conf` überschreiben (aus einem Forumsbeitrag; **nicht selbst geprüft**) – deshalb mit `systemctl show -p RuntimeWatchdogUSec` kontrollieren. |
| 5. Daten | SQLite (WAL) nur für Zustand/Verlauf, atomares Schreiben von Konfigurationen (Temp-Datei + `os.replace`), Konfiguration vor dem Laden validieren; bei kaputter Konfiguration letzte gute Version laden. |
| 6. Strom | Gutes Netzteil, Unterspannungsanzeige im Dashboard (`vcgencmd get_throttled`), nach Stromausfall startet der Pi von selbst (Standard) und der Dienst über systemd. |
| 7. Beobachtung | `journald` dauerhaft mit Größenlimit, Health-Endpunkt `/health` (Würfel, Hue, Cast, MQTT, Sense HAT, WLAN, Last), optional Benachrichtigung per MQTT/Push bei Dauerstörung, Diagnoseexport (ZIP ohne Geheimnisse). |
| 8. Updates | `unattended-upgrades` für Sicherheitsupdates, eigene App-Updates manuell per `git pull` + Neustart des Dienstes; Rollback = vorherige Version behalten. |

**Fehlerkategorien und Reaktion (Auszug)**

| Fehler | Reaktion |
|---|---|
| Würfel nicht erreichbar | Reconnect mit Backoff, Anzeige „Würfel 1 getrennt“ (Sense HAT/Dashboard), keine Aktion auslösen |
| Würfel verbunden, aber stumm | Watchdog trennt und verbindet neu |
| Hue-Bridge nicht erreichbar / 429 / Zertifikatsfehler | Aktion in Outbox, Backoff, nach Ablauf verwerfen + Warnung; Zertifikatsfehler → klare Meldung, nicht blind vertrauen |
| Cast-Gerät schläft/nicht gefunden | Wecken per Abspielversuch, zweiter Versuch, dann Fehler anzeigen; Lokales Abspielen als Fallback (konfigurierbar) |
| MQTT-Broker weg | Puffer begrenzen, Wiederverbinden |
| Sense HAT nicht ansprechbar (I2C) | Anzeige/Sensoren deaktivieren, Hub läuft weiter, Warnung |
| WLAN weg | Warten, NetworkManager reconnect, Outbox hält Aktionen |
| Speicher knapp | Frühwarnung im Dashboard (RAM/Swap), Dienst bei Überschreiten Grenzwert sauber neu starten (`MemoryMax`) |
| SD-Karte voll | Log-Rotation, Warnung ab 85 % Belegung |

## 18.9 Aufstellung im Wohnzimmer – praktische Punkte
- **Funk:** Pi möglichst zentral zu den Würfeln; USB-BLE-Dongle per Verlängerung abgesetzt; nicht hinter dem Fernseher/Metallregal.
- **Wärme:** Pi 3 wird im Gehäuse mit aufgesetztem Sense HAT warm → verfälscht Temperaturmessung; luftiges Gehäuse oder Riser-Kabel; kleiner Kühlkörper optional.
- **Lärm:** Pi 3 ist lüfterlos, geräuschlos.
- **Licht:** LED-Matrix nachts dimmen/aus (Schlafzimmer-/Wohnzimmerbetrieb).
- **Strom:** Steckdosenleiste mit Netzteil nicht zusammen mit anderen Funkstörern; Kabel sicher verlegen.
- **Lautsprecher:** Kein Pi-Ton nötig, solange Musik über die Nest-Geräte (Cast) läuft; sonst USB-Soundkarte/aktive Lautsprecher am 3,5-mm-Ausgang (Qualität mäßig → USB-DAC) oder PULSE-Lautsprecher (siehe oben).
- **Betrieb ohne Bildschirm:** Einmalige Einrichtung per SSH; danach nur Weboberfläche. Zugriff aus der Ferne (unterwegs) optional per Tailscale/WireGuard – **kein Port am Router öffnen**.

## 18.10 Sicherheit (Kurzfassung)
- SSH nur mit Schlüssel, Passwort-Login aus, Standardbenutzer `pi` nicht verwenden (Imager legt eigenen Benutzer an).
- Dienst läuft unter eigenem Benutzer `octapihub` (keine Root-Rechte), Gruppen nur `bluetooth`, `i2c`, `input`, `video`, `audio` nach Bedarf.
- Weboberfläche/API nur im LAN, Token-Pflicht für schreibende Aktionen, kein Internet-Zugriff ohne VPN, Standard-Bindung `0.0.0.0` nur auf ausdrücklichen Wunsch.
- Geheimnisse (Hue-App-Key, MQTT-Passwort, Spotify-Token) in `/etc/octapihub/secrets.env` mit Rechten `0600`, nie im Repository, nie in Logs.
- Firewall (`nftables`/`ufw`) nur Ports 22, 8080 (LAN), 1883 nur lokal.
- Regelmäßige Updates; Sicherheits-Meldeweg für das eigene Projekt später festlegen.

## 18.11 Nicht verifiziert / offene Punkte (Nachtrag 3)
- Genaue **BlueZ-Version** auf aktueller Trixie-Basis (im Handbuch prüfbar).
- Verhalten von **Sense HAT** (v1/v2?) auf Trixie mit dem apt-Paket und in einer venv; Kalibrierungswerkzeug auf Pi 3.
- **USB-Boot** beim Pi 3B (OTP-Freischaltung) – Doku vor Einsatz prüfen.
- Ob der **Würfel zwei Clients** zulässt; ob der **Batteriestand** über den Standard-Batteriedienst lesbar ist.
- **Streamlit-Speicherbedarf** auf dem Pi 3 (nur Forenwerte, keine Messung); FastAPI+HTMX-RAM ist eine Schätzung („deutlich geringer“), nicht gemessen.
- **Trixie-Watchdog-Drop-in** (nur Forenhinweis).
- **pi-topPULSE** unter Trixie (Treiberstatus).
- Zeitangaben (Veröffentlichungsdaten der Trixie-Images) widersprüchlich in der Quelle.

### Quellen (Nachtrag 3)
- Sense HAT (offizielle Doku): https://www.raspberrypi.com/documentation/accessories/sense-hat.html
- sense-hat auf PyPI: https://pypi.org/project/sense-hat
- Raspberry Pi OS „Trixie“-Ankündigung: https://www.raspberrypi.com/news/trixie-the-new-version-of-raspberry-pi-os/
- Raspberry-Pi-OS-Downloads: https://raspberrypi.com/software/operating-systems
- Debian 13 „What’s new“: https://www.debian.org/releases/trixie/release-notes/whats-new.html
- Raspberry-Pi-Magazin, RAM-Optimierung: https://magazine.raspberrypi.com/articles/make-your-ram-go-further-raspberry-pi-os-memory-optimisation-tips
- Streamlit-Speicher/Ressourcen: https://blog.streamlit.io/common-app-problems-resource-limits
- Hardware-Watchdog auf dem Pi: https://pimylifeup.com/raspberry-pi-watchdog/
- systemd-Watchdog: https://support.tq-group.com/en/general_information/hw-watchdog-systemd
- FastAPI+HTMX (Beispiele): https://dev.to/rerere_l_f165d08cdc06148/lightweight-python-stack-for-modern-frontend-fastapi-htmx-jinja2-1f19

---

# NACHTRAG 4 (24.11.2026) – Bluetooth-Dongles für den Hub

> Eigenes Projekt, nicht Teil von OctaLedger. Quellen waren fast nur Shop-Seiten, Kernel-Patches und Forenbeiträge. **Nichts davon habe ich am Gerät getestet.**

## 19.1 Was der Würfel braucht
- Der Hub arbeitet als **BLE-Zentrale**: scannen, verbinden, Notify abonnieren. Nötig ist ein **USB-Bluetooth-Adapter mit HCI-Schnittstelle und Bluetooth ≥ 4.0 LE**, den Linux (`btusb` + BlueZ) sauber anspricht.
- **Nicht nötig:** Bluetooth 5.3/5.4, EDR, Audio-Sender/-Empfänger (A2DP), aptX. Der Würfel überträgt nur wenige Bytes pro Seitenwechsel (Annahme: BLE 4.x).
- Wichtig sind **Linux-Treiber** und **Reichweite/Stabilität**, nicht die Versionsnummer. „Sender & Empfänger“ in den Shop-Texten meint Audio (Musik senden/empfangen) und ist für den Würfel irrelevant.
- BlueZ ≥ 5.55 für Bleak; Home Assistant empfiehlt ≥ 5.63 (aus der HA-Doku).

## 19.2 Bewertung der sechs Kandidaten
| Modell | Chip (Quelle) | Linux/Pi-Einschätzung |
|---|---|---|
| Ugreen CM591 | Actions **ATS2851**, USB-ID 10d7:b012 (Herstellerangaben/Treiberseite). Handbuch nennt nur Windows. | **Riskant.** Kernel-Patches existieren (ID 2022, weitere Quirks mit 6.12), trotzdem gibt es Berichte über Nicht-Funktionieren (Firmware-/Opcode-Fehler). openSUSE-Liste: „nicht unterstützt“. |
| Ugreen CM748 (75073) | vermutlich **Barrot** (33fa:0010, nicht endgültig bestätigt); Versionsangabe 5.3/5.4/6.0 uneinheitlich | **Riskant.** HA-Forum: „funktioniert nicht“, Neustarts; Barrot-Kernel-Quirk existiert, an 0010 nicht getestet (Patchautor). |
| Renkforce RF-BTA-430 | **unbekannt** | Keine Spezifikation gefunden. Shop: „Sender & Empfänger“. |
| Renkforce RF-BTA-380 | **unbekannt** | Shop (Digitec): nur **Empfänger**, 5.3, 20 m (Herstellerangabe). Widerspricht deiner Angabe „Sender & Empfänger“. |
| Techsuit BlueJet A20 | **unbekannt** | Shop: Sender & Empfänger, 5.4, „für PC“, 30 Tage Rückgabe. Keine Chipangabe. |
| Link2Go AD6040BB | unbekannt | Shop: Audio-Adapter, Erscheinungsjahr 2016 → vermutlich **kein** USB-Bluetooth-Stick, sondern Audio-Box. **Ungeeignet** (Annahme). |

## 19.3 Fazit
- **Link2Go: ausscheiden.** Ugreen CM591/CM748: nur mit Rückgaberecht und Test, nicht als erste Wahl.
- Zwischen **Renkforce RF-BTA-430** und **Techsuit A20** kann ich nicht belegt entscheiden (Chip unbekannt). Kaufe dort, wo 30 Tage Rückgabe gelten, und prüfe sofort (Handbuch Kap. 6: `lsusb`, `dmesg`, `bluetoothctl list`, Scan).
- Bewährter, aber ggf. teurer als 10 CHF: Adapter mit **Realtek RTL8761B** (z. B. TP-Link UB500, ASUS USB-BT500) oder **CSR8510** (Quellen: Community/HA-Doku; Preis nicht geprüft). Broadcom BCM20702 steht in der HA-Doku als funktionierend, braucht ggf. Firmware.
- **Zuerst ohne Dongle probieren:** Der Onboard-Bluetooth des Pi 3 (BLE 4.1) reicht für die Würfel. Ein Dongle lohnt sich nur bei Aussetzern durch die WLAN/BT-Teilung.

### Quellen
- ATS2851: https://oemdrivers.com/bluetooth-ugreen-cm591 · https://bugs.launchpad.net/bugs/2056800 · https://bugzilla.kernel.org/show_bug.cgi?id=217870
- Barrot/CM748: https://community.home-assistant.io/t/the-ugren-cm748-bluetooth-adapter-does-not-work-in-ha/833948 · https://spinics.net/lists/linux-bluetooth/msg122687.html · https://oemdrivers.com/bluetooth-ugreen-75073
- Renkforce/Techsuit/Link2Go (Shop): https://www.digitec.ch/en/s1/product/renkforce-rf-bta-430-bluetooth-stick-53-transmitter-receiver-bluetooth-audio-adapters-46404005 · https://www.digitec.ch/en/s1/product/renkforce-rf-bta-380-bluetooth-stick-53-receiver-bluetooth-audio-adapters-46000372 · https://www.digitec.ch/en/s1/product/link2go-ad6040bb-transmitter-receiver-bluetooth-audio-adapters-5741506
- HA-Bluetooth-Doku: https://www.home-assistant.io/integrations/bluetooth/

---

# NACHTRAG 5 (24.11.2026) – Forenrecherche: welcher Bluetooth-Stick (modern, Linux/Pi/Home Assistant)

> Eigenes Projekt. Quellen: Home-Assistant-Forum und -Doku, Bleak-Doku, Linux-Foren/Patches. **Nichts am Gerät getestet; Preise nicht geprüft.** Korrigiert Nachtrag 4 §19.3 in einem Punkt: TP-Link UB500 ist umstritten.

## 20.1 Ergebnisse
- **Realtek RTL8761B(U/E)** (BT 5.0/5.1) ist in der HA-Community der meistgenannte Chip für Linux/Pi. Die am häufigsten empfohlenen Sticks: **EDUP EP-B3536 Plus (RTL8761BUE, ca. 10 USD)**, **ASUS USB-BT500** (steht auf der Empfehlungsliste). Berichte über Funktion am Pi 4 existieren.
- **Firmware:** Realtek braucht Firmware-Dateien (`rtl_bt/…`). Eine Forenquelle: unter Raspbian war sie nicht vorinstalliert und das war der Schlüssel zur Lösung. Debian-Paket vermutlich `firmware-realtek` (auf Trixie **nicht geprüft**). Ein Mercusys-Stick (RTL8761BU) wurde unter Manjaro erkannt, erzeugte aber kein HCI-Gerät – Einzelfall.
- **TP-Link UB500** (ebenfalls RTL8761B): widersprüchlich – leere Scan-Ergebnisse unter HassOS in VM gemeldet, TP-Link bewirbt Linux nicht. Nicht als Erstwahl.
- **ATS2851** (Ugreen CM591, EDUP EP-B3552): läuft anfangs, „stürzt über Zeit ab“ (Forum) → meiden.
- **Barrot (Ugreen CM748, „BT 6.0“-Sticks, 33fa:0010/0012):** Erkennungsprobleme unter Debian 13 und Zorin gemeldet; nur inoffizielle/neue Kernel-Patches → meiden.
- **Bluetooth 6.0:** Ich fand **keinen** 6.0-Stick mit bestätigter Linux-/Pi-Funktion. **ASUS USB-BT600** (6.0) listet Linux, Chip aber unbekannt/ungeprüft. TP-Link UB600: nur Windows.
- **Intel BE200 (USB, 6.0-nah):** HA-Forum meldet HCI-Timeouts/Reset-Schleife (in VM).
- **Bleak-Doku:** Onboard-Chip des Pi kann „Not connected“ verursachen; Abhilfe WLAN aus oder USB-Adapter. Mehrere Geräte: vorher scannen, nacheinander verbinden.
- **Bessere Version bringt für den Würfel nichts:** Verbindung, Notify und Reichweite funktionieren auch mit BT 5.0; 5.x/6.0 ist nur „für später“ und Linux-Treiber hinken neuen Chips hinterher.
- **Alternative (nur mit Home Assistant):** ESPHome-Bluetooth-Proxy (ESP32) statt USB-Stick; viele Nutzer berichten mehr Stabilität. Für eigenen Bleak-Dienst ohne HA nicht nutzbar.
- **Hue:** Die Lampen laufen über Zigbee an der Bridge, der Hub spricht nur IP mit der Bridge. Für Hue ist der Bluetooth-Stick nicht nötig.

## 20.2 Empfehlung
1. **EDUP EP-B3536 Plus (RTL8761BUE, BT 5.1)** – günstig, Community-erprobt. 2. **ASUS USB-BT500 (RTL8761B, BT 5.0)** – ca. doppelt so teuer. Beide: Chip vor dem Kauf auf der Produktseite bestätigen (Händler tauschen Chips ohne Hinweis aus), `lsusb` zeigt `0bda:8771`-artige Realtek-ID (genaue ID nicht verifiziert).
3. Wunsch „6.0“: nur ASUS USB-BT600 als Experiment, nicht als Hauptlösung.

### Quellen
- https://community.home-assistant.io/t/recommended-usb-bluetooth-adapter/616065 · https://community.home-assistant.io/t/bluetooth-dongle-for-ha/528047 · https://community.home-assistant.io/t/bluetooth-usb-adapter-for-hassos-tp-link-ub500-not-working/404517 · https://www.home-assistant.io/integrations/bluetooth/
- https://community.home-assistant.io/t/the-ugren-cm748-bluetooth-adapter-does-not-work-in-ha/833948 · https://forum.zorin.com/t/my-bluetooth-adapter-is-not-working/55017 · https://community.home-assistant.io/t/intel-be200-bluetooth-usb-unstable-in-haos-18-1-hci-timeout-reset-loop-in-vm-environment/1015884
- https://bleak.readthedocs.io/en/latest/troubleshooting.html · https://www.bhphotovideo.com/c/product/1967161-REG/asus_usb_bt600_bluetooth_6_0_adapter.html
