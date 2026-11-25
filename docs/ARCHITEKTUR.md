# OctaPiHub-Architektur

## Ziel und Status

OctaPiHub wird als lokaler, hardwareunabhängig testbarer Python-Dienst mit klaren
Grenzen zwischen Domänenregeln, Anwendungslogik und Hardware-/Netzwerkadaptern
entwickelt. Das ist eine Zielrichtung, keine Behauptung, dass alle Schichten oder
der Pi-Dienst bereits existieren.

Aktuell implementiert sind:

- `domain`: Würfel-Orientierungsereignisse, Baseline, Entprellung und Reconnect-Backoff.
- `core`: Fehlerklassen sowie versioniertes TOML-Laden, Validierung und atomare Speicherung.
- `application.plugins`: Plugin-Vertrag, Registry und Discovery über Python-Entry-Points.
- `application.ble`: hardwareunabhängiger BLE-Transportvertrag und GATT-Profile.
- `application.ble_runtime`: serialisierte Verbindungseinrichtung und je ein überwachter
  Worker pro konfiguriertem Würfel.
- `application.rules` und `application.outbox`: stabile Würfelseiten auf Plugin-Aktionen
  abbilden und Aktionen isoliert aus der persistenten Warteschlange ausführen.
- `infrastructure.ble`: Bleak/BlueZ-Adapter, Fake und Decoder der bekannten BLE-Nutzdaten.
- `infrastructure.hue`: lokaler CLIP-v2-Client und Plugin für Szenenliste/-Recall mit
  verifizierter TLS-Verbindung und begrenzter Anfragerate.
- `infrastructure.sqlite_outbox`: SQLite-WAL-Speicherung, FIFO je Würfel und Retry-Status.
- `application.sense_hat` definiert den typisierten Port für Messwerte, Fähigkeiten,
  Joystick und Matrix; `infrastructure.sense_hat` adaptiert die offizielle Bibliothek
  lazy, während `infrastructure.fake_sense_hat` Hardware-freie Tests ermöglicht.

Der BLE-Transport und die Laufzeit sind implementiert und lokal mit Fakes getestet.
Regelzuordnung, generische Outbox und Hue-Adapter sind lokal getestet. Der Hue-Adapter
wurde nicht gegen eine echte Bridge geprüft. (Die Aufzählung oben ist historisch; Kopplung, Musik, Composition Root, systemd-Dienst und
Weboberfläche sind inzwischen vorhanden, siehe die späteren Abschnitte.) Der Sense-HAT-Port und Hardwareadapter
sind mit einem Fake getestet, aber nicht auf echter Hardware verifiziert. Die
Fähigkeitserkennung meldet ohne erkannten Farbsensor absichtlich keine automatische
v1-Revision, da das Fehlen des Sensors keinen sicheren Revisionsnachweis darstellt.
Schema v3 enthält bereits validierte Sense-HAT-Einstellungen; daraus folgt kein
Hardware- oder Weboberflächennachweis. Die BLE-Laufzeit wurde nicht auf Raspberry Pi
oder echter Hardware verifiziert.

## Abhängigkeitsrichtung

```text
domain  <-  application  <-  infrastructure adapters
  ^              ^                    ^
  +--------------+--------------------+
                 core
```

- `domain` enthält reine Regeln und Modelle. Es importiert keine Hardware-, HTTP-,
  Datenbank- oder Webframeworks.
- `application` orchestriert Anwendungsfälle und definiert Verträge für austauschbare
  Adapter (Ports). Es importiert keine konkreten Adapter.
- `infrastructure` implementiert diese Verträge für BlueZ/BLE, Hue, Audio/Cast und
  Sense HAT. Hardwareadapter bleiben ersetzbar durch Fakes. Der BLE-Transportvertrag
  (`application.ble.DeviceTransport`, `TransportFactory`) enthält absichtlich keine
  GATT-Schreibmethode.
- Der BLE-Transport kapselt einen Verbindungszyklus. Sein Besitzer steuert Verbindung,
  Erkennung, Abonnement und Freigabe; nur der Besitzer orchestriert mehrere Cubes.
  `application.ble_runtime` serialisiert den Verbindungsaufbau und überwacht getrennte
  Würfel-Worker mit eigener Wiederverbindung.
- `core` stellt querschnittliche, stabile Grundtypen bereit, etwa Fehler und
  Konfigurationsvalidierung und -speicherung; es hängt nicht von den anderen Schichten ab.
- Der Composition Root (noch nicht implementiert) lädt Konfiguration und Plugins,
  erstellt Adapter und verbindet die Abhängigkeiten. Module erzeugen keine globalen
  Einzelinstanzen und greifen nicht selbst auf Hardware zu.

## Plugin-Schnittstelle

Ein Erweiterungspaket registriert eine Factory mit der Gruppe
`octapihub.plugins`. Beispiel für ein separates Paket:

```toml
[project.entry-points."octapihub.plugins"]
my_audio = "my_audio_plugin:build_plugin"
```

Die Factory ist parameterlos und liefert ein Objekt mit einem stabilen `kind` sowie
`async execute(action, parameters)`. Die Schnittstelle liegt in
`octapihub.application.plugins`; Plugins sollen öffentliche Anwendungsschnittstellen
verwenden und keine internen Details anderer Adapter importieren.

Die Registry wird durch Dependency Injection erzeugt. Sie weist doppelte oder
ungültige Kennungen zurück, löst fehlende Plugins mit einem stabilen Fehler aus und
lädt Entry-Points deterministisch. Ein Import- oder Factoryfehler wird nicht
verschluckt; der Start muss fehlschlagen, statt eine fehlende Funktion vorzutäuschen.
Die Discovery sammelt und prüft zunächst alle Instanzen und registriert sie danach
atomar, damit Fehler keine teilweise befüllte Registry zurücklassen.

Plugins sind gewöhnlicher Python-Code, keine isolierten Prozesse. Sie laufen mit den
Rechten des OctaPiHub-Dienstes und dürfen daher nur aus vertrauenswürdigen Paketen
installiert werden. Die Plugin-API ist ein Erweiterungspunkt, keine Sicherheitsgrenze.
Ein Plugin darf nicht selbst den BLE-Adapter oder einen konkurrierenden Würfel-Worker
besitzen.

## Konfigurationsspeicherung

`core.config.save_config` nimmt das typisierte Konfigurationsmodell entgegen und
validiert es vor der Serialisierung erneut gegen das unterstützte Schema. Die
TOML-Datei wird als UTF-8 in eine temporäre Datei im Zielverzeichnis geschrieben,
geflusht und synchronisiert; anschließend ersetzt `os.replace` die Zieldatei atomar.
Fehler vor dem Ersetzen werden als `ConfigurationError` mit erhaltener Ursache
gemeldet, und die vorhandene Konfiguration bleibt unangetastet. Temporäre Dateien
werden nach Schreib- oder Ersetzungsfehlern entfernt; wenn das nicht gelingt, wird
der Bereinigungsfehler protokolliert.

