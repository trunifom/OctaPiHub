# Sicherheit und Datenschutz

## 1. Das Wichtigste in Kürze
- OctaPiHub läuft **lokal**. Es sendet nichts an den Hersteller oder an Dritte, außer an die Ziele, die **Sie** einrichten (Hue Bridge, Google-Lautsprecher, MQTT-Broker, Home Assistant, Webhooks, Radio- und YouTube-Adressen).
- **Geben Sie keine Ports im Router frei.** Die Oberfläche hat keine Verschlüsselung (HTTP) und keine Benutzerkonten.
- **Ansehen** darf jeder im Heimnetz, **Ändern** nur, wer das **Zugriffs-Token** kennt.

## 2. Zugriffsschutz
| Maßnahme | Wirkung |
|---|---|
| Zugriffs-Token | Alle Änderungen und Aktionen (speichern, abspielen, koppeln, exportieren, suchen) verlangen es. Es wird mit konstanter Zeit verglichen. |
| Token-Prüfung beim Start | Fehlt es, ist es kürzer als 12 Zeichen oder noch der Platzhalter, startet der Dienst nicht (`CH-WEB-008`) bzw. die Oberfläche ist nur lesend. |
| Nur lokal als Standard | Der Dienst lauscht auf `127.0.0.1`. Das Heimnetz schalten Sie bewusst mit `OCTAPIHUB_HOST` frei. |
| Content-Security-Policy | Die Seiten laden nur eigene Skripte und Stile (`default-src 'self'`); Einbetten in fremde Seiten ist verboten. |

Wählen Sie ein **langes, zufälliges** Token (`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`) und geben Sie es nicht weiter. Es ist nur so sicher wie Ihr Netz.

## 3. Geheimnisse
- **Hue-Schlüssel, MQTT-Passwörter, Home-Assistant-Token** liegen ausschließlich in `secrets.env` (Rechte 0600, Besitzer `octapihub`). Dateien mit offenen Rechten werden abgelehnt (`CH-SEC-001`).
- Sie erscheinen **nie** in der Oberfläche (nur „gespeichert“), in der Konfiguration, in Protokollen, im Diagnose-Paket oder in Export-Dateien.
- Das Token der Oberfläche steht in `octapihub.env` (Rechte 0600).

## 4. Was in Protokollen und Exporten steht
| | Zugangsdaten | Bluetooth-Adressen | Web-Adressen von Webhooks |
|---|---|---|---|
| Protokoll / Logdatei | unkenntlich | gekürzt | nicht protokolliert |
| Diagnose-Paket | nicht enthalten | gekürzt | nicht enthalten |
| Export-Datei | nicht enthalten | nicht enthalten | **enthalten** (Konfiguration) |
Behandeln Sie Export-Dateien deshalb vertraulich, wenn Ihre Webhook-Adressen Kennungen enthalten.

## 5. Dateien und Pfade
- Musik wird **nur aus dem Musikordner** gelesen. Pfade mit `..`, Netzwerkpfade und Verknüpfungen, die aus dem Ordner hinausführen, werden abgelehnt – auch in Playlist-Dateien.
- Google-Lautsprecher bekommen Dateien über **zufällige, ablaufende Adressen** (`/media/<Token>`). Bei jedem Abruf wird erneut geprüft, dass die Datei noch im Musikordner liegt.
- Der Datenordner und die Konfiguration gehören dem Benutzer `octapihub`; die systemd-Unit schränkt den Dienst stark ein (kein Root, kaum Schreibrechte, kein `/home`).

## 6. Web-Aufrufe der Smart-Home-Aktionen
- Nur `http` und `https`, ohne Zugangsdaten in der Adresse.
- **Weiterleitungen werden nicht verfolgt**, Umgebungs-Proxys ignoriert, Link-lokale Adressen (`169.254.x.x`, z. B. Cloud-Metadaten) abgelehnt.
- Antworten werden nur bis zur Statuszeile (maximal 4 KB) gelesen.
- **Keine doppelte Wirkung:** Nur wenn sicher nichts gesendet wurde, wird wiederholt.

## 7. Bluetooth
Ein Würfel spricht Bluetooth LE. Der Hub liest nur die Lage-Meldungen und den Akkustand; er schreibt nichts in den Würfel. Bluetooth-Adressen von Würfeln werden in der Oberfläche nicht und in Protokollen nur gekürzt gezeigt.

## 8. Empfehlungen
1. Eigenes, langes Token; nicht im Browser speichern lassen.
2. Router: keine Portfreigabe, Gäste-WLAN getrennt halten.
3. Raspberry Pi OS aktuell halten (`sudo apt update && sudo apt full-upgrade`).
4. Sicherungen (`tar` von `/etc/octapihub` und `/var/lib/octapihub`) privat aufbewahren: sie enthalten Token und Schlüssel.
5. Ein eigenes Token pro Dienst: Home-Assistant-Token nur für OctaPiHub anlegen, damit Sie es jederzeit löschen können.

## 9. Grenzen
- **Keine Verschlüsselung** der Oberfläche (HTTP) und keine Benutzerverwaltung. Wer im Heimnetz mithören kann, könnte das Token abfangen. Für Fernzugriff nutzen Sie ein VPN, nicht eine Portfreigabe.
- Die Software wurde **nicht** von Dritten auf Sicherheit geprüft.
