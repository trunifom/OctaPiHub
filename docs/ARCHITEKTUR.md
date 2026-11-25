# OctaPiHub-Architektur

## Ziel und Status

OctaPiHub wird als lokaler, hardwareunabhängig testbarer Python-Dienst mit klaren
Grenzen zwischen Domänenregeln, Anwendungslogik und Hardware-/Netzwerkadaptern
entwickelt. Das ist eine Zielrichtung, keine Behauptung, dass alle Schichten oder
der Pi-Dienst bereits existieren.

Aktuell implementiert sind:

- `domain`: Würfel-Orientierungsereignisse, Baseline, Entprellung und Reconnect-Backoff.
- `core`: Fehlerklassen sowie versioniertes TOML-Laden und dessen Validierung.
- `application.plugins`: Plugin-Vertrag, Registry und Discovery über Python-Entry-Points.
- `infrastructure.ble`: hardwareunabhängiger BLE-Transportvertrag, Fake und Decoder
  der bekannten BLE-Nutzdaten.

Der produktive Bleak-Transport, Laufzeit/Prozesssteuerung, Regeln/Aktionen, Hue,
Audio/Cast, Sense HAT, Weboberfläche und dauerhafte Konfigurationsspeicherung sind
noch nicht implementiert. Sie gelten erst nach eigenen Tests und einem eigenen Commit
als fertig.

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
  Adapter. Es importiert keine konkreten Adapter.
- `infrastructure` implementiert diese Verträge für BlueZ/BLE, Hue, Audio/Cast und
  Sense HAT. Hardwareadapter bleiben ersetzbar durch Fakes. Der BLE-Transportvertrag
  (`DeviceTransport`, `TransportFactory`) enthält absichtlich keine GATT-Schreibmethode.
- Der BLE-Transport kapselt einen Verbindungszyklus. Sein Besitzer steuert Verbindung,
  Erkennung, Abonnement und Freigabe; nur der Besitzer orchestriert mehrere Cubes.
  Detaillierte Wiederverbindung und die globale Serialisierung folgen in der BLE-Runtime.
- `core` stellt querschnittliche, stabile Grundtypen bereit, etwa Fehler und
  Konfigurationsvalidierung; es hängt nicht von den anderen Schichten ab.
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

`DeviceProfile` enthält ausschließlich entdeckte Characteristic-UUIDs und Eigenschaften,
keine Backend- oder GATT-Client-Objekte. Eine Gerätefähigkeit gilt erst dann als
Orientierungskanal, wenn die erwartete UUID `notify` oder `indicate` anbietet.
Unbekannte schreibbare Characteristics werden nicht verwendet.

`FakeDeviceTransport` bildet die gleiche öffentliche Transportoberfläche ab und erlaubt
Tests von Notifications, Trennung, Capability-Fehlern und gezielten Transportfehlern
ohne Bluetooth. Der Fake beweist weder BlueZ-/Bleak-Kompatibilität noch Hardwareverhalten.
