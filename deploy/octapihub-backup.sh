#!/bin/sh
# Sicherung und Wiederherstellung von OctaPiHub (Einstellungen, Zugangsdaten, Daten).
#
#   sudo ./octapihub-backup.sh backup  [ZIELORDNER]      Archiv anlegen (Standard: aktueller Ordner)
#   sudo ./octapihub-backup.sh restore ARCHIV            Archiv zurückspielen (Dienst wird kurz gestoppt)
#   sudo ./octapihub-backup.sh list    ARCHIV            Inhalt anzeigen
#
# Das Archiv enthält Token, Hue-Schlüssel, MQTT-Passwörter und Datenbanken: privat aufbewahren!
# Noch nicht auf einem echten Raspberry Pi ausprobiert.
set -eu

CONFIG_DIR="${OCTAPIHUB_CONFIG_DIR:-/etc/octapihub}"
DATA_DIR="${OCTAPIHUB_DATA_DIR:-/var/lib/octapihub}"
SERVICE="${OCTAPIHUB_SERVICE:-octapihub}"

usage() {
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

action="${1:-}"
[ -n "$action" ] || usage

case "$action" in
backup)
    target="${2:-.}"
    [ -d "$target" ] || { echo "Zielordner nicht gefunden: $target" >&2; exit 1; }
    [ -d "$CONFIG_DIR" ] || { echo "Einstellungsordner nicht gefunden: $CONFIG_DIR" >&2; exit 1; }
    archive="$target/octapihub-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    umask 077
    tar czf "$archive" -C / "${CONFIG_DIR#/}" "${DATA_DIR#/}" 2>/dev/null || {
        echo "Sicherung fehlgeschlagen (Rechte? mit sudo ausführen)." >&2
        rm -f "$archive"
        exit 1
    }
    chmod 600 "$archive"
    echo "Sicherung angelegt: $archive"
    ;;
list)
    [ -f "${2:-}" ] || usage
    tar tzf "$2"
    ;;
restore)
    archive="${2:-}"
    [ -f "$archive" ] || usage
    tar tzf "$archive" >/dev/null 2>&1 || { echo "Das ist kein gültiges Archiv: $archive" >&2; exit 1; }
    echo "Der Dienst $SERVICE wird gestoppt, $CONFIG_DIR und $DATA_DIR werden ersetzt."
    printf "Fortfahren? (ja/nein) "
    read -r answer
    [ "$answer" = "ja" ] || { echo "Abgebrochen."; exit 1; }
    systemctl stop "$SERVICE" || true
    tar xzf "$archive" -C /
    systemctl start "$SERVICE" || true
    echo "Wiederhergestellt. Prüfen: systemctl status $SERVICE"
    ;;
*)
    usage
    ;;
esac
