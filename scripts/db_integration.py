"""
Database integration module for goshawk_ETL.

This module provides helper functions to integrate with the satelit_metadata database
for product traceability, preventing duplicate processing, and safe cleanup.

Author: goshawk_ETL + satelit_metadata integration
Version: 2.0
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Import satelit_db (will be available after conda env update)
try:
    from satelit_db.database import get_session
    from satelit_db.api import SatelitDBAPI
    from sqlalchemy import text

    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logging.warning("satelit_db not available - database features disabled")

logger = logging.getLogger(__name__)


def init_db() -> bool:
    """
    Initialize the database schema with granular tracking tables.
    
    This function ensures that the new tables for S1/S2 granular tracking exist:
    - slc_products: Sentinel-1 SLC tracking with per-subswath processing flags
    - insar_pairs: Full-swath InSAR pair tracking
    - s2_products: Sentinel-2 products with MSAVI tracking
    - insar_pair_msavi: Integration between InSAR and MSAVI
    
    Returns:
        True if tables exist or were created successfully, False otherwise
    """
    if not DB_AVAILABLE:
        logger.error("Database not available - cannot initialize schema")
        return False
    
    try:
        with get_session() as session:
            # Check if tables exist
            result = session.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'satelit' 
                AND table_name IN ('slc_products', 'insar_pairs', 's2_products', 'insar_pair_msavi')
            """))
            existing_tables = {row[0] for row in result}
            
            required_tables = {'slc_products', 'insar_pairs', 's2_products', 'insar_pair_msavi'}
            missing_tables = required_tables - existing_tables
            
            if missing_tables:
                logger.warning(f"Missing tables: {missing_tables}")
                logger.info("Please run: cd ../satelit_metadata && alembic upgrade head")
                return False
            
            logger.info("✓ All granular tracking tables exist")
            return True
            
    except Exception as e:
        logger.error(f"Failed to check database schema: {e}")
        return False


