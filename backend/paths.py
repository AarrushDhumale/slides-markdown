"""Filesystem locations. Override the data root with S2M_DATA."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("S2M_DATA", PROJECT_ROOT / "data"))

INBOX_DIR = DATA_ROOT / "inbox"
CACHE_DIR = DATA_ROOT / "cache"
CHAPTERS_DIR = DATA_ROOT / "chapters"

PROMPTS_DIR = PACKAGE_ROOT / "prompts"
BACKENDS_FILE = Path(os.environ.get("S2M_BACKENDS", PACKAGE_ROOT / "llm" / "backends.yaml"))

LEVELS = ("proficient", "learner", "dockey")          # written from the slides
OUTPUTS = LEVELS + ("summary",)                       # summary is condensed from the learner doc