Schema v3 ergänzt die bisherigen Würfel-/Regelkonfigurationen um `sense_hat`.
Schema-v1- und v2-Dateien bleiben lesbar und werden beim nächsten Speichern zu v3
migriert. Die Hardwareversion ist `auto`, `v1` oder `v2`; `auto` ist für die spätere
Fähigkeitserkennung vorgesehen, während der manuelle Wert als Override dient.
Temperatur, Feuchtigkeit und Druck sind standardmäßig aktiviert; Orientierung,
Kompass, Gyroskop, Beschleunigung und Farbe sind vorbereitet, aber standardmäßig
deaktiviert. Jede Sensorgruppe hat ein eigenes Intervall, eine Verlaufseinstellung,
Anzeigeart (`hidden`, `value`, `chart`, `value_and_chart`) und Anzeigepräzision.
Die Präzision betrifft nur die spätere Darstellung, nicht gespeicherte Messwerte.
Verlauf (SQLite, standardmäßig 30 Tage), Temperaturkorrektur, Matrixrotation,
Low-Light-Modus, automatische Helligkeit sowie Joystick und v2-Farb-Sensorparameter
sind konfigurierbar. Adapter, Versions-/Fähigkeitserkennung, Datenhaltung und GUI
setzen diese Einstellungen in späteren Schritten um.

```toml
[sense_hat]
enabled = true
hardware_version = "auto"
temperature_correction_factor = 1.0
temperature_offset_c = 0.0

[sense_hat.sensors.temperature]
enabled = true
interval_seconds = 30
history_enabled = true
presentation = "value_and_chart"
precision = 1

[sense_hat.sensors.orientation]
enabled = false
interval_seconds = 30
history_enabled = true
presentation = "hidden"
precision = 2
```

## Regeln und Outbox

Schema v2 ergänzt `mappings`: jede Zuordnung benennt einen konfigurierten Würfel, eine
Seite 1–8 und eine geordnete Liste aus Plugin-Art, Aktionsname und TOML-Parametern.
Schema-v1-Dateien werden mit leerer Zuordnung weitergeladen und beim nächsten Speichern
als Schema v2 geschrieben. Unbekannte Würfel, doppelte Würfel-/Seitenpaare und ungültige
Aktionen werden vor dem Anwenden zurückgewiesen. Beim Erzeugen des Dispatchers wird
zusätzlich geprüft, dass jede konfigurierte Plugin-Art registriert ist.

```toml
[[mappings]]
cube = "Licht"
face = 2

[[mappings.actions]]
kind = "hue"
action = "scene.set"
parameters = { scene_id = "living-room" }
```

`RuleEngine` löst nur stabile Seitenwechsel auf; Baseline und Neutral-Seite lösen keine
Aktion aus. `OutboxDispatcher.enqueue_face_change` bietet denselben Einstieg für BLE,
Web und Joystick. Die SQLite-Outbox speichert jede Aktion als Schritt einer atomaren
Batch, arbeitet pro Würfel strikt FIFO ab, verwendet begrenztes exponentielles Backoff
und markiert abgelaufene Einträge. Ein Fehler oder Retry eines Würfels blockiert den
anderen Würfel nicht. Abgeschlossene und abgelaufene Einträge können gezielt bereinigt
werden.

Es darf nur einen aktiven Outbox-Eigentümerprozess geben. Die Ausführung ist
At-least-once: Wenn der Prozess nach einer externen Wirkung, aber vor dem Erfolgs-Commit
abbricht, kann die Aktion nach dem Neustart erneut ausgeführt werden. Plugins müssen
daher wiederholbare Aktionen bevorzugen. Mehrstufige Zuordnungen laufen der Reihe nach;
zeitbasierte Bedingungen, eine zusätzliche Aktions-Entprellung und konkrete
Hue-/Musik-Aktionen sowie die konfigurierbare Zustandswiederherstellung beim Start
folgen später.

## Regeln für neue Module

1. Zuerst eine kleine Schnittstelle an der Anwendungsgrenze festlegen; Hardwarezugriff
   bleibt im Adapter.
2. Fehler an Systemgrenzen in stabile `CH-...`-Fehler übersetzen und Ursachen erhalten.
   Keine pauschalen stillen Fallbacks.
3. Zeit, Netzwerk und Hardware über injizierbare Abhängigkeiten oder Fakes testbar halten.
4. Grenzen und Fehlerfälle testen, bevor eine Funktion als fertig dokumentiert oder
   committed markiert wird.
5. Nur implementierte Fähigkeiten als solche dokumentieren; Hardwaretests getrennt von
   lokalen Tests ausweisen.

## BLE-Vertrag und Fake

`application.ble.DeviceProfile` enthält ausschließlich entdeckte Characteristic-UUIDs und Eigenschaften,
keine Backend- oder GATT-Client-Objekte. Eine Gerätefähigkeit gilt erst dann als
Orientierungskanal, wenn die erwartete UUID `notify` oder `indicate` anbietet.
Unbekannte schreibbare Characteristics werden nicht verwendet.

`FakeDeviceTransport` bildet dieselbe Anwendungsschnittstelle ab und erlaubt
Tests von Notifications, Trennung, Capability-Fehlern und gezielten Transportfehlern
ohne Bluetooth. Der Fake beweist weder BlueZ-/Bleak-Kompatibilität noch Hardwareverhalten.

## BLE-Laufzeit und Bleak-Adapter

`application.ble_runtime.BleRuntime` besitzt einen Worker pro konfiguriertem Würfel.
Verbindung, GATT-Capability-Erkennung und Notify-Anmeldung werden über eine gemeinsame
Sperre serialisiert; danach können beide Verbindungen parallel aktiv bleiben. Ein
stiller Würfel gilt nicht als getrennt: Reconnect wird durch den Disconnect-Callback
oder den tatsächlichen Verbindungsstatus ausgelöst. Wiederholungen verwenden
begrenztes exponentielles Backoff mit Jitter. Unterstützt ein Gerät die erwartete
Notify-/Indicate-Characteristic nicht, wird es als nicht unterstützt markiert und
nicht endlos erneut verbunden.

Die Runtime begrenzt sowohl Benachrichtigungs- als auch Ereigniswarteschlangen,
entprellt Orientierungen und stellt Status ohne Bluetooth-Adresse bereit.
`infrastructure.ble.bleak_transport.BleakDeviceTransport` bindet den Vertrag an Bleak
und den konfigurierten BlueZ-Adapter. Tests dafür verwenden einen injizierten
Bleak-Fake; sie belegen weder Zugriff auf BlueZ noch einen erfolgreichen Betrieb mit
einem echten Würfel oder Raspberry Pi.

