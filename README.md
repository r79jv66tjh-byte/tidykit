# TidyKit

Aufräumen & Ordnung für Windows – ein Tool mit vier Werkzeugen und einem gemeinsamen Verlauf:

| Tab | Was es tut |
|---|---|
| **Reste-Finder** | Findet leere Ordner und Reste deinstallierter Programme in `AppData` (Roaming/Local/LocalLow), `ProgramData`, `Program Files` und `Program Files (x86)`. Außerdem: leere Ordner in einem beliebigen Ordner suchen. |
| **Autostart** | Zeigt alles, was beim Windows-Start läuft (Registry-Run-Keys, Autostart-Ordner, Aufgabenplanung), bewertet es und schaltet es ein/aus. |
| **Umbenennen** | Massen-Umbenenner mit Regeln (Suchen/Ersetzen, Regex, Muster/Nummerierung/Datum, Groß/Klein, Leerzeichen) und Live-Vorschau. |
| **Ordner-Vergleich** | Vergleicht zwei Ordner (Größe/Datum, optional per SHA-256), zeigt Unterschiede und gleicht gezielt oder per Vorschlag ab (A→B, B→A, beidseitig). |
| **Verlauf / Rückgängig** | Jede Änderung wird protokolliert und kann rückgängig gemacht werden. |

## Sicherheitsregeln

- **Nichts ohne Vorschau:** Vor jeder Änderung zeigt ein Dialog genau, was passieren wird.
- **Löschen nur in den Papierkorb:** Laufwerke ohne Papierkorb (z. B. Netzlaufwerke) werden verweigert, statt endgültig zu löschen.
- **Alles rückgängig machbar:** Gelöschtes wird gezielt aus dem Papierkorb an den Originalort zurückgelegt, Umbenennungen zurückbenannt, beim Ordner-Abgleich überschriebene Dateien vorher in den Papierkorb gelegt und beim Rückgängigmachen wiederhergestellt.
- **Systemänderungen nur nach Bestätigung, alter Zustand gesichert:** Autostart-Einträge werden wie im Task-Manager über `…\Explorer\StartupApproved` deaktiviert – der eigentliche Eintrag bleibt unverändert. Der vorherige Wert landet im Verlauf und zusätzlich als JSON in `%LOCALAPPDATA%\TidyKit\sicherungen`.

## Starten

Voraussetzung: Python 3.11+ (getestet mit 3.13) – keine Zusatzpakete nötig.

- Doppelklick auf `TidyKit.pyw`, oder
- im Projektordner:

```bash
python -m tidykit
```

Ohne Adminrechte lassen sich nur Benutzer-Einträge schalten und Ordner im eigenen Profil löschen. Für `Program Files`, `ProgramData` und System-Autostart: Button **„Als Administrator neu starten“**.

## Wie der Reste-Finder bewertet

Ein Ordner wird nur vorgeschlagen, wenn er zu nichts passt, was noch da ist: installierte Programme (Uninstall-Registry, Name/Hersteller/Installationspfad), Steam- und Epic-Spiele (inkl. interner Projektordner wie `TslGame`), Dienste, laufende Prozesse und Autostart-Ziele. Windows-/Microsoft-Ordner sind geschützt.

| Sicherheit | Bedeutung |
|---|---|
| **hoch** | leerer Ordner, oder Rest in `AppData`, der seit ≥ 180 Tagen unverändert ist |
| **mittel** | 30–180 Tage alt, enthält vermutlich Spielstände/Profile, oder liegt unter `Program Files`/`ProgramData` |
| **niedrig** | kürzlich geändert, GUID-Ordner oder geteilte Komponente (VST-Plugins, Anti-Cheat, Laufzeiten, Lizenzen) |

Es bleiben Vorschläge – vor dem Löschen kurz prüfen („Im Explorer zeigen“).

## Muster-Platzhalter (Umbenennen)

`{name}` aktueller Name · `{n}` laufende Nummer · `{n:3}` dreistellig (001) · `{datum}` Änderungsdatum (`2026-11-12`) · `{datum:%Y%m%d}` eigenes Format · `{ordner}` Name des Ordners · `{ext}` Endung.
Regex-Ersatz akzeptiert `\1` und `$1`. Konflikte (doppelte Namen, vorhandene Dateien, ungültige Zeichen) werden rot markiert und blockieren das Ausführen. Tauschen (`a`↔`b`) funktioniert dank Zwischennamen.

