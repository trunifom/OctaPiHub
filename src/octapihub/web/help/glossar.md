# Glossar

Fachbegriffe in einfachen Worten.

| Begriff | Bedeutung |
|---|---|
| **Aktion** | Ein Schritt, den eine Würfelseite auslöst: z. B. Szene einschalten, Playlist abspielen, Webhook senden. |
| **Aktionswarteschlange (Outbox)** | Eine Liste auf der Festplatte, in der Aktionen warten, bis sie ausgeführt sind. Je Würfel in der richtigen Reihenfolge, mit Wiederholung, Pausen und Ablaufzeit. |
| **Akkustand** | Ladezustand des Würfels in Prozent; unter 20 % „Bald laden“, unter 10 % „Bitte jetzt laden“. |
| **Baseline** | Der erste Lagewert nach dem Verbinden. Er legt fest, wo der Würfel liegt, und löst keine Aktion aus. |
| **Bluetooth LE** | Der Funkstandard, mit dem der Würfel mit dem Pi spricht (Low Energy). |
| **Bridge (Hue)** | Das Kästchen, das Philips-Hue-Lampen steuert. Der Hub spricht lokal mit ihm. |
| **Broker (MQTT)** | Ein Vermittler, der Nachrichten zwischen Geräten verteilt, z. B. Mosquitto. |
| **Cast / Google Cast** | Das Verfahren, mit dem Musik oder Bilder an Google-Nest-Lautsprecher und -Displays gesendet werden. |
| **Entprellung** | Schnelles Kippen zählt nur einmal: die letzte Seite. |
| **entity_id** | Der Name eines Geräts in Home Assistant, z. B. `light.schreibtisch`. |
| **Export / Import** | Einstellungen in eine Datei sichern bzw. aus einer Datei laden. |
| **Fehlercode** | Kennung wie `CH-MUS-002`; sie führt zur Erklärung unter Hilfe → Fehlercodes. |
| **grouped_light** | Hue-Begriff für die Gruppe von Lampen eines Raums, einer Zone oder des ganzen Hauses. |
| **Halterung** | Der Zustand „Würfel steht senkrecht“; Seite 0. |
| **Home Assistant** | Eine verbreitete Smart-Home-Software, die viele Systeme (auch Matter) verbindet. |
| **Hub** | Der Raspberry Pi mit OctaPiHub; die Schaltzentrale. |
| **Joystick** | Der kleine Steuerknüppel auf dem Sense HAT mit fünf Richtungen. |
| **Konfigurationsdatei** | `octapihub.toml`: alle Einstellungen in einer Textdatei. |
| **LED-Matrix** | Die 8×8 farbigen Leuchtpunkte auf dem Sense HAT. |
| **Matter** | Ein neuer Standard für Smart-Home-Geräte. OctaPiHub erreicht Matter-Geräte über Home Assistant. |
| **mpv** | Das Programm, das auf dem Pi Musik abspielt (Ausgang und Bluetooth-Box). |
| **MQTT** | Ein Nachrichtenprotokoll des Smart Homes. OctaPiHub sendet Nachrichten (Thema + Inhalt). |
| **Payload** | Der Inhalt einer MQTT-Nachricht. |
| **Playlist** | Eine Musikquelle (Ordner, Datei, Radio, YouTube) mit Vorlieben wie Zufall und Wiederholen. |
| **Plugin** | Ein Baustein für eine Aktionsart (Hue, Musik, Smart Home). |
| **QoS** | MQTT-Zustellung: 0 = höchstens einmal, 1 = mindestens einmal. |
| **Raspberry Pi** | Der kleine Computer, auf dem der Hub läuft. |
| **Retain** | MQTT: Der Broker behält die letzte Nachricht für später hinzukommende Empfänger. |
| **Scene (Szene)** | Eine gespeicherte Lichtstimmung der Hue-App. |
| **secrets.env** | Geschützte Datei für Passwörter, Schlüssel und Token (Rechte 0600). |
| **Sense HAT** | Der Aufsatz mit LED-Matrix, Joystick und Sensoren. |
| **Seite (Würfel)** | Eine der acht Flächen des Würfels, nummeriert 1 bis 8. |
| **SSE** | Technik, mit der die Oberfläche live aktualisiert wird, ohne ständig nachzufragen. |
| **systemd** | Der Dienstverwalter des Raspberry Pi OS; startet OctaPiHub beim Hochfahren. |
| **Thema (Topic)** | Die „Adresse“ einer MQTT-Nachricht, z. B. `zigbee2mqtt/lampe/set`. |
| **Token (Zugriffs-Token)** | Ein langes Passwort für die Weboberfläche; nötig zum Ändern. |
| **TLS** | Verschlüsselung einer Verbindung (https, MQTT über Port 8883). |
| **Webhook** | Eine Web-Adresse, deren Aufruf in einem anderen System etwas auslöst. |
| **WLED / Tasmota / Shelly / ESPHome** | Verbreitete Software bzw. Geräte für selbstgebaute und günstige Smart-Home-Geräte mit Web-Schnittstelle. |
| **yt-dlp** | Hilfsprogramm, das YouTube-Links für mpv auflöst. |
| **Zigbee2MQTT** | Verbindet Zigbee-Geräte mit MQTT. |