## Hardwarefreier End-to-End-Pfad

`tests/test_end_to_end.py` verbindet `FakeDeviceTransport`, die echte BLE-Laufzeit,
Regelauflösung, `SQLiteOutbox`, den echten Dispatcher und ein zustandsbehaftetes
`FakeHuePlugin`. Der Test prüft, dass Baseline-Ereignisse keine Aktion auslösen und
ein stabiler Seitenwechsel eine geordnete Szene-/Helligkeitsaktion bis zum erfolgreichen
Outbox-Abschluss führt. Das Fake-Plugin simuliert nur die Adaptergrenze; es spricht
weder mit einer Hue Bridge noch prüft es Hue-API-, Zertifikats- oder Geräteverhalten.

Laut Nutzerangabe wurde in dem separaten Projekt OctaLedger ein echter Würfel mit
Musikplayer erprobt. Das ist hilfreiche externe Praxiserfahrung, aber kein Testnachweis
für die OctaPiHub-Implementierung; insbesondere wurde keine reale Hue-Lichtsteuerung
bestätigt. OctaPiHub selbst wurde weiterhin nicht auf echter BLE-Hardware oder einem
Raspberry Pi verifiziert.

## Hue CLIP-v2-Adapter

`infrastructure.hue.HueBridgeClient` liest Szenen unter
`/clip/v2/resource/scene` und aktiviert eine Szene mit dem CLIP-v2-Recall-Aufruf.
`HueActionPlugin` stellt `scene.activate` für die Outbox bereit. Die Verbindung nutzt
HTTPS ab TLS 1.2, System-Vertrauensanker und optional eine zusätzlich konfigurierte
CA-Datei; die Hostnamenprüfung bleibt immer aktiv. Ist CA oder Hostname falsch, schlägt
der Aufruf mit `CH-HUE-003` fehl. Es gibt keinen unsicheren HTTP-, Redirect- oder
Zertifikatsprüfungs-Fallback.

Der Application Key wird zur Laufzeit injiziert und aus Objekt-Repräsentationen,
Fehlermeldungen und Logs herausgehalten; er gehört nicht in die TOML-Konfiguration.
Standardmäßig wird höchstens eine Anfrage alle 0,5 Sekunden gesendet; das Intervall wird
beim Erzeugen des Clients festgelegt. HTTP 429 wird als `CH-HUE-002` gemeldet; ein
numerischer oder HTTP-Datumswert aus `Retry-After` wird als Mindestwartezeit an die
generische Outbox weitergegeben. Der Adapter unterstützt derzeit nicht das Bridge-Pairing,
Dimm-/Lichtaktionen oder den Eventstream.

`tests/test_hue.py` prüft Zertifikatskette und Hostnamenprüfung an einem lokalen HTTPS-
Server sowie Szenenliste, Recall, Ratenbegrenzung, 429/Retry-After und Redirect-Ablehnung.
Das dazugehörige Zertifikat und der private Schlüssel unter `tests/fixtures/` sind
ausschließlich öffentliche Testmaterialien und dürfen nicht für reale Bridges verwendet
werden. Ein erfolgreicher Fake-Server-Test ist kein Nachweis für das tatsächlich von der
Bridge präsentierte Signify-Zertifikat, deren Hostnamen oder die Szenen-Recall-Anfrage
an einer realen Bridge.

## Sense-HAT-Messworker

`application/sense_hat_worker.py` startet je aktivierter und von der Hardware unterstützter
Sensorgruppe einen asyncio-Task mit dem konfigurierten Intervall; der blockierende Port
läuft in einem Thread. Fehler einer Gruppe werden pro Gruppe als Status (`error`, sichere
Nutzermeldung, Fehlerzähler) erfasst und beim nächsten Intervall erneut versucht; andere
Gruppen und der Sample-Sink bleiben unberührt. Nicht vorhandene Hardware (z. B. fehlender
Farbsensor) wird als `unavailable` markiert und nicht abgefragt. Samples enthalten den
unveränderten Rohwert; die Temperaturkorrektur (`roh * Faktor + Offset`) liegt separat als
`corrected_temperature_c` daneben, Anzeigepräzision wird hier nicht angewandt. Verlauf
und Weboberfläche hängen sich später über den Sample-Sink an.

## Sense-HAT-Verlauf

`SenseHatHistoryRecorder` (application) ist ein Sample-Sink, der nur Gruppen mit
aktivem Verlauf (`history.enabled` und `history_enabled` der Gruppe) puffert und in
Stapeln schreibt (Standard: 20 Samples oder 300 s), um SQLite-Commits und SD-Karten-
Schreibzugriffe zu reduzieren. Bei Speicherfehlern bleibt der begrenzte Puffer erhalten
(älteste Samples werden verworfen und gezählt); der Worker bleibt unberührt.
`SQLiteSenseHatHistory` (infrastructure, WAL, `synchronous=NORMAL`) speichert unrunde
Rohwerte mit UTC-Zeitstempel, Skalarwerte in einer Spalte, Vektoren/Orientierung als JSON.
Aufbewahrung (`retention_days`) wird höchstens stündlich per Löschung angewandt.
`query_series` liefert Zeitreihen, optional als Mittelwert je Zeitfenster für Graphen.
Beim Beenden muss der Dienst `flush()` aufrufen (Composition Root, noch offen); bei hartem
Stromverlust können die zuletzt gepufferten Samples fehlen.

## Sense-HAT-Dienst und Composition Root

`SenseHatService` (application) besitzt den Lebenszyklus: Port erzeugen, Farbsensor-
Parameter anwenden (nur bei aktivierter Farbgruppe), Verlaufsspeicher öffnen, Recorder und
Messworker starten; `stop()` beendet den Worker und schreibt gepufferte Samples
(`flush`). Fehlt Board oder Bibliothek, läuft der Dienst ohne Sensoren weiter
(`hardware_error`); ist die Verlaufsdatenbank unbenutzbar, wird weiter gemessen, aber
nicht gespeichert (`history_error`). `octapihub/composition.py` verdrahtet die
echten Adapter (`build_sense_hat_service`). Ein startbarer Hub-Prozess, der BLE-Runtime,
Regeln und diesen Dienst gemeinsam startet, fehlt noch. `set_imu_config` wird noch nicht
aus der Konfiguration angewandt.

## Weboberfläche

`octapihub/web` ist die Oberflächenschicht (FastAPI, Jinja2, kein Build-Schritt, keine
externen Fonts oder Skripte; die Seiten funktionieren offline im LAN).

