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
wurde nicht gegen eine echte Bridge geprüft; Hue-Kopplung/Key-Verwaltung, Audio-Aktionen,
ein startbarer Systemdienst/Composition Root, und
Weboberfläche sind noch nicht implementiert. Der Sense-HAT-Port und Hardwareadapter
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