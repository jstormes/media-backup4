#!/usr/bin/env python3
"""Entry point for media-backup."""

import os
import sys

# Allow running from the repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from media_backup.gui_app import main

main()
