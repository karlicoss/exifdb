#!/usr/bin/env python3
from datetime import datetime, timezone
from pathlib import Path
import sys

from loguru import logger

logger.remove()
logger.add(sys.stderr, level='INFO', format='<lvl>{level}</lvl> {message}', colorize=True)


# TODO reuse in run.py
def get_media_mtime(path: Path) -> str:
    dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    return dt.isoformat()