class GoshawkDBIntegration:
    """Integration layer between goshawk_ETL and satelit_metadata database."""

    def __init__(self, enabled: bool = True):
        """
        Initialize database integration.

        Args:
            enabled: If False, all operations become no-ops (graceful degradation)
        """
        self.enabled = enabled and DB_AVAILABLE

        if not self.enabled and enabled:
            logger.warning("Database integration requested but not available")

    def register_slc_download(
        self,
        scene_id: str,
        acquisition_date: datetime,
        file_path: str,
        orbit_direction: str = "UNKNOWN",
        relative_orbit: int = 0,
        absolute_orbit: int = 0,
        subswath: str = "IW",
        satellite_id: Optional[str] = None,
    ) -> Optional[int]:
        """
        Register a downloaded SLC product in the database.

        Args:
            scene_id: Sentinel product identifier
            acquisition_date: Acquisition datetime
            file_path: Path to downloaded SAFE file
            orbit_direction: ASCENDING or DESCENDING
            relative_orbit: Relative orbit number
            absolute_orbit: Absolute orbit number
            subswath: IW1, IW2, IW3, or IW
            satellite_id: S1A, S1B, S1C (extracted from scene_id if not provided)

        Returns:
            Product ID if registered, None if database not available
        """
        if not self.enabled:
            return None

        try:
            # Extract satellite ID from scene_id if not provided
            if satellite_id is None:
                satellite_id = scene_id[:3]  # S1A, S1B, S1C

            with get_session() as session:
                api = SatelitDBAPI(session)

                # Register product (API handles duplicates)
                product = api.register_slc_product(
                    scene_id=scene_id,
                    acquisition_date=acquisition_date,
                    satellite_id=satellite_id,
                    orbit_direction=orbit_direction,
                    relative_orbit=relative_orbit,
                    absolute_orbit=absolute_orbit,
                    subswath=subswath,
                    polarization=["VV", "VH"],  # Default for IW
                    download_source="copernicus",
                )

                # Update status
                product.processing_status = "DOWNLOADED"

                # Add storage location
                api.add_storage_location(
                    product_id=product.id,
                    storage_type="ORIGINAL_SLC",
                    file_path=file_path,
                    file_format="SAFE",
                    calculate_size=True,
                )

                logger.info(f"✓ SLC registered in database: {scene_id} (ID: {product.id})")
                return product.id

        except Exception as e:
            logger.warning(f"Failed to register SLC in database: {e}")
            return None

    def is_slc_downloaded(self, scene_id: str) -> bool:
        """
        Check if SLC is already downloaded (registered in database).

        Args:
            scene_id: Sentinel product identifier

        Returns:
            True if already downloaded, False otherwise
        """
        if not self.enabled:
            return False

        try:
            with get_session() as session:
                api = SatelitDBAPI(session)

                existing = api.find_products_by_criteria(
                    product_type="SLC",
                    scene_id=scene_id,
                    processing_status="DOWNLOADED",
                )

                return len(existing) > 0

        except Exception as e:
            logger.debug(f"Could not check database: {e}")
            return False

    def register_insar_product(
        self,
        master_scene_id: str,
        slave_scene_id: str,
        pair_type: str,
        output_path: str,
        temporal_baseline_days: int,
        perpendicular_baseline_m: Optional[float] = None,
        coherence_stats: Optional[Dict] = None,
        subswath: Optional[str] = None,
        orbit_direction: Optional[str] = None,
        track_number: Optional[int] = None,
    ) -> Optional[int]:
        """
        Register an InSAR product in the database.

        Args:
            master_scene_id: Master SLC scene ID
            slave_scene_id: Slave SLC scene ID
            pair_type: 'short' or 'long'
            output_path: Path to InSAR product
            temporal_baseline_days: Temporal baseline in days
            perpendicular_baseline_m: Perpendicular baseline in meters
            coherence_stats: Dict with 'mean' and 'std' coherence values
            subswath: Subswath for the InSAR product (IW1, IW2, IW3)
            orbit_direction: Orbit direction (ASCENDING, DESCENDING)
            track_number: Track number

        Returns:
            Product ID if registered, None if database not available
        """
        if not self.enabled:
            return None

        try:
            coherence_stats = coherence_stats or {}

            with get_session() as session:
                api = SatelitDBAPI(session)

                # Register InSAR product
                insar_product = api.register_insar_product(
                    master_scene_id=master_scene_id,
                    slave_scene_id=slave_scene_id,
                    pair_type=pair_type,
                    temporal_baseline_days=temporal_baseline_days,
                    perpendicular_baseline_m=perpendicular_baseline_m,
                    coherence_mean=coherence_stats.get("mean"),
                    coherence_std=coherence_stats.get("std"),
                    subswath=subswath,
                    orbit_direction=orbit_direction,
                    track_number=track_number,
                )

                # Update status
                insar_product.processing_status = "PROCESSED"

                # Add storage location
                api.add_storage_location(
                    product_id=insar_product.id,
                    storage_type="INSAR_PRODUCT",
                    file_path=output_path,
                    file_format="BEAM-DIMAP",
                    calculate_size=True,
                )

                logger.info(
                    f"✓ InSAR registered: {master_scene_id}_{slave_scene_id} (ID: {insar_product.id})"
                )
                return insar_product.id

        except Exception as e:
            logger.warning(f"Failed to register InSAR in database: {e}")
            return None

    def can_delete_slc(self, slc_path: str) -> Tuple[bool, str]:
        """
        Check if an SLC can be safely deleted.

        Args:
            slc_path: Path to SLC product

        Returns:
            Tuple of (can_delete: bool, reason: str)
        """
        if not self.enabled:
            return False, "Database not available"

        try:
            # Extract scene_id from path
            scene_id = self._extract_scene_id_from_path(slc_path)
            if not scene_id:
                return False, "Could not extract scene_id from path"

            with get_session() as session:
                api = SatelitDBAPI(session)

                # Find product
                products = api.find_products_by_criteria(
                    product_type="SLC",
                    scene_id=scene_id,
                )

                if not products:
                    return False, "SLC not found in database"

                # Check if can delete
                can_delete, reason = api.can_delete_slc(products[0].id)
                return can_delete, reason

        except Exception as e:
            logger.warning(f"Failed to check if SLC can be deleted: {e}")
            return False, f"Database error: {str(e)}"

    def get_track_statistics(
        self, orbit_direction: str, subswath: str, track_number: int
    ) -> Optional[Dict]:
        """
        Get statistics for a track.

        Args:
            orbit_direction: ASCENDING or DESCENDING
            subswath: IW1, IW2, IW3
            track_number: Track number (1-175)

        Returns:
            Dictionary with track statistics, None if database not available
        """
        if not self.enabled:
            return None

        try:
            with get_session() as session:
                api = SatelitDBAPI(session)
                return api.get_track_statistics(orbit_direction, subswath, track_number)

        except Exception as e:
            logger.warning(f"Failed to get track statistics: {e}")
            return None

    @staticmethod
    def _extract_scene_id_from_path(path: str) -> Optional[str]:
        """
        Extract scene_id from file path.

        Args:
            path: Path to SAFE file

        Returns:
            Scene ID or None
        """
        # Pattern: S1A_IW_SLC__1SDV_20230111T060136_20230111T060203_046714_059C5B_F5B0
        pattern = r"(S1[ABC]_IW_SLC__1SDV_\d{8}T\d{6}_\d{8}T\d{6}_[0-9A-F]{6}_[0-9A-F]{6}_[0-9A-F]{4})"
        match = re.search(pattern, path)
        return match.group(1) if match else None

    def update_processing_status(
        self, scene_id: str, status: str, product_type: str = "SLC"
    ) -> bool:
        """
        Update processing status for a product.

        Args:
            scene_id: Scene identifier
            status: New status (DISCOVERED, DOWNLOADED, PREPROCESSING, PROCESSED, FAILED)
            product_type: Product type (default: SLC)

        Returns:
            True if updated, False otherwise
        """
        if not self.enabled:
            return False

        try:
            with get_session() as session:
                api = SatelitDBAPI(session)

                products = api.find_products_by_criteria(
                    product_type=product_type,
                    scene_id=scene_id,
                )

                if not products:
                    logger.warning(f"Product {scene_id} not found in database")
                    return False

                products[0].processing_status = status
                logger.info(f"✓ Updated {scene_id} status to {status}")
                return True

        except Exception as e:
            logger.warning(f"Failed to update processing status: {e}")
            return False