- **Seiten:** Übersicht, Würfel (Verbindungsstatus je Würfel, zuletzt erkannte Seite, Zuordnung nur lesend; Adressen werden nie angezeigt), Sense HAT (Live-Karten, Verlaufsdiagramm), Einstellungen
  (alle Sense-HAT-Optionen). Licht, Musik und System fehlen und erscheinen auf der
  Übersicht ehrlich als „Noch nicht angebunden“.
- **Live-Daten:** Server-Sent Events (`/events`). Der Server rendert HTML-Fragmente und
  sendet sie nur bei Änderungen; das Browserskript pollt nicht. Das Diagramm lädt höchstens
  einmal pro Minute nach, wenn Daten eintreffen.
- **Präsentation:** `web/presenter.py` formatiert rein funktional (Rundung nur für die
  Anzeige, Rohwert und korrigierter Wert getrennt, Fehler als „Wert nicht verfügbar“,
  Hinweise mit Fehlercode und Abhilfe). `web/forms.py` übersetzt Formularfelder in die
  validierte Konfiguration und liefert deutsche Feldfehler.
- **Speichern:** Nur mit Zugriffs-Token (`OCTAPIHUB_WEB_TOKEN`, konstantzeitiger Vergleich,
  ohne Token nur lesend). `SettingsManager` validiert, schreibt atomar und startet den
  Sense-HAT-Dienst mit der neuen Konfiguration neu; ungültige Eingaben ändern nichts.
- **Sicherheit:** Content-Security-Policy ohne Inline-Skripte/-Styles, `no-store`,
  kein Token in Antworten. Der Server bindet standardmäßig an `127.0.0.1`.
- **Darstellung:** Hell/Dunkel/Automatisch und zwei Schriftgrößen (im Browser gespeichert),
  Tab-Leiste auf dem Handy, Zeitanzeige in Europe/Zurich.
- **Demo:** `python -m octapihub.web --demo` nutzt ein simuliertes Sense HAT.

Bekannte Grenzen: keine Rollen/Anmeldung außer Token, LAN-Beschränkung nicht erzwungen
(nur Bindeadresse), keine Barrierefreiheitsprüfung mit Hilfsmitteln, LED-Matrix- und
Joystick-Einstellungen werden gespeichert, aber noch nicht angewendet.

## Würfel-Dienst

`CubeService` (application) besitzt die Würfel-Pipeline: BLE-Laufzeit → Regeln → Outbox → Plugins. Ein Router-Task legt Ereignisse in die Outbox, je Würfel läuft ein eigener Dispatcher-Task. Ein fehlerhaftes Ereignis stoppt das Routing nicht, ein ausgefallener Dispatcher wird nur für seinen Würfel neu gestartet. Lässt sich die Pipeline nicht aufbauen (z. B. fehlendes Plugin für eine konfigurierte Aktion oder nicht beschreibbarer Datenordner), startet nichts, `error`/`error_code` erklären den Grund und der übrige Hub läuft weiter. `build_cube_service` in `composition.py` verdrahtet Bleak, die SQLite-Outbox (`data/state.sqlite3`) und die installierten Plugins. Bluetooth, Würfel und Plugins sind nur mit Fakes getestet; ein Hue-Plugin ist mangels Konfiguration (Bridge, Schlüssel) noch nicht installierbar.

## Hue-Konfiguration (Schema v4)

Schema v4 ergänzt den Abschnitt `[hue]` (`enabled`, `host`, `port`, `ca_file`, `tls_server_name`); v1–v3-Dateien bleiben lesbar und werden beim Speichern migriert. Der Application Key steht bewusst nie in der TOML-Datei (unbekannte Felder wie `application_key` werden abgelehnt), sondern in `secrets.env`. Hue ist standardmäßig ausgeschaltet; ist es eingeschaltet, ist ein gültiger Host Pflicht. Das Zertifikat der Bridge wird immer geprüft; `tls_server_name` ist für Bridges gedacht, deren Zertifikat nicht auf die IP-Adresse ausgestellt ist (nicht an echter Hardware geprüft).

## Hue-Anbindung (Dienst und Oberfläche)

- **`HueService`** (application) ist zugleich das Plugin `hue`. Es ist auch vor dem Koppeln registriert: Aktionen scheitern dann mit `CH-HUE-005` und die Outbox wiederholt sie bis zum Ablauf, sodass eine kurz vor dem Koppeln gemachte Würfelgeste danach noch wirkt (per Test belegt). Gekoppelt, getrennt und ausgeschaltet sind eigene Zustände.
- **Fehlerverhalten:** Kopplung ist serialisiert und liefert typisierte Fehler (Taste nicht gedrückt `CH-HUE-004`, Zertifikat `CH-HUE-003`, Bridge `CH-HUE-001`, Speicher `CH-SEC-001`); schlägt das Speichern des Schlüssels fehl, gilt nichts als gekoppelt. Unlesbare Secrets oder eine ungültige CA-Datei stoppen den Hub nicht, sondern werden als Status gemeldet; Reconfigure behebt das ohne Neustart. Die Szenenliste bleibt bei Fehlern als veralteter Stand sichtbar.
- **Geheimnisse:** Der Application Key liegt nur in `secrets.env` (0600, Datei mit offenen Rechten wird abgelehnt), nie in TOML, Status, Seiten oder Fehlermeldungen.
- **Seite Licht:** Bridge eintragen (Validierung über das Konfigurationsschema), Koppeln per Knopfdruck, Szenen laden, Szenen testen, Verbindung trennen (mit Bestätigung). Alle Änderungen brauchen das Token; ein Formular mit einem Token-Feld. Szenennamen werden nie als HTML ausgegeben.
- **Grenzen:** Nur gegen lokale TLS-Fakes getestet; Verhalten einer echten Bridge (Zertifikatsname, Kopplungsfenster, Ratenlimit) ist ungeprüft. Szenen-IDs müssen noch in der Konfigurationsdatei zugeordnet werden.

## Zuordnung bearbeiten (F-R1)

Auf der Seite Würfel hat jede der acht Seiten beider Würfel eine Auswahl „Keine Aktion“ oder eine geladene Hue-Szene. Speichern validiert (Formular und Konfigurationsschema), schreibt atomar und schaltet die Regeln **ohne Neuverbindung** der Würfel um (`OutboxDispatcher.update_rules`, `CubeService.update_config`). Lehnt die laufende Pipeline die Änderung ab (z. B. fehlendes Plugin), bleibt alles unverändert; scheitert das Schreiben der Datei, werden die laufenden Regeln zurückgesetzt, sodass Datei und Betrieb nie auseinanderlaufen. Eine neue Szene kann nur aus der geladenen Hue-Liste gewählt werden; bereits konfigurierte Szenen bleiben auch bei ausgefallener Bridge erhalten. Seiten mit mehreren oder anderen Aktionen sind gesperrt, bleiben unverändert und sind nur in der Konfigurationsdatei änderbar. Wurde die Pipeline wegen eines Fehlers nicht gestartet, versucht eine Zuordnungsänderung den Start erneut.

