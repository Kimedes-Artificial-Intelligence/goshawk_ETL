#!/usr/bin/env python3
"""
Test script to verify the database schema is properly initialized.

Usage:
    python scripts/test_db_schema.py
"""

import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.db_integration import init_db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """Test database schema initialization."""
    logger.info("Testing database schema...")
    
    success = init_db()
    
    if success:
        logger.info("✓ Database schema is properly initialized")
        sys.exit(0)
    else:
        logger.error("✗ Database schema initialization failed")
        logger.info("\nTo fix this, run:")
        logger.info("  cd ../satelit_metadata")
        logger.info("  alembic upgrade head")
        sys.exit(1)


if __name__ == "__main__":
    main()
