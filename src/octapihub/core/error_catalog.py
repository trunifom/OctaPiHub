# ruff: noqa: E501 - the catalogue is prose; long lines read better than broken ones
"""The catalogue of every error code: what it means and what to do about it.

One source for the interface (page "Hilfe → Fehlercodes", error pages), the documentation, and a
test that fails when a code exists in the software but not here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """One error code in plain language."""

    code: str
    area: str
    title: str
    meaning: str
    action: str
    retried: bool = False  # the action queue tries again by itself


def _e(
    code: str, area: str, title: str, meaning: str, action: str, retried: bool = False
) -> ErrorInfo:
    return ErrorInfo(code, area, title, meaning, action, retried)


_ENTRIES = (
    _e(
        "CH-GENERIC-000",
        "Allgemein",
        "Unerwarteter Fehler",
        "Etwas ist schiefgelaufen, das die Software nicht genauer einordnen konnte.",
        "Seite System öffnen, Meldungen ansehen und das Diagnose-Paket speichern. Dienst notfalls neu starten.",
    ),
    # ----- Einstellungen
    _e(
        "CH-CFG-001",
        "Einstellungen",
        "Einstellungen nicht lesbar oder nicht speicherbar",
        "Die Konfigurationsdatei fehlt, ist kein gültiges TOML, hat eine unbekannte Version oder lässt sich nicht schreiben.",
        "Pfad und Rechte der Datei prüfen (Besitzer octapihub). Beim Start nutzt OctaPiHub automatisch die neueste "
        "gültige Sicherung `octapihub.toml.bak-…`, falls vorhanden; sonst mit `octapihub --check` die Datei prüfen.",
    ),
    _e(
        "CH-CFG-002",
        "Einstellungen",
        "Ungültiger Wert in den Einstellungen",
        "Ein Feld verletzt die Regeln (Format, Bereich, Länge, Verweis auf etwas, das es nicht gibt).",
        "Das genannte Feld korrigieren. Beim Speichern in der Oberfläche wird dann nichts verändert.",
    ),
    # ----- Bluetooth / Würfel
    _e(
        "CH-BLE-001",
        "Würfel",
        "Würfel nicht erreichbar",
        "Die Bluetooth-Verbindung zum Würfel ließ sich nicht aufbauen oder ist abgebrochen.",
        "Würfel kippen (aufwecken), Akku und Reichweite prüfen, mit dem Smartphone trennen, Adresse prüfen. "
        "Der Hub versucht es selbst mit wachsenden Pausen erneut.",
        True,
    ),
    _e(
        "CH-BLE-002",
        "Würfel",
        "Unerwartete Daten vom Würfel",
        "Der Würfel hat Daten gesendet, die nicht dem erwarteten Format entsprechen.",
        "Prüfen, ob es wirklich ein Timeular-/ZEI-Würfel ist. Tritt es wiederholt auf, Diagnose-Paket speichern.",
    ),
    _e(
        "CH-BLE-003",
        "Würfel",
        "Würfel bietet die Funktion nicht an",
        "Das Gerät hat die benötigte Bluetooth-Funktion (Lage-Meldungen) nicht.",
        "Adresse prüfen: vermutlich ist es ein anderes Gerät.",
    ),
    # ----- Sense HAT
    _e(
        "CH-SENSE-001",
        "Sense HAT",
        "Sense HAT nicht ansprechbar",
        "Das Sense HAT wurde nicht gefunden oder antwortet nicht (I2C).",
        "I2C einschalten (`raspi-config`), Aufsatz fest aufstecken, Gruppe `i2c` des Dienstbenutzers prüfen. "
        "Der Hub läuft ohne Sense HAT weiter und versucht es erneut.",
        True,
    ),
    _e(
        "CH-SENSE-002",
        "Sense HAT",
        "Sensor oder Funktion nicht verfügbar",
        "Diese Messung oder Funktion gibt es auf dem erkannten Sense HAT nicht (z. B. Farbsensor bei Version 1).",
        "Die Sensorgruppe in den Einstellungen ausschalten oder die Hardware-Version prüfen.",
    ),
    # ----- Datenbank
    _e(
        "CH-DB-001",
        "Datenbank",
        "Datenordner nicht beschreibbar",
        "Die Warteschlange der Aktionen (SQLite) konnte nicht angelegt oder beschrieben werden.",
        "Datenordner (`--data-dir`, Standard /var/lib/octapihub) und seine Rechte prüfen, Speicherplatz prüfen.",
    ),
    _e(
        "CH-DB-002",
        "Datenbank",
        "Verlauf nicht lesbar oder speicherbar",
        "Die Datenbank mit dem Sensorverlauf meldet einen Fehler.",
        "Speicherplatz und Rechte prüfen. Die Messwerte werden weiter angezeigt, nur der Verlauf fehlt. "
        "Notfalls die Datei `…/history.sqlite3` sichern und löschen (sie wird neu angelegt).",
    ),
    # ----- Geheimnisse
    _e(
        "CH-SEC-001",
        "Zugangsdaten",
        "Zugangsdatei unsicher oder ungültig",
        "Die Datei `secrets.env` ist für andere lesbar, hat ein falsches Format oder lässt sich nicht schreiben.",
        "Rechte auf 0600 setzen (`chmod 600`), Besitzer octapihub, Format `SCHLÜSSEL=wert` je Zeile.",
    ),
    # ----- Musik
    _e(
        "CH-MUS-001",
        "Musik",
        "Musik allgemein fehlgeschlagen",
        "Eine Musik-Aktion ist aus einem nicht genauer bekannten Grund gescheitert.",
        "Seite Musik: Gerät und Quelle mit „Jetzt abspielen (Test)“ prüfen, dann Meldungen auf der Seite System.",
        True,
    ),
    _e(
        "CH-MUS-002",
        "Musik",
        "Wiedergabegerät antwortet nicht",
        "Der Lautsprecher, das Display, der Audioausgang oder die Bluetooth-Box ist nicht erreichbar oder lehnt ab.",
        "Gerät einschalten, im selben Netz? mpv installiert? Bluetooth-Box verbunden? Der Hub versucht es kurz erneut.",
        True,
    ),
    _e(
        "CH-MUS-003",
        "Musik",
        "Musikquelle unbrauchbar",
        "Die Quelle ist ungültig, nicht gefunden, liegt außerhalb des Musikordners oder ist ein Spotify-Link.",
        "Quelle der Playlist prüfen. Dateien gehören in den Musikordner. Spotify lässt sich nicht abspielen.",
    ),
    _e(
        "CH-MUS-004",
        "Musik",
        "Musik nicht eingerichtet",
        "Musik ist ausgeschaltet, es ist kein Gerät gewählt oder Playlist bzw. Gerät gibt es nicht mehr.",
        "Seite Musik: „Musik verwenden“ einschalten, Gerät und Playlist anlegen.",
    ),
    _e(
        "CH-MUS-005",
        "Musik",
        "Es fehlt ein Programm oder Paket",
        "Für diese Wiedergabe fehlt zum Beispiel `pychromecast` oder das Betriebssystem passt nicht.",
        'Installationsanleitung Abschnitt 4 (`pip install ".[music]"`) und Abschnitt 2 (`apt install mpv`).',
    ),
    # ----- Plugins
    _e(
        "CH-PLUG-001",
        "Aktionen",
        "Aktionsart nicht installiert",
        "Eine Zuordnung nutzt eine Aktionsart (Plugin), die es nicht gibt.",
        "Zuordnung ändern oder das fehlende Erweiterungsmodul installieren. Die alte Zuordnung bleibt unverändert.",
    ),
    # ----- Hue
    _e(
        "CH-HUE-001",
        "Hue",
        "Hue-Bridge meldet einen Fehler",
        "Die Bridge hat eine Anfrage abgelehnt oder unerwartet geantwortet.",
        "Bridge-Adresse prüfen, Szenen/Räume neu laden. Der Hub versucht es kurz erneut.",
        True,
    ),
    _e(
        "CH-HUE-002",
        "Hue",
        "Zertifikat der Bridge nicht vertrauenswürdig",
        "Die verschlüsselte Verbindung zur Bridge konnte nicht geprüft werden.",
        "Unter Licht → Erweiterte Einstellungen die Zertifikatsdatei (CA) oder den Namen im Zertifikat eintragen.",
    ),
    _e(
        "CH-HUE-003",
        "Hue",
        "Zu viele Anfragen",
        "Die Bridge verlangt eine Pause (HTTP 429).",
        "Etwas warten. Der Hub hält die vorgegebene Wartezeit selbst ein.",
        True,
    ),
    _e(
        "CH-HUE-004",
        "Hue",
        "Taste an der Bridge nicht gedrückt",
        "Beim Koppeln wurde die runde Taste nicht gedrückt oder die 30 Sekunden sind um.",
        "Taste drücken und innerhalb von 30 Sekunden „Mit Bridge verbinden“ klicken.",
    ),
    _e(
        "CH-HUE-005",
        "Hue",
        "Hue nicht eingerichtet oder nicht gekoppelt",
        "Hue ist ausgeschaltet, die Adresse fehlt oder die Kopplung wurde noch nicht gemacht.",
        "Seite Licht: Bridge eintragen und koppeln. Aktionen davor werden kurz wiederholt, dann verworfen.",
        True,
    ),
    # ----- Smart Home
    _e(
        "CH-HOME-001",
        "Smart Home",
        "Smart-Home-Aktion fehlgeschlagen",
        "Eine Aktion für ein anderes System ist aus einem unbekannten Grund gescheitert.",
        "Seite Smart Home: Aktion mit „Jetzt senden (Test)“ prüfen, Meldungen auf der Seite System ansehen.",
    ),
    _e(
        "CH-HOME-002",
        "Smart Home",
        "Smart-Home-Gerät nicht erreichbar",
        "Es konnte keine Verbindung aufgebaut werden. Es wurde nichts gesendet.",
        "Adresse, Port, Netzwerk und ob das Gerät/der Broker läuft prüfen. Der Hub versucht es kurz erneut.",
        True,
    ),
    _e(
        "CH-HOME-003",
        "Smart Home",
        "Smart-Home-Gerät hat abgelehnt",
        "Das Ziel antwortete mit einem Fehler, lehnte Passwort/Token ab oder bestätigte nicht. Es wird nicht wiederholt, "
        "weil die Aktion schon angekommen sein könnte.",
        "Passwort/Token und Zieladresse prüfen. Beim Gerät nachsehen, ob die Aktion ausgeführt wurde.",
    ),
    _e(
        "CH-HOME-004",
        "Smart Home",
        "Smart-Home-Aktion nicht eingerichtet",
        "Die Aktion oder ihre Verbindung gibt es nicht (mehr) oder es fehlt das Token.",
        "Seite Smart Home: Aktion, Verbindung und Token prüfen.",
    ),
    # ----- Weboberfläche
    _e(
        "CH-WEB-000",
        "Weboberfläche",
        "Ungültige Anfrage",
        "Die Anfrage war nicht verständlich oder nicht erlaubt.",
        "Seite neu laden und es noch einmal versuchen.",
    ),
    _e(
        "CH-WEB-001",
        "Weboberfläche",
        "Seite nicht gefunden",
        "Diese Adresse gibt es nicht.",
        "Über die Navigation oben zur gewünschten Seite gehen.",
    ),
    _e(
        "CH-WEB-002",
        "Weboberfläche",
        "Aktion hier nicht erlaubt",
        "Diese Anfrage-Art gilt für diese Adresse nicht.",
        "Seite neu laden.",
    ),
    _e(
        "CH-WEB-003",
        "Weboberfläche",
        "Speichern ist ausgeschaltet",
        "Es ist kein Zugriffs-Token eingerichtet, die Oberfläche ist nur lesend.",
        "`OCTAPIHUB_WEB_TOKEN` in `octapihub.env` setzen und den Dienst neu starten.",
    ),
    _e(
        "CH-WEB-004",
        "Weboberfläche",
        "Zugriffs-Token stimmt nicht",
        "Das eingegebene Token passt nicht.",
        "Token aus `octapihub.env` erneut eingeben. Es wurde nichts geändert.",
    ),
    _e(
        "CH-WEB-005",
        "Weboberfläche",
        "Hue in diesem Modus nicht verfügbar",
        "Die Oberfläche läuft ohne Konfigurationsdatei (Demo).",
        "Mit `--config` starten.",
    ),
    _e(
        "CH-WEB-006",
        "Weboberfläche",
        "Unbekannte Aktion",
        "Die Schaltfläche gehört nicht zu dieser Seite.",
        "Seite neu laden.",
    ),
    _e(
        "CH-WEB-007",
        "Weboberfläche",
        "Diagnose-Paket nicht erstellbar",
        "Das ZIP konnte nicht erstellt werden.",
        "Speicherplatz prüfen und noch einmal versuchen.",
    ),
    _e(
        "CH-WEB-008",
        "Weboberfläche",
        "Token zu kurz oder Platzhalter",
        "Der Dienst startet nicht, weil das Token unsicher ist.",
        "Ein langes, zufälliges Token setzen (mindestens 12 Zeichen), siehe Installationsanleitung Abschnitt 6.",
    ),
    _e(
        "CH-WEB-009",
        "Weboberfläche",
        "Anzeige-Steuerung nicht verfügbar",
        "Die LED-Anzeige läuft nicht.",
        "Sense HAT und Anzeige in den Einstellungen prüfen.",
    ),
    _e(
        "CH-WEB-010",
        "Weboberfläche",
        "Ungültige Eingabe",
        "Ein Wert der Anzeige-Steuerung ist ungültig.",
        "Wert korrigieren.",
    ),
    _e(
        "CH-WEB-011",
        "Weboberfläche",
        "Ungültige Auswahl",
        "Die Auswahl gibt es nicht (mehr), zum Beispiel nach einer Änderung in einem anderen Tab.",
        "Seite neu laden und erneut wählen.",
    ),
    _e(
        "CH-WEB-012",
        "Weboberfläche",
        "Aktionssteuerung nicht aktiv",
        "Die Würfel-Steuerung läuft in diesem Modus nicht.",
        "Mit Konfigurationsdatei starten.",
    ),
    _e(
        "CH-WEB-013",
        "Weboberfläche",
        "Nichts ausgelöst",
        "Für diese Seite ist keine Aktion gespeichert oder die Aktionssteuerung läuft nicht.",
        "Aktion zuordnen und speichern, dann erneut auslösen.",
    ),
    _e(
        "CH-WEB-014",
        "Weboberfläche",
        "Musik nicht verfügbar",
        "Musik ist in diesem Modus nicht aktiv.",
        "Mit Konfigurationsdatei starten.",
    ),
    _e(
        "CH-WEB-015",
        "Weboberfläche",
        "Bluetooth-Suche nicht verfügbar",
        "In diesem Modus gibt es keine Suche.",
        "Mit Konfigurationsdatei starten.",
    ),
    _e(
        "CH-WEB-016",
        "Weboberfläche",
        "Würfel ohne Namen",
        "Ein neuer Würfel braucht einen Namen.",
        "Namen eintragen.",
    ),
    _e(
        "CH-WEB-017",
        "Weboberfläche",
        "Zu viele Würfel",
        "Es sind höchstens 3 Würfel möglich.",
        "Zuerst einen Würfel entfernen.",
    ),
    _e(
        "CH-WEB-018",
        "Weboberfläche",
        "Letzter Würfel",
        "Mindestens ein Würfel muss eingerichtet bleiben.",
        "Zuerst einen neuen Würfel hinzufügen.",
    ),
    _e(
        "CH-WEB-019",
        "Weboberfläche",
        "Smart Home nicht verfügbar",
        "Smart Home ist in diesem Modus nicht aktiv.",
        "Mit Konfigurationsdatei starten.",
    ),
    _e(
        "CH-WEB-020",
        "Weboberfläche",
        "Anfrage zu groß",
        "Die gesendeten Daten sind größer als erlaubt.",
        "Weniger Daten senden (zum Beispiel eine kleinere Export-Datei).",
    ),
    _e(
        "CH-WEB-500",
        "Weboberfläche",
        "Interner Fehler",
        "Bei der Bearbeitung ist ein unerwarteter Fehler aufgetreten. Er steht mit einer Kennung im Protokoll.",
        "Seite neu laden. Tritt es wieder auf: Diagnose-Paket speichern und die Kennung der Meldung angeben.",
    ),
)

CATALOG: dict[str, ErrorInfo] = {entry.code: entry for entry in _ENTRIES}


def lookup(code: str) -> ErrorInfo | None:
    """The catalogue entry for ``code`` (``None`` for an unknown code)."""
    return CATALOG.get(code)


def by_area() -> dict[str, list[ErrorInfo]]:
    """Entries grouped by area, in catalogue order."""
    grouped: dict[str, list[ErrorInfo]] = {}
    for entry in _ENTRIES:
        grouped.setdefault(entry.area, []).append(entry)
    return grouped
