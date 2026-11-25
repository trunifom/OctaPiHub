# Häufige Fragen

## Würfel
**Wie viele Würfel kann ich nutzen?** Bis zu drei, mit je neun belegbaren Zuständen (acht Seiten und die Halterung). Dazu kommen fünf Joystick-Richtungen.

**Ein Würfel löst nichts aus.** Seite *Würfel*: Ist er „Verbunden“? Ändert sich „zuletzt erkannte Seite“? Ist für die Seite eine Aktion gespeichert? Probieren Sie *Jetzt auslösen*. Klappt das, aber der Würfel nicht, liegt es an der Verbindung (trennen Sie ihn vom Smartphone). Klappt es auch dort nicht, zeigt die Meldung (System) den Grund.

**Wie koppele ich einen neuen Würfel?** Würfel kippen, *Würfel → Würfel verwalten → Nach Würfeln suchen*, Namen vergeben, *Hinzufügen*.

**Kann ich die Belegung auf einen zweiten Würfel übertragen?** Ja: *Sichern & Wiederherstellen* → „Ein einzelner Würfel“ exportieren und mit „anwenden auf“ importieren.

**Was soll ich der Halterung zuordnen?** Beim Musik-Würfel „Alle Wiedergaben stoppen“, beim Licht-Würfel nichts.

**Welche Seite hat welche Nummer?** Das zeigt die Seite *Würfel* beim Ausprobieren („zuletzt erkannte Seite“). Geben Sie jeder Seite dort einen Namen.

## Musik
**Die Musik startet nicht.** Seite *Musik*: „Musik verwenden“ an? Funktioniert *Jetzt abspielen (Test)*? Hat das Gerät den Zustand „Fehler“ (Code daneben)?

**Lässt sich Spotify abspielen?** Nein. Spotify erlaubt das nur in seinen eigenen Apps und Geräten. YouTube (auch YouTube Music), Radio-Adressen und eigene Dateien gehen.

**Zufall und Wiederholen?** Je Playlist und je Würfelseite einstellbar. Zufall wirkt lokal und bei YouTube über den Pi. YouTube-Playlists auf einem Google-Gerät lassen sich nicht mischen.

**Auf dem Google-Lautsprecher spielt Radio, aber keine Datei.** Der Dienst lauscht vermutlich nur auf `127.0.0.1`. `OCTAPIHUB_HOST` auf die LAN-Adresse setzen.

**Wo hinterlege ich meine Musik?** In `/srv/music` (nicht unter `/home`).

**Kann ich mehrere Lautsprecher gleichzeitig bespielen?** Nein: Es spielt immer nur ein Gerät.

## Licht und Smart Home
**Kann ich Licht dimmen und die Farbe wechseln?** Ja: Katalog „Licht: Steuern“ (heller, dunkler, nächste Farbe, nächste Szene) und „Farbe setzen“.

**Wie steuere ich Matter-Geräte?** Über Home Assistant: Gerät dort einbinden, dann auf *Smart Home* eine Home-Assistant-Aktion anlegen. Siehe [Smart Home](smarthome.md).

**Warum wiederholt der Hub eine abgelehnte Smart-Home-Aktion nicht?** Weil sie schon angekommen sein könnte. Ein zweites „Umschalten“ würde das Gerät zurückschalten.

**Funktioniert CoAP?** Nein. Nutzen Sie Webhook, MQTT oder Home Assistant.

## Oberfläche und Einstellungen
**Ich habe das Token vergessen.** Es steht in `/etc/octapihub/octapihub.env` auf dem Raspberry Pi (`OCTAPIHUB_WEB_TOKEN=`).

**Wie sichere ich alles?** Export-Datei (*Sichern & Wiederherstellen*) plus `tar` von `/etc/octapihub` und `/var/lib/octapihub`. Siehe Installation, Abschnitt 10.

**Was bedeutet das gelbe Banner oben?** Beim Start hat etwas nicht geklappt (z. B. ein Dienst konnte nicht starten, oder eine Sicherung der Einstellungen wird verwendet). Der Text nennt den Grund und den Code.

**Wie sehe ich Erklärungen zu den Feldern?** Auf das kleine **ⓘ** neben dem Feld klicken. Esc schließt.

**Ändert sich etwas, wenn ich Einstellungen speichere?** Sofort und ohne Neustart. Nur die Liste der Würfel verbindet die Würfel neu.

**Wo sind meine Messdaten?** In der Datenbank unter `/var/lib/octapihub/` (SQLite). Als Tabelle: *Sichern & Wiederherstellen → Messdaten (CSV)*.

## Betrieb
**Startet der Hub nach einem Stromausfall von selbst?** Ja, wenn der Dienst mit `systemctl enable` eingerichtet wurde (Installation, Abschnitt 7).

**Wie aktualisiere ich?** `git pull`, `pip install`, `systemctl restart` (Installation, Abschnitt 10). Alte Einstellungen werden beim Laden auf die neue Version gehoben.

**Wie viel Speicher braucht der Hub?** Der Dienst ist auf etwa 350 MB (hart 500 MB) begrenzt; mpv zählt mit.

**Wurde das auf echter Hardware getestet?** Nein, bisher nur mit Simulationen. Die Prüfliste in der Installation hilft, das nachzuholen.
