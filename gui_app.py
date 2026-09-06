#!/usr/bin/env python3
"""Entry point for media-backup."""

import sys
import os

# Redirect stderr to a log file for debugging
log_path = os.path.join(os.path.dirname(__file__), "app.log")
sys.stderr = open(log_path, "w")

# Allow running from the repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from media_backup.gui_app import main

main()