## Claude-Schnittstelle

TidyKit lässt sich von Claude über **ToolHub** (MCP-Server) bedienen. Dafür gibt es:

- **`cli.py`** – Kommandozeile, jeder Befehl gibt JSON auf stdout aus (`{"ok": true, "command": …, …}`). Bei Fehlern: Meldung auf stderr und Exit-Code ≠ 0.
- **`toolhub.json`** – beschreibt die Befehle für ToolHub (`run` relativ zum Projektordner, `python` = aktueller Interpreter).

| Befehl | readonly | Zweck |
|---|---|---|
| `leftovers` | ja | Programmreste und leere Ordner finden (`--root`, `--min-confidence`, `--limit`) |
| `empty-dirs` | ja | leere Ordner unterhalb von `--folder` |
| `startup-list` | ja | Autostart-Einträge mit Status und Bewertung |
| `compare` | ja | zwei Ordner vergleichen (`--a`, `--b`, `--hash`) |
| `history` | ja | Verlauf aller Änderungen (GUI und CLI) mit IDs |
| `trash` | nein | Pfade in den Papierkorb (System-/Hauptordner werden verweigert) |
| `startup-set` | nein | Autostart-Eintrag `--state on/off` (alter Zustand wird gesichert) |
| `rename` | nein | Umbenennen nach Regeln (`--find/--replace`, `--regex`, `--case`, `--template`, `--trim` oder `--rules` als JSON) |
| `sync` | nein | Ordner abgleichen (`--mode a2b/b2a/both`, `--delete-extra`) |
| `undo` | nein | Änderung per `--id` rückgängig machen |

Alle schreibenden Befehle unterstützen **`--dry-run`** (nur Vorschau als JSON) und tragen sich in denselben Verlauf ein wie die GUI; die Antwort enthält eine `undo_id`. Typischer Ablauf für Claude: lesen → schreibenden Befehl mit `--dry-run` → ausführen → bei Bedarf `undo`.

```bash
python cli.py leftovers --json --min-confidence hoch --limit 20
```

```bash
python cli.py rename --json --folder "C:\Fotos" --filter "*.jpg" --template "Urlaub_{n:3}" --dry-run
```

`python cli.py <befehl> --help` zeigt alle Optionen. Für Tests ersetzt die Umgebungsvariable `TIDYKIT_STARTUP_TEST_BASE` die echten Autostart-Quellen durch einen Test-Registry-Zweig.

## Daten

- Verlauf: `%LOCALAPPDATA%\TidyKit\verlauf.json` (jeder Schritt wird sofort geschrieben)
- Sicherungen: `%LOCALAPPDATA%\TidyKit\sicherungen\`
- Mit der Umgebungsvariable `TIDYKIT_DATA` lässt sich ein anderer Ordner setzen (nutzen die Tests).

## Tests

```bash
python -m unittest discover -s tests -v
```

Die Tests arbeiten nur mit Testordnern unter `%TEMP%` und einem eigenen Registry-Zweig (`HKCU\Software\TidyKitTest`) – echte Autostart-Einträge werden nicht angefasst. Abgedeckt: Papierkorb + Wiederherstellen, Umbenennen inkl. Tausch/Konflikte/Rückgängig, Ordner-Vergleich/Spiegeln/Rückgängig, Reste-Bewertung, leere Ordner, Autostart schalten/Rückgängig sowie ein GUI-Durchlauf aller Tabs. `tests/test_cli.py` führt jeden Befehl aus `toolhub.json` so aus wie ToolHub (inkl. `--dry-run`, Ausführen und `undo`) – nur an Testdaten.

## Aufbau

```
tidykit/
  trash.py       Papierkorb (SHFileOperationW) und gezieltes Wiederherstellen ($I/$R-Dateien)
  journal.py     Verlauf und Rückgängig
  cli.py         Kommandozeile mit JSON-Ausgabe (Einstieg: ../cli.py)
  leftovers.py   Reste-Finder und leere Ordner
  autostart.py   Autostart lesen, bewerten, schalten
  renamer.py     Regeln, Konfliktprüfung, zweiphasiges Umbenennen
  foldersync.py  Vergleich und Abgleich
  gui/           tkinter-Oberfläche (ein Tab je Werkzeug)
```

## Lizenz

MIT – siehe [LICENSE](LICENSE).
