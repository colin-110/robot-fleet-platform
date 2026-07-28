"""
Pre-start hook: run migrations and prepare the metrics directory.

Runs once in the parent process before uvicorn forks its workers.
"""

import logging
import subprocess

from app import metrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prestart")


def init_db() -> None:
    logger.info("Running database migrations...")
    try:
        subprocess.run(["alembic", "upgrade", "head"], check=True)
        logger.info("Database migrations complete.")
    except subprocess.CalledProcessError:
        logger.exception("Alembic migration failed")
        raise


def main() -> None:
    # Must happen before workers fork: clears samples left by dead PIDs from a
    # previous run, which would otherwise be summed into the live numbers.
    metrics.init_multiproc_dir()
    init_db()


if __name__ == "__main__":
    main()