## System-Seite und Protokoll

- **Werte:** `SystemInfoReader` (infrastructure) liest Laufzeit, Auslastung, Speicher (`/proc`), CPU-Temperatur (`/sys`), Drosselung/Unterspannung (`vcgencmd get_throttled`, Unterscheidung „jetzt“ und „früher“) und freien Speicherplatz. Jede Quelle kann einzeln ausfallen; der Wert gilt dann als „Nicht verfügbar“ mit Erklärung (z. B. außerhalb eines Raspberry Pi), die Seite funktioniert trotzdem. Unplausible Werte (z. B. Temperatur außerhalb -40…150 °C) werden verworfen. Schwellen: Temperatur ab 70 °C Hinweis, ab 80 °C Achtung; Speicher ab 80/90 % belegt; Speicherplatz unter 15/5 % frei.
- **Protokoll (NF-7):** `Redactor` maskiert Zugangsdaten (Schlüssel, Token, Passwörter, Bearer) und kürzt MAC-Adressen; zusätzlich werden die tatsächlichen Werte aus `secrets.env` und das Web-Token überall entfernt. `RedactingFilter` wirkt auf die Konsolenausgabe, `LogRing` hält die letzten 500 Zeilen für die Oberfläche; von Ausnahmen wird nur der Typ, nie der Text ausgegeben. Uvicorn-Zugriffsprotokolle laufen über eigene Handler und enthalten keine Geheimnisse (Token nur im Formularkörper).
- **Grenzen:** Die Linux-Quellen wurden nur gegen Testdateien geprüft, nicht auf einem echten Raspberry Pi; die Seite lädt bei Bedarf neu („Aktualisieren“), nicht live.

## Diagnose-Paket (F-U8)

Die Seite System bietet ein ZIP mit `status.json` (Komponentenzustände, Fehlercodes, keine Adressen), `system.json`, `config.toml` und `logs.txt`. Es ist nur mit Token abrufbar und wird im Speicher erzeugt. Zugangsdaten und Token werden entfernt, Bluetooth-Adressen gekürzt, die Bridge-Adresse und der CA-Pfad ersetzt bzw. überall maskiert (auch innerhalb von Logzeilen); für die Konfiguration wird eine Kopie verwendet, die echte Datei und die laufende Konfiguration bleiben unverändert. Fehler beim Erzeugen werden mit Code gemeldet, ohne Details auszugeben. Auch dieses Paket sollte vor der Weitergabe kurz geprüft werden.

## Bis zu drei Würfel und Schema v5

Schema v5 erlaubt **1 bis 3 Würfel** mit beliebiger Rolle (`light`, `music`, `general`; die Rolle beschriftet nur, jede Rolle darf mehrfach vorkommen) und ergänzt die LED-Matrix-Einstellungen: `brightness` (5–100 %), `default_view`, `views` (aktivierte Ansichten in Joystick-Reihenfolge), `auto_cycle_seconds`, Nachtmodus (`night_enabled`, `night_start`, `night_end`, `night_mode` = `off` oder `dim`). Ältere Dateien (v1–v4) bleiben lesbar und erhalten Standardwerte. Die BLE-Laufzeit verbindet die Würfel nacheinander und hält sie gleichzeitig; Zuordnungseditor, Statuskarten und Zuordnung arbeiten mit beliebig vielen konfigurierten Würfeln.

## Akkustand der Würfel (F-W5)

Die BLE-Laufzeit liest nach dem Verbinden und danach alle 10 Minuten (`battery_poll_seconds`) das Standard-Merkmal *Battery Level* (`0x2A19`, ein Byte, 0–100 %), sofern die Entdeckung es mit Eigenschaft `read` meldet. Das Lesen ist Teil des Transportvertrags (`read_characteristic`, nur für als lesbar bestätigte Merkmale; es gibt weiterhin keine Schreibzugriffe). Ein Lesefehler, eine Zeitüberschreitung oder ein ungültiges Paket beenden die Verbindung nie: der Fehler wird am Würfel vermerkt, der letzte bekannte Wert bleibt sichtbar. Fehlt der Dienst, gilt der Würfel als „nicht abrufbar“. Die Oberfläche zeigt je Würfel Balken, Prozent und Hinweis (ab 20 % „Bald laden“, ab 10 % „Bitte jetzt laden“), warnt oben auf der Seite Würfel und auf der Übersicht; das Diagnose-Paket enthält die Werte.

**Ungeprüft:** Ob echte Timeular-/ZEI-Würfel das Standard-Merkmal tatsächlich anbieten und wie genau die Werte sind, ist an Hardware nicht verifiziert (Pflichtenheft F-W5: „Prüfen: lesbar?“). Der zuletzt gelesene Wert wird nicht über einen Neustart des Dienstes hinweg gespeichert.

## LED-Matrix-Anzeige und Joystick

**Schichten:** `application/matrix/` ist rein und ohne Hardware: `canvas` (8×8-Fläche, Farbfunktionen, 3×5-Schrift, Icons), `views` (21 Ansichten als reine Funktionen Zustand → Bild, robust gegen fehlende oder ungültige Werte, NaN/Inf zählen als fehlend), `controller` (Joystick-Zustandsmaschine), `overlays` (kurze Einblendungen und `AlertDetector`), `builder` (Anzeigezustand aus Würfeln, Hue, Sensoren, Host) und `service` (`MatrixDisplayService`). Die Ansichtennamen stehen einmal in `core.config.MATRIX_VIEW_IDS`; ein Import-Test erzwingt, dass Registry und Konfiguration übereinstimmen.

**Betrieb:** Ein einziger Task besitzt Matrix und Joystick (kein Zugriff aus BLE-Callbacks, F-D8), nutzt den Port des Sense-HAT-Dienstes und schreibt nur bei geänderten Bildern. Priorität: Anzeige aus > Hinweis (Verbindung/Akku/Fehler) > Aktionsfeedback > Info-Text > Ansicht. Helligkeit = Joystick-/Konfigwert × optionale Umgebungslichtanpassung (Farbsensor) × Nachtfenster (aus oder 5 %; ein Joystick-Druck weckt 30 s gedimmt). Hardwarefehler (I2C) erzeugen Status `Störung` mit Code, Backoff bis 30 s und automatische Erholung; der Hub läuft immer weiter (F-D7). Fehlt das Sense HAT, bleibt die Vorschau auf der Webseite funktionsfähig.

