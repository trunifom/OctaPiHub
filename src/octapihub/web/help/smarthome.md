# Smart Home: andere Geräte und Systeme steuern

Mit der Seite **Smart Home** löst eine Würfelseite (oder eine Joystick-Richtung) auch Aktionen in **anderen Systemen** aus. OctaPiHub spricht dafür drei „Sprachen“ und ist über **Home Assistant** auch der Weg zu **Matter**.

> **Ehrlicher Stand:** Alle drei Wege wurden nur gegen lokale Test-Server und einen Test-Broker geprüft, nicht gegen ein echtes Gerät, Mosquitto oder Home Assistant. Die Beispiele sind aus den Anleitungen der jeweiligen Geräte abgeleitet.

## 1. Überblick: welche Sprache wofür?
| Sie haben … | Nehmen Sie … | Aufwand |
|---|---|---|
| ein Gerät mit **Web-Schnittstelle** (Shelly, Tasmota, WLED, ESPHome, eigene Skripte, Node-RED, IFTTT-ähnliche Dienste) | **Webhook** | gering |
| **Zigbee-Geräte über Zigbee2MQTT**, Tasmota/ESPHome mit MQTT, Node-RED | **MQTT-Nachricht** | mittel (Broker nötig) |
| **Matter-Geräte**, Google-Home-Geräte, Zigbee, KNX, … die in **Home Assistant** eingebunden sind | **Home-Assistant-Dienst** | mittel (Home Assistant nötig) |

### Und Matter, CoAP, …?
- **Matter** wird nicht direkt gesprochen (ein Matter-„Controller“ ist ein eigenes, großes Programm mit Zertifikaten). Der empfohlene Weg: Binden Sie Ihre Matter-Geräte in **Home Assistant** ein (Einstellungen → Geräte & Dienste → Matter) und lösen Sie sie mit einer **Home-Assistant-Aktion** aus. Das funktioniert auch für Google-Home-Geräte, die Home Assistant kennt.
- **CoAP** (ein schlankes Protokoll für kleine Geräte) wird nicht unterstützt. Fast alle CoAP-Geräte sprechen zusätzlich HTTP oder lassen sich über Home Assistant oder MQTT erreichen.
- Weitere Wünsche (z. B. direkte Zigbee-Anbindung) stehen im Pflichtenheft unter „Geplant“.

## 2. Webhook
Ein Webhook ruft eine **Web-Adresse** auf. Eine Aktion braucht keine Verbindung.

| Feld | Beispiel | Bedeutung |
|---|---|---|
| Name | `Garagentor auf` | erscheint in der Zuordnung |
| Art | Webhook | |
| Web-Adresse | `http://192.168.1.31/relay/0?turn=on` | nur `http://` oder `https://`, **ohne** Benutzername/Passwort in der Adresse |
| Anfrage-Art | GET | GET, POST, PUT, PATCH oder DELETE |
| Inhaltstyp, Inhalt | JSON, `{"on":true}` | nur wenn das Gerät einen Inhalt erwartet (höchstens 500 Zeichen) |

**Sicherheitsregeln:** Weiterleitungen (3xx) werden **nicht verfolgt**; Umgebungs-Proxys werden ignoriert; Adressen wie `169.254.x.x` (Link-lokal/Cloud-Metadaten) werden abgelehnt; es wird nur die Statuszeile gelesen. Eine Web-Adresse kann Geheimnisse enthalten (etwa die Kennung eines Home-Assistant-Webhooks): in der Zuordnung wird sie deshalb gekürzt angezeigt.

### Beispiele
| Gerät | Adresse | Art | Inhalt |
|---|---|---|---|
| **Shelly** (Gen 1), Relais an | `http://<IP>/relay/0?turn=on` | GET | – |
| **Shelly**, Relais aus | `http://<IP>/relay/0?turn=off` | GET | – |
| **Tasmota**, einschalten | `http://<IP>/cm?cmnd=Power%20On` | GET | – |
| **WLED**, einschalten | `http://<IP>/json/state` | POST | `{"on":true}` (JSON) |
| **Home-Assistant-Webhook** | `http://<HA>:8123/api/webhook/<WEBHOOK-ID>` | POST | optional JSON |
Passen Sie Adressen an Ihr Gerät an; die genauen Pfade stehen in der Anleitung des Geräts.

## 3. MQTT
MQTT ist der Standard des Smart Homes: Ein **Broker** (z. B. Mosquitto) verteilt Nachrichten an Geräte. OctaPiHub **sendet** Nachrichten (es abonniert keine).

