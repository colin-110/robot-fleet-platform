"""
Pre-start hook: run database migrations before uvicorn starts.
"""

import logging
import subprocess

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
    init_db()


if __name__ == "__main__":
    main()
