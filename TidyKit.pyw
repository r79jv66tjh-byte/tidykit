"""Doppelklick-Start ohne Konsolenfenster."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tidykit.gui.app import main  # noqa: E402

main()
