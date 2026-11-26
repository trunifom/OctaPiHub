# OctaPiHub als Container (optional)

Der empfohlene Weg ist der systemd-Dienst ([deploy/INSTALL.md](../deploy/INSTALL.md)): Bluetooth, I2C (Sense HAT)
und Audio sind dort am einfachsten. Das `Dockerfile` im Hauptordner gibt es für Tests und für Geräte, auf denen
Sie ohnehin Container nutzen. **Es wurde weder gebaut noch auf einem Raspberry Pi ausprobiert.**

## Bauen und starten

```bash
docker build -t octapihub .
docker run -d --name octapihub --restart unless-stopped \
  --network host \
  -e OCTAPIHUB_WEB_TOKEN="ein-langes-zufaelliges-token" \
  -v /etc/octapihub:/etc/octapihub \
  -v /var/lib/octapihub:/var/lib/octapihub \
  --device /dev/i2c-1 \
  octapihub
```

- `--network host`: nötig für Google Cast und die Suche im Heimnetz (mDNS).
- Bluetooth (BlueZ über D-Bus) und Audio brauchen zusätzlich `-v /var/run/dbus:/var/run/dbus` und Zugriff auf die
  Audio-Geräte (`--device /dev/snd`). Das hängt stark vom System ab.
- Die Konfiguration (`octapihub.toml`, `secrets.env`) liegt auf dem Wirt in `/etc/octapihub`, die Zustandsdaten in
  `/var/lib/octapihub`; beides überlebt ein Neuerstellen des Containers.
- Der Container lauscht auf `0.0.0.0`. **Geben Sie den Port nicht im Router frei.**

## Aktualisieren

```bash
git pull && docker build -t octapihub . && docker rm -f octapihub && docker run ... (wie oben)
```

Sichern Sie vorher mit `octapihub-backup.sh` oder über die Seite **Sicherung**.

## Automatische Prüfung

`.github/workflows/ci.yml` führt bei jedem Push `ruff`, `mypy` und `pytest` aus (Python 3.12 und 3.13). Das läuft auf
GitHub, nicht auf einem Raspberry Pi.
