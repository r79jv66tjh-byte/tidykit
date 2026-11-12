"""TidyKit-Kommandozeile (JSON-Ausgabe) – Einstieg für Claude/ToolHub.

Beispiel:  python cli.py startup-list --json
Hilfe:     python cli.py --help   bzw.   python cli.py <befehl> --help
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tidykit.cli import main  # noqa: E402

sys.exit(main())