**Joystick:** ◀ ▶ Ansicht, ▲ ▼ Auswahl in der Ansicht (Würfel, Messwert, Palette ...) sonst Helligkeit, Drücken = Info-Laufschrift (in *Lampe*: Einstellmodus für Farbe/Helligkeit), langes Drücken = Anzeige aus/ein, Halten wiederholt nur hoch/runter (gedrosselt). Bei gedrehter Anzeige werden die Richtungen mit gedreht; **ob das mit der echten Hardware übereinstimmt, ist ungeprüft.** Optionales automatisches Weiterschalten pausiert nach jeder Eingabe eine Minute.

**Web:** Seite *Anzeige* mit Live-Vorschau (SSE `/events/display`, bis 4 Bilder/s), Galerie aller Ansichten, virtuellem Joystick und Helligkeit (alle Änderungen per Token über `POST /display/control`); Einstellungen für Ansichten, Startansicht, Auto-Weiterschalten, Nachtmodus, Helligkeit, Drehung. Änderungen werden ohne Neustart angewendet; wird die Startansicht geändert, gilt sie sofort.

**Grenzen:** Joystick-Änderungen (Helligkeit, Ansicht) sind flüchtig und werden nicht gespeichert; der Verlauf der Anzeige umfasst nur die letzten 8 Messwerte im Speicher; Hub-Aktionen löst der Joystick in der Ansicht *Steuerung* aus (siehe unten).

## Joystick als Aktionsquelle (F-R7)

Der Sense-HAT-Joystick ist eine eingebaute Aktionsquelle neben den Würfeln: Konfiguration und Regeln kennen den reservierten Namen `Joystick` (kein Würfel darf so heißen) mit **5 „Seiten“**: 1 hoch, 2 rechts, 3 runter, 4 links, 5 Mitte. Zuordnungen, Regelwerk, Outbox (eigene FIFO-Schlange und eigener Dispatcher-Task) und Plugins sind dieselben wie bei Würfeln; Aktionen werden wiederholt, wenn z. B. die Bridge nicht antwortet.

**Bedienung:** In der LED-Ansicht **Steuerung** (`control`; unter Einstellungen aktivieren) führt ein *kurzer Druck* die Aktion der Richtung aus (beim Loslassen), *links/rechts lang halten* wechselt die Ansicht, langes Drücken der Mitte schaltet weiter die Anzeige aus/ein. In allen anderen Ansichten bleibt die Navigation unverändert, so gibt es keinen Konflikt. Die Ansicht zeigt, welche Richtungen belegt sind; nach dem Druck erscheint kurz ein Pfeil (Aktion eingereiht), ein graues Kreuz (nichts eingerichtet) und danach wie bei Würfeln Häkchen oder Kreuz.

**Web:** Der Zuordnungseditor hat einen Joystick-Block mit fünf Feldern und zeigt, ob der Joystick einsatzbereit ist (Anzeige aktiv, Joystick eingeschaltet, Ansicht aktiviert). Der Knopf **„Jetzt auslösen“** führt die gespeicherte Aktion einer Würfelseite oder Joystick-Richtung sofort aus (`POST /cubes/trigger`, nur mit Token): Joystick und Weboberfläche lösen so dieselben Aktionen aus.

**Grenzen:** Der Joystick wird vom Anzeige-Dienst gelesen; ist die LED-Anzeige ausgeschaltet, funktioniert er nicht. Nicht an echter Hardware geprüft.

## Musik (F-M)

**Schichten.** `application/music/` ist rein und ohne Netzwerk/Prozesse: `sources` (`parse_source` erkennt LOCAL, URL, YOUTUBE und SPOTIFY und weist `..`, UNC-Pfade, Zugangsdaten im Link und fehlerhafte Links ab), `queue` (`play_order`, `following`, `preceding`: Mischen, Wiederholen, Ende der Liste), `models` (Protokolle `MusicPlayer`, `PlayerFactory`, `TrackResolver` und Datentypen) und `service` (`MusicService`). `infrastructure/music/` enthält die Adapter: `library` (`LibraryResolver`: Ordner, Dateien, m3u/pls, **nur innerhalb des Musikordners**), `media` (`MediaRegistry` mit zufälligen, ablaufenden Tokens und `MediaUrls`, die LAN-Adresse), `mpv_player` (mpv per JSON-IPC auf einem Unix-Socket, Bluetooth-Verbindung per `bluetoothctl`), `cast_player` (pychromecast hinter einem `CastBackend`-Protokoll, eigene Warteschlange mit Monitor-Task), `players` (`DefaultPlayerFactory` wählt je Zielart) und `fake` (für Tests und Demo).

**Aktionsplugin.** `MusicService` ist das Plugin `music` mit den Aktionen `play`, `pause`, `resume`, `toggle`, `stop`, `stop.all`, `next`, `previous`, `volume.set`, `volume.up`, `volume.down`. Würfel, Joystick und Weboberfläche lösen sie über denselben Weg (Regeln → Outbox → Plugin) aus; es gibt keinen Sondermodus für Musik. Die **Halterung** ist Seite 0 und wird wie eine Seite zugeordnet; die Baseline löst nie aus.

**Zielauswahl und Exklusivität.** Ziel = ausdrücklich angegeben > Ziel der Playlist > zuletzt verwendet > das einzige vorhandene. Startet Musik auf einem Ziel, werden aktive andere Ziele gestoppt. Pause/Stopp ohne Ziel wirken nur auf aktive Ziele. Je Ziel gibt es ein eigenes `asyncio.Lock`, jede Player-Operation hat ein Zeitlimit von 20 s.

**Fehlerbehandlung.** Typisierte Fehler: `MusicError` (CH-MUS-001, wiederholbar), `MusicTargetError` (002, Gerät antwortet nicht, wiederholbar), `MusicSourceError` (003, Quelle ungültig, **nicht** wiederholbar), `MusicNotConfiguredError` (004, nicht wiederholbar) und `MusicUnsupportedError` (005, fehlendes Paket oder Betriebssystem, nicht wiederholbar). Die Outbox lässt Fehler mit `retryable = False` sofort ablaufen und wiederholt nur vorübergehende. Für Musik gilt eine kurze Gültigkeit (`ttl_by_kind`, 60 s), damit eine Aktion nicht Minuten später plötzlich Musik startet. Der Status des Dienstes (`MusicStatus`) zeigt Zustand, Titel, Lautstärke und letzten Fehler je Ziel; Fehler eines Ziels beeinflussen andere nicht.