### Verbindung anlegen
| Feld | Beispiel |
|---|---|
| Art | MQTT-Broker |
| Adresse | `192.168.1.20:1883` (Name oder IP, **mit Port**; kein `mqtt://`) |
| Benutzername | `hub` (leer = ohne Anmeldung) |
| Passwort | wird nur in `secrets.env` gespeichert |
| Verschlüsselt (TLS) | nur wenn der Broker es verlangt, meist Port 8883 |

### Aktion anlegen
| Feld | Beispiel | Bedeutung |
|---|---|---|
| Verbindung | der Broker | |
| Thema | `zigbee2mqtt/schreibtisch/set` | **ohne** Platzhalter `+`, `#` und nicht mit `$` beginnend |
| Inhalt | `{"state":"ON"}` | höchstens 500 Zeichen |
| Zustellung (QoS) | 0 | 0 = höchstens einmal, 1 = mindestens einmal (mit Bestätigung) |
| Behalten (retain) | aus | für Schaltbefehle meist aus |

Nehmen Sie **ON** und **OFF** statt **TOGGLE**: Umschalten wirkt bei jedem Senden erneut. Zigbee2MQTT-Lampen: Thema `zigbee2mqtt/<Gerätename>/set`, Inhalt `{"state":"ON"}`, `{"state":"OFF"}` oder `{"brightness":128}`.

**Technik:** MQTT 3.1.1, QoS 0 und 1; die Software spricht das Protokoll selbst (keine zusätzliche Programmbibliothek) und verbindet nur für die Dauer einer Nachricht.

## 4. Home Assistant (und damit Matter)
### Token erstellen
1. In Home Assistant unten links auf Ihren **Benutzernamen** klicken (Profil).
2. Tab **Sicherheit**, ganz unten **Langlebige Zugriffstoken** → **Token erstellen** → Namen „OctaPiHub“ vergeben.
3. Das Token **sofort kopieren** (es wird nur einmal angezeigt) und in OctaPiHub einfügen.

### Verbindung anlegen
| Feld | Beispiel |
|---|---|
| Art | Home Assistant |
| Adresse | `http://192.168.1.20:8123` (genau wie im Browser, mit Port) |
| Zugriffstoken | das Token aus dem Profil (wird nur in `secrets.env` gespeichert) |

### Aktion anlegen
| Feld | Beispiel | Bedeutung |
|---|---|---|
| Dienst | `light.turn_on` | Format `bereich.dienst` (Entwicklerwerkzeuge → Aktionen) |
| Gerät | `light.schreibtisch` | „entity_id“, Format `bereich.name`; leer, wenn der Dienst keins braucht |
| Zusatzdaten | `{"brightness": 120}` | JSON-Objekt, optional |

Beispiele: `scene.turn_on` mit Gerät `scene.kino`; `switch.toggle` mit `switch.steckdose`; `script.turn_on` mit `script.guten_morgen`; `light.turn_off` ohne Gerät schaltet alles Licht aus.

**Technik:** OctaPiHub ruft `POST <Adresse>/api/services/<Bereich>/<Dienst>` mit dem Token als `Authorization: Bearer …` auf.

## 5. Aktion einer Würfelseite zuordnen
Seite **Würfel** → Auswahlfeld der Seite → Gruppe **Smart Home** → Ihre Aktion → *Zuordnung speichern*. Unter „Was passiert, wenn diese Seite oben liegt?“ sehen Sie danach Art und Ziel.

## 6. Testen und Fehler
- Jede gespeicherte Aktion hat **Jetzt senden (Test)**. Darunter zeigt ein Hinweis, ob der **letzte Versuch** geklappt hat, sonst mit Fehlercode.
- **CH-HOME-002 „nicht erreichbar“:** Adresse, Port, Netzwerk prüfen. Es wurde **nichts gesendet**, der Hub versucht es kurz erneut.
- **CH-HOME-003 „abgelehnt“:** Das Ziel hat mit einem Fehler geantwortet, Passwort/Token abgelehnt (401/403), oder es kam keine Bestätigung. Es wird **nicht wiederholt**, weil die Aktion schon angekommen sein könnte (ein zweites „Umschalten“ würde das Gerät wieder zurückschalten).
- **CH-HOME-004 „nicht eingerichtet“:** Aktion oder Verbindung fehlt, oder es fehlt das Token.
- Aktionen verfallen nach etwa 2 Minuten, damit nichts viel später passiert.

## 7. Datenschutz und Sicherheit
- Passwörter und Tokens liegen nur in `secrets.env` (Rechte 0600), nie in der Konfiguration, in Protokollen, in Diagnose-Paketen oder in Exporten.
- Web-Adressen von Webhooks stehen in der Konfiguration und im Export. Behandeln Sie die Export-Datei deshalb vertraulich, wenn Ihre Webhooks Kennungen enthalten.
- Die Smart-Home-Aktionen sprechen nur mit den von Ihnen eingetragenen Zielen.