# Global instance (lazy initialization)
_db_integration = None


def get_db_integration(enabled: bool = True) -> GoshawkDBIntegration:
    """
    Get global database integration instance.

    Args:
        enabled: Enable database integration

    Returns:
        GoshawkDBIntegration instance
    """
    global _db_integration
    if _db_integration is None:
        _db_integration = GoshawkDBIntegration(enabled=enabled)
    return _db_integration


# Convenience functions
def register_slc_download(scene_id: str, acquisition_date: datetime, file_path: str, **kwargs):
    """Register SLC download (convenience function)."""
    return get_db_integration().register_slc_download(
        scene_id, acquisition_date, file_path, **kwargs
    )


def is_slc_downloaded(scene_id: str) -> bool:
    """Check if SLC is downloaded (convenience function)."""
    return get_db_integration().is_slc_downloaded(scene_id)


def register_insar_product(
    master_scene_id: str,
    slave_scene_id: str,
    pair_type: str,
    output_path: str,
    temporal_baseline_days: int,
    **kwargs,
):
    """Register InSAR product (convenience function)."""
    return get_db_integration().register_insar_product(
        master_scene_id,
        slave_scene_id,
        pair_type,
        output_path,
        temporal_baseline_days,
        **kwargs,
    )


def can_delete_slc(slc_path: str) -> Tuple[bool, str]:
    """Check if SLC can be deleted (convenience function)."""
    return get_db_integration().can_delete_slc(slc_path)
