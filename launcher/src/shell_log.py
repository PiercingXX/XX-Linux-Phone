from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging() -> None:
    log_dir = Path.home() / '.local' / 'share' / 'piercing-shell'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / 'shell.log'

    fmt = logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
    )

    file_handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding='utf-8',
    )
    file_handler.setFormatter(fmt)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)

    logging.getLogger('piercing').info('PiercingOS shell starting — log: %s', log_path)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f'piercing.{name}')