**Dateien für Cast-Geräte.** Ein Google-Lautsprecher kann keine lokalen Pfade lesen. Für Dateien registriert `MediaRegistry` einen unratbaren Token mit Ablaufzeit; `GET /media/{token}` liefert die Datei (Bereichsanfragen unterstützt) und prüft bei **jeder** Anfrage erneut, dass die Datei noch im Musikordner liegt. Die Route braucht kein Token der Oberfläche, weil der Link selbst der Zugriffsschutz ist; sie liefert nur Audiodateien aus dem Musikordner. Der Dienst muss dafür im LAN erreichbar sein.

**Web.** Seite *Musik* (`web/music_routes.py`, `web/music_forms.py`): Geräte und Playlists in einem Formular, pro Zeile validiert (alle Fehler gleichzeitig, deutsche Meldungen am Feld), gespeichert über `SettingsManager.apply_music` (atomar, dann live angewendet). Zusätzlich Geräte-/Audiosuche, Statusaktualisierung, Steuerungstasten je Gerät und Testwiedergabe. Der Zuordnungseditor bietet je Seite einen Aktionskatalog (Hue-Szene, Playlist abspielen, Playlist starten/pausieren, Steuern); Werte `music:play:<id>`, `music:toggle:<id>`, `music:<aktion>`, Hue-Szenen bleiben unverändert die rohe Szenen-ID.

**LED.** Die Ansicht `music` zeigt den Zustand als Symbol und die Lautstärke als Balken; Daten stammen aus `DisplayStateBuilder`, die Anzeige scheitert nie an der Musik.

**Grenzen.** Nichts davon wurde mit echtem mpv, echten Cast-Geräten oder einer Bluetooth-Box geprüft. Spotify ist nicht abspielbar. YouTube-Playlists ohne Video lassen sich auf Cast nicht starten. Verzögerungen (z. B. „nach 2 s“) sind in der Zuordnung nicht möglich.

## Sichern, Export und Import (F-K3)

`application/backup.py` ist rein: `build_bundle` erzeugt ein JSON-Bündel (`format = octapihub-export`, `version`) mit den Bereichen `music`, `playlists`, `mappings`, `sense_hat` und `hue`; Würfelliste, Bluetooth-Adapter, Pfad der Verlaufsdatenbank und Schlüssel sind absichtlich **nicht** enthalten. `parse_bundle` prüft Größe (512 KB), JSON, Format und Version; `prepare_section` wendet einen Bereich validiert auf die **aktuelle** Konfiguration an (z. B. müssen Zuordnungen zu vorhandenen Würfeln passen; Playlists werden nach Kennung zusammengeführt, unbekannte Geräte entfernt), `preview_bundle` fasst das für die Oberfläche zusammen.

`SettingsManager.apply_bundle` übernimmt gewählte Bereiche **alles oder nichts**: zuerst Validierung aller Bereiche, dann Sicherungskopie `octapihub.toml.bak-<Zeit>` (die neuesten 5 bleiben; scheitert sie, wird nichts importiert), Aktualisierung der Zuordnung im laufenden Dienst, atomares Schreiben (mit Rücksetzen bei Fehler) und zuletzt das Anwenden von Sense HAT, Hue und Musik. Schlägt ein Anwenden zur Laufzeit fehl, bleibt die Datei gespeichert und die Oberfläche empfiehlt einen Neustart.

**Web** (`web/backup_routes.py`): Export und Import per POST nur mit Token, Import in den Schritten *prüfen* und *übernehmen* (Datei wird im Browser gelesen und in ein Textfeld übernommen, kein Datei-Upload nötig; Formulargrenze 640 KB). Der Sensor-CSV-Export (`GET /export/sensor-data.csv`, Mittelwerte oder bis 24 h Rohwerte, Temperatur mit Rohwert und korrigiertem Wert, UTF-8 mit BOM) liest über denselben Weg wie die Diagramme; der Log-Download redigiert erneut.

## Protokollierung

`core/logging_support.py`: `Redactor` maskiert Zugangsdaten, Token, Bearer-Werte und kürzt MAC-Adressen; `RedactingFilter` gilt für jeden Handler. Handler: Konsole (journald), `LogRing` (die letzten 500 Zeilen für die Seite System und den Log-Download) und eine **rotierende Datei** (`create_file_handler`, Standard `<data-dir>/logs/octapihub.log`, 1 MB × 5 Rotationen, UTF-8). Ein nicht beschreibbarer Ordner kostet nur die Datei, nie den Start. Ausführlichkeit: `--log-level` oder `OCTAPIHUB_LOG_LEVEL`; Ausnahmetexte werden nicht protokolliert, nur der Typ.

## Lichtsteuerung (Hue-Gruppen)

`application/hue_lights.py` ist rein: Farbpalette (Weißtöne als Mired, Farben als CIE-xy), Befehle (`light.on/off/toggle/brighter/darker/brightness/color`, `light.color.next`, `scene.next/previous`), Wertprüfung mit deutschen Meldungen und Durchschalt-Logik. `HueBridgeClient` liest Räume, Zonen und das ganze Haus (`room`, `zone`, `bridge_home`) mit ihrer `grouped_light`-Ressource, sendet Befehle per `PUT grouped_light/{id}` und fragt den Ein-Zustand ab; Szenen tragen ihre Raum-Kennung. `HueService` löst die Gruppe auf (Standard: ganzes Haus, sonst nur bei genau einer Gruppe eindeutig), merkt sich die Position beim Durchschalten je Gruppe im Speicher (nach Neustart wieder von vorn) und meldet Fehler wie bei Szenen. Der Gruppenabruf bei `refresh_scenes` ist tolerant: schlägt er fehl, bleiben die Szenen gültig. Hue-Aktionen laufen in der Outbox mit 120 s Gültigkeit.

## Würfel-Verwaltung, Namen und Farben (Schema v7)

Schema v7 ergänzt `[[face_labels]]` (Würfel, Seite, Name, Farbe `#rrggbb`); v1–v6 werden migriert, ein v6-File darf den neuen Abschnitt nicht enthalten. `with_cubes_table` ersetzt die Würfelliste validiert und entfernt Zuordnungen und Namen entfernter Würfel. `application/ble_scan.py` definiert den Scanner-Port (`FoundDevice`, Namenshinweis auf Würfel), `BleakDeviceScanner` implementiert ihn, `FakeScanner` ersetzt ihn in Tests und Demo. `CubeService.reconfigure` startet die Verbindungen mit der neuen Liste neu; `SettingsManager.apply_cubes` sichert die Datei vorher, speichert atomar und startet neu. `apply_mappings` speichert Zuordnung und Namen/Farben gemeinsam. `web/face_detail.py` erzeugt die Klartext-Details je Seite. Farben sind CSS-Klassen (keine Inline-Styles wegen der CSP). Die aktive Seite wird im Browser anhand der Live-Daten (`data-last-face`) markiert. Export/Import kennt zusätzlich `labels` und `cube` (ein Würfel samt Namen/Farben, Ziel beim Import wählbar).

