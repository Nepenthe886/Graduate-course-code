import logging
import os
import re
import sys
import json
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s%(experiment_tags)s: %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

SERVICE_INSTANCE_ID = datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]

_CONFIGURED = False
ACTIVE_LOG_FILE: Path | None = None


def _entrypoint_name() -> str:
    script_name = Path(sys.argv[0]).stem or "interactive"
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", script_name).strip("._-")
    return safe_name or "interactive"


def _new_log_file() -> Path:
    directory = LOG_DIR / 'service' / SERVICE_INSTANCE_ID
    directory.mkdir(parents=True, exist_ok=True)
    return directory / 'service.log'


def service_lifecycle(status):
    path = LOG_DIR / 'service' / SERVICE_INSTANCE_ID / 'service.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'schema_version': 3, 'service_instance_id': SERVICE_INSTANCE_ID, 'pid': os.getpid(),
        'entrypoint': _entrypoint_name(), 'started_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    data['status'] = status
    if status == 'stopped':
        data['stopped_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temp, path)


class ExperimentLogFilter(logging.Filter):
    def filter(self, record):
        # Import lazily to avoid a logging / experiment module cycle.
        from experiment_tracking import ACTIVE_RUN
        context = ACTIVE_RUN.get()
        ids = context.get('ids', {}) if context else {}
        record.experiment_tags = ''.join(f' {key}={value}' for key, value in ids.items() if value is not None)
        return True


def configure_logging() -> None:
    """Configure project-wide operational logging."""
    global _CONFIGURED, ACTIVE_LOG_FILE
    if _CONFIGURED:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_LOG_FILE = _new_log_file()
    service_lifecycle('running')

    formatter = logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(
        ACTIVE_LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(ExperimentLogFilter())

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(ExperimentLogFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not any(isinstance(handler, RotatingFileHandler) for handler in root_logger.handlers):
        root_logger.addHandler(file_handler)
    if not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, RotatingFileHandler)
        for handler in root_logger.handlers
    ):
        root_logger.addHandler(stream_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def get_active_log_file() -> Path | None:
    return ACTIVE_LOG_FILE
