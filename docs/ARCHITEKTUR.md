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
- `infrastructure.sqlite_outbox`: SQLite-WAL-Speicherung, FIFO je Würfel und Retry-Status.

Der BLE-Transport und die Laufzeit sind implementiert und lokal mit Fakes getestet.
Regelzuordnung und generische Outbox sind lokal getestet; konkrete Hue-/Audio-Aktionsadapter,
ein startbarer Systemdienst/Composition Root, Sense HAT und Weboberfläche sind noch
nicht implementiert. Die BLE-Laufzeit wurde nicht auf Raspberry Pi oder echter
Hardware verifiziert.

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