## Smart Home (Webhook, MQTT, Home Assistant)

`application/smarthome.py` ist das Plugin `smarthome` mit der einen Aktion `trigger` (Parameter `id` einer konfigurierten Aktion). Konfiguration: `[smarthome]` mit `connections` (MQTT-Broker oder Home Assistant: Kennung, Name, Art, Adresse, TLS, Benutzername) und `actions` (Art `http`, `mqtt` oder `homeassistant` mit je eigenen Feldern); Validierung je Art (Web-Adresse nur http(s) ohne Zugangsdaten, MQTT-Thema ohne `+`/`#`/`$`, Dienst `bereich.dienst`, JSON-Objekt als Zusatzdaten, Verbindungsart passend zur Aktion). Passwort bzw. Token einer Verbindung liegt unter `SMARTHOME_<ID>` in `secrets.env` (`SettingsManager.apply_smarthome` schreibt und löscht sie; sie sind nie Teil von Konfiguration, Export, Protokoll oder Diagnose).

`infrastructure/smarthome/http_client.py`: baut zuerst die Verbindung auf (Fehler dort = nichts gesendet = `SmartHomeConnectionError`, wiederholbar), sendet dann und liest nur die Statuszeile (höchstens 4 KB). Weiterleitungen werden nie verfolgt, Proxys ignoriert, Link-lokale Adressen abgelehnt, 401/403 und ≥ 300 sind `SmartHomeRejectedError`; ein Fehler **nach** dem Senden ist ebenfalls nicht wiederholbar (Ergebnis unbekannt). `mqtt_client.py` spricht MQTT 3.1.1 selbst (CONNECT, PUBLISH QoS 0/1, DISCONNECT, TLS) mit hartem Zeitlimit je Schritt, ohne Bibliothek. **Wiederholungsregel:** nur sicher nicht Zugestelltes wird wiederholt (`retryable = True` nur bei `CH-HOME-002`), damit ein „Umschalten“ nicht doppelt wirkt. `SmartHomeService` merkt sich je Aktion das letzte Ergebnis (`ActionResult`) für die Oberfläche. **Matter** wird über Home Assistant erreicht (Dienstaufruf `POST /api/services/<bereich>/<dienst>` mit Bearer-Token); CoAP ist nicht implementiert. Zuordnungswerte im Editor: `home:<Aktions-Id>`.

## Fehlerbehandlung und Ausfallsicherheit

- **Fehlerklassen:** `OctaPiHubError` mit stabilem Code (`CH-<BEREICH>-<NNN>`), deutscher Nutzermeldung und `retryable`. Bereiche: CFG, BLE, SENSE, DB, SEC, MUS, PLUG, HUE, HOME, WEB.
- **Fehlerkatalog** (`core/error_catalog.py`): zu jedem Code Titel, Bedeutung, Abhilfe, „wird wiederholt“. Er speist die Oberfläche (*Hilfe → Fehlercodes*, Verweis von der Fehlerseite) und ein Test erzwingt Vollständigkeit (jeder `code = "…"` in `errors.py` und jeder im Quelltext verwendete `CH-…`-Code muss im Katalog stehen; im Katalog genannte Wiederholbarkeit entspricht der Klasse).
- **Isolierung beim Start:** `_guarded_start` startet Sense HAT, Hue, LED-Anzeige und Würfel einzeln; eine Ausnahme wird protokolliert und als Startwarnung (gelbes Banner, `WebBackend.startup_warnings`) gezeigt, der Rest läuft weiter.
- **Globale Web-Handler:** `OctaPiHubError` ohne eigenen Fang → Seite mit dem eigenen Code; jede andere Ausnahme → `CH-WEB-500` mit achtstelliger Kennung im Protokoll (nur Typ, Pfad, Methode – nie der Ausnahmetext); API-/SSE-Pfade liefern JSON; scheitert sogar die Fehlerseite, kommt Klartext. 413 (zu große Eingabe) → `CH-WEB-020`.
- **Konfigurations-Fallback** (`core/resilience.py`): `load_with_fallback` nimmt bei unbrauchbarer Datei die neueste **gültige** Sicherung `<name>.bak-<Zeit>` (die defekte Datei bleibt unverändert) und meldet das als Startwarnung; ohne Sicherung bleibt es beim Fehler und Rückgabewert 2. `--check` prüft Datei, Zugangsdaten und Token ohne Start (0/1/2).
- **Nicht abgefangene Fehler:** `sys.excepthook`, `threading.excepthook` und ein asyncio-Handler protokollieren nur den Typ.
- **Gesundheit:** `/healthz` fasst Sense HAT, Würfel (verbunden/konfiguriert), Hue, Musik (Fehlercodes je Gerät) und Smart Home (letzte Fehler) zusammen: `ok` oder `degraded`.
- **Protokolle:** `RedactingFilter` maskiert Zugangsdaten und kürzt Adressen für Konsole, Ring und Datei; Ausnahmetexte werden nicht ausgegeben (`exc_info` wird geleert, nur der Typ bleibt).
- **Aktionen:** Warteschlange je Würfel (SQLite WAL), Wiederholung mit Backoff und Jitter, Ablauf (Standard 15 min; Musik 60 s, Licht und Smart Home 120 s), nicht wiederholbare Fehler laufen sofort ab.

## Eingebaute Hilfe und Feldhilfe

- **Dokumente:** `web/help/*.md` (Paketdaten) werden von `web/markdown.py` – einem kleinen, sicheren Renderer: erst HTML-Escape, nur http(s)/relative/Anker-Links, Tabellen, Listen, Code, Zitate – zu `/help/<slug>` mit Inhaltsverzeichnis gerendert; `/help/errors` rendert den Fehlerkatalog; die Suche (`/help?q=`) durchsucht Dokumente und Codes (Text-Snippets, HTML-escaped). `deploy/INSTALL.md` und `help/installation.md` sind identisch (Test).
- **Feldhilfe:** `web/field_help.py` ergänzt nach dem Rendern jedes Eingabefeld (Label mit `for="f-…"`, Schalter, Häkchen) um einen ⓘ-Button; der Text kommt aus `web/field_help_texts.py` (Schlüssel mit Platzhaltern für Zeilen, Adressen und Sensorgruppen). Ein Test rendert alle Seiten und schlägt fehl, wenn ein Feld keinen Text hat. Der Button sitzt im Label, der Text ist ein `role="note"`-Element (`hidden`, per Klick/Esc umschaltbar, `aria-expanded`).
