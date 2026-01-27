"""
Database query and update helper functions for goshawk_ETL.

This module provides an abstraction layer for interacting with the granular
tracking tables (slc_products, insar_pairs, s2_products, insar_pair_msavi).

Author: goshawk_ETL
Version: 1.0
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

try:
    from sqlalchemy import text, and_, or_
    from sqlalchemy.exc import IntegrityError
    from satelit_db.database import get_session
    
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logging.warning("satelit_db not available - database query features disabled")

logger = logging.getLogger(__name__)


# ============================================================================
# Sentinel-1 Functions
# ============================================================================

def get_slc_status(scene_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the status of an SLC product.
    
    Args:
        scene_id: Sentinel-1 scene identifier
        
    Returns:
        Dictionary with download status, processing status (IW1/IW2/IW3),
        and polarimetry status. None if not found or database unavailable.
        
    Example:
        {
            'id': 123,
            'scene_id': 'S1A_IW_SLC__1SDV_...',
            'downloaded': True,
            'downloaded_date': datetime(...),
            'fullswath_iw1_processed': True,
            'fullswath_iw1_date': datetime(...),
            'fullswath_iw2_processed': False,
            'fullswath_iw3_processed': False,
            'polarimetry_processed': False,
            'file_path': '/path/to/slc.SAFE'
        }
    """
    if not DB_AVAILABLE:
        return None
    
    try:
        with get_session() as session:
            result = session.execute(
                text("""
                    SELECT id, scene_id, acquisition_date, satellite_id,
                           orbit_direction, track_number, subswath, file_path,
                           downloaded, downloaded_date,
                           fullswath_iw1_processed, fullswath_iw1_date, fullswath_iw1_version,
                           fullswath_iw2_processed, fullswath_iw2_date, fullswath_iw2_version,
                           fullswath_iw3_processed, fullswath_iw3_date, fullswath_iw3_version,
                           polarimetry_processed, polarimetry_date, polarimetry_version
                    FROM satelit.slc_products
                    WHERE scene_id = :scene_id
                """),
                {"scene_id": scene_id}
            ).fetchone()
            
            if result is None:
                return None
            
            return dict(result._mapping)
            
    except Exception as e:
        logger.error(f"Failed to get SLC status for {scene_id}: {e}")
        return None


def update_slc(scene_id: str, **kwargs) -> bool:
    """
    Update an SLC product with new flags and timestamps.
    
    Args:
        scene_id: Sentinel-1 scene identifier
        **kwargs: Fields to update (e.g., fullswath_iw1_processed=True,
                  fullswath_iw1_date=datetime.now(), file_path='/path/to/file')
        
    Returns:
        True if update succeeded, False otherwise
        
    Example:
        update_slc('S1A_IW_SLC__1SDV_...', 
                   fullswath_iw1_processed=True,
                   fullswath_iw1_date=datetime.now(),
                   fullswath_iw1_version='1.0')
    """
    if not DB_AVAILABLE:
        return False
    
    if not kwargs:
        logger.warning("update_slc called with no fields to update")
        return False
    
    try:
        with get_session() as session:
            # Build UPDATE statement dynamically
            set_clause = ", ".join([f"{key} = :{key}" for key in kwargs.keys()])
            params = {"scene_id": scene_id, **kwargs}
            
            result = session.execute(
                text(f"""
                    UPDATE satelit.slc_products
                    SET {set_clause}
                    WHERE scene_id = :scene_id
                """),
                params
            )
            
            session.commit()
            
            if result.rowcount == 0:
                logger.warning(f"No SLC found with scene_id={scene_id}")
                return False
            
            logger.info(f"✓ Updated SLC {scene_id}: {list(kwargs.keys())}")
            return True
            
    except Exception as e:
        logger.error(f"Failed to update SLC {scene_id}: {e}")
        return False


def register_slc_download(
    scene_id: str,
    acquisition_date: datetime,
    orbit_direction: str,
    track_number: int,
    file_path: str,
    satellite_id: Optional[str] = None,
    subswath: str = "IW"
) -> Optional[int]:
    """
    Register a downloaded SLC product or update existing record.
    
    Args:
        scene_id: Sentinel-1 scene identifier
        acquisition_date: Acquisition datetime
        orbit_direction: ASCENDING or DESCENDING
        track_number: Track number (1-175)
        file_path: Path to downloaded SAFE file
        satellite_id: S1A, S1B, or S1C (auto-extracted if None)
        subswath: IW1, IW2, IW3, or IW
        
    Returns:
        Product ID if successful, None otherwise
    """
    if not DB_AVAILABLE:
        return None
    
    if satellite_id is None:
        satellite_id = scene_id[:3]
    
    try:
        with get_session() as session:
            # Check if already exists
            existing = session.execute(
                text("SELECT id FROM satelit.slc_products WHERE scene_id = :scene_id"),
                {"scene_id": scene_id}
            ).fetchone()
            
            if existing:
                # Update existing record
                session.execute(
                    text("""
                        UPDATE satelit.slc_products
                        SET file_path = :file_path,
                            downloaded = true,
                            downloaded_date = :downloaded_date
                        WHERE scene_id = :scene_id
                    """),
                    {
                        "scene_id": scene_id,
                        "file_path": file_path,
                        "downloaded_date": datetime.now()
                    }
                )
                session.commit()
                logger.info(f"✓ Updated SLC download: {scene_id}")
                return existing[0]
            else:
                # Insert new record
                result = session.execute(
                    text("""
                        INSERT INTO satelit.slc_products
                        (scene_id, acquisition_date, satellite_id, orbit_direction, 
                         track_number, subswath, file_path, downloaded, downloaded_date)
                        VALUES (:scene_id, :acq_date, :sat_id, :orbit, :track, 
                                :subswath, :file_path, true, :downloaded_date)
                        RETURNING id
                    """),
                    {
                        "scene_id": scene_id,
                        "acq_date": acquisition_date,
                        "sat_id": satellite_id,
                        "orbit": orbit_direction,
                        "track": track_number,
                        "subswath": subswath,
                        "file_path": file_path,
                        "downloaded_date": datetime.now()
                    }
                )
                session.commit()
                product_id = result.fetchone()[0]
                logger.info(f"✓ Registered SLC download: {scene_id} (ID: {product_id})")
                return product_id
                
    except Exception as e:
        logger.error(f"Failed to register SLC download for {scene_id}: {e}")
        return None


def insar_pair_exists(
    master_scene_id: str,
    slave_scene_id: str,
    subswath: str,
    pair_type: str
) -> bool:
    """
    Check if an InSAR pair already exists in the database.
    
    Args:
        master_scene_id: Master SLC scene ID
        slave_scene_id: Slave SLC scene ID
        subswath: IW1, IW2, or IW3
        pair_type: 'short' or 'long'
        
    Returns:
        True if pair exists, False otherwise
    """
    if not DB_AVAILABLE:
        return False
    
    try:
        with get_session() as session:
            result = session.execute(
                text("""
                    SELECT COUNT(*) 
                    FROM satelit.insar_pairs ip
                    JOIN satelit.slc_products m ON ip.master_slc_id = m.id
                    JOIN satelit.slc_products s ON ip.slave_slc_id = s.id
                    WHERE m.scene_id = :master_id
                      AND s.scene_id = :slave_id
                      AND ip.subswath = :subswath
                      AND ip.pair_type = :pair_type
                """),
                {
                    "master_id": master_scene_id,
                    "slave_id": slave_scene_id,
                    "subswath": subswath,
                    "pair_type": pair_type
                }
            ).scalar()
            
            return result > 0
            
    except Exception as e:
        logger.error(f"Failed to check InSAR pair existence: {e}")
        return False


def register_insar_pair(
    master_scene_id: str,
    slave_scene_id: str,
    pair_type: str,
    subswath: str,
    temporal_baseline_days: int,
    file_path: str,
    perpendicular_baseline_m: Optional[float] = None,
    coherence_mean: Optional[float] = None,
    coherence_std: Optional[float] = None,
    processing_version: Optional[str] = None
) -> Optional[int]:
    """
    Register a successfully processed InSAR pair.
    
    Args:
        master_scene_id: Master SLC scene ID
        slave_scene_id: Slave SLC scene ID
        pair_type: 'short' or 'long'
        subswath: IW1, IW2, or IW3
        temporal_baseline_days: Temporal baseline in days
        file_path: Path to processed .dim file
        perpendicular_baseline_m: Perpendicular baseline in meters
        coherence_mean: Mean coherence value
        coherence_std: Standard deviation of coherence
        processing_version: Processing software version
        
    Returns:
        InSAR pair ID if successful, None otherwise
    """
    if not DB_AVAILABLE:
        return None
    
    try:
        with get_session() as session:
            # Get master and slave IDs
            master_result = session.execute(
                text("SELECT id FROM satelit.slc_products WHERE scene_id = :scene_id"),
                {"scene_id": master_scene_id}
            ).fetchone()
            
            slave_result = session.execute(
                text("SELECT id FROM satelit.slc_products WHERE scene_id = :scene_id"),
                {"scene_id": slave_scene_id}
            ).fetchone()
            
            if not master_result or not slave_result:
                logger.error(f"Master or slave SLC not found: {master_scene_id}, {slave_scene_id}")
                return None
            
            master_id = master_result[0]
            slave_id = slave_result[0]
            
            # Insert or update InSAR pair
            result = session.execute(
                text("""
                    INSERT INTO satelit.insar_pairs
                    (master_slc_id, slave_slc_id, pair_type, subswath, temporal_baseline_days,
                     perpendicular_baseline_m, file_path, processed_date, processing_version,
                     coherence_mean, coherence_std)
                    VALUES (:master_id, :slave_id, :pair_type, :subswath, :temporal_baseline,
                            :perp_baseline, :file_path, :processed_date, :version,
                            :coherence_mean, :coherence_std)
                    ON CONFLICT (master_slc_id, slave_slc_id, subswath, pair_type)
                    DO UPDATE SET
                        file_path = EXCLUDED.file_path,
                        processed_date = EXCLUDED.processed_date,
                        processing_version = EXCLUDED.processing_version,
                        perpendicular_baseline_m = EXCLUDED.perpendicular_baseline_m,
                        coherence_mean = EXCLUDED.coherence_mean,
                        coherence_std = EXCLUDED.coherence_std
                    RETURNING id
                """),
                {
                    "master_id": master_id,
                    "slave_id": slave_id,
                    "pair_type": pair_type,
                    "subswath": subswath,
                    "temporal_baseline": temporal_baseline_days,
                    "perp_baseline": perpendicular_baseline_m,
                    "file_path": file_path,
                    "processed_date": datetime.now(),
                    "version": processing_version,
                    "coherence_mean": coherence_mean,
                    "coherence_std": coherence_std
                }
            )
            session.commit()
            
            pair_id = result.fetchone()[0]
            logger.info(f"✓ Registered InSAR pair: {master_scene_id}_{slave_scene_id} (ID: {pair_id})")
            return pair_id
            
    except Exception as e:
        logger.error(f"Failed to register InSAR pair: {e}")
        return None


def get_insar_pairs(
    track_number: int,
    orbit_direction: str,
    subswath: str,
    pair_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get list of processed InSAR pairs for a specific track/orbit/subswath.
    
    Useful for the final crop step to know what pairs are available.
    
    Args:
        track_number: Track number (1-175)
        orbit_direction: ASCENDING or DESCENDING
        subswath: IW1, IW2, or IW3
        pair_type: Optional filter for 'short' or 'long' (None = all)
        
    Returns:
        List of dictionaries with pair information
        
    Example:
        [
            {
                'id': 456,
                'master_scene_id': 'S1A_IW_SLC__1SDV_...',
                'slave_scene_id': 'S1A_IW_SLC__1SDV_...',
                'master_date': datetime(...),
                'slave_date': datetime(...),
                'temporal_baseline_days': 12,
                'perpendicular_baseline_m': 45.2,
                'file_path': '/path/to/insar.dim',
                'coherence_mean': 0.65
            },
            ...
        ]
    """
    if not DB_AVAILABLE:
        return []
    
    try:
        with get_session() as session:
            pair_type_filter = "AND ip.pair_type = :pair_type" if pair_type else ""
            params = {
                "track": track_number,
                "orbit": orbit_direction,
                "subswath": subswath
            }
            if pair_type:
                params["pair_type"] = pair_type
            
            results = session.execute(
                text(f"""
                    SELECT 
                        ip.id,
                        m.scene_id as master_scene_id,
                        s.scene_id as slave_scene_id,
                        m.acquisition_date as master_date,
                        s.acquisition_date as slave_date,
                        ip.pair_type,
                        ip.subswath,
                        ip.temporal_baseline_days,
                        ip.perpendicular_baseline_m,
                        ip.file_path,
                        ip.coherence_mean,
                        ip.coherence_std,
                        ip.processed_date
                    FROM satelit.insar_pairs ip
                    JOIN satelit.slc_products m ON ip.master_slc_id = m.id
                    JOIN satelit.slc_products s ON ip.slave_slc_id = s.id
                    WHERE m.track_number = :track
                      AND m.orbit_direction = :orbit
                      AND ip.subswath = :subswath
                      {pair_type_filter}
                    ORDER BY m.acquisition_date, s.acquisition_date
                """),
                params
            ).fetchall()
            
            return [dict(row._mapping) for row in results]
            
    except Exception as e:
        logger.error(f"Failed to get InSAR pairs: {e}")
        return []


# ============================================================================
# Sentinel-2 Functions
# ============================================================================

def get_s2_status(scene_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the status of a Sentinel-2 product.
    
    Args:
        scene_id: Sentinel-2 scene identifier
        
    Returns:
        Dictionary with download status and MSAVI processing status.
        None if not found or database unavailable.
        
    Example:
        {
            'id': 789,
            'scene_id': 'S2A_MSIL2A_...',
            'acquisition_date': datetime(...),
            'cloud_cover_percent': 15.3,
            'aoi_coverage_percent': 98.5,
            'downloaded': True,
            'msavi_processed': True,
            'msavi_file_path': '/path/to/msavi.tif'
        }
    """
    if not DB_AVAILABLE:
        return None
    
    try:
        with get_session() as session:
            result = session.execute(
                text("""
                    SELECT id, scene_id, acquisition_date, satellite_id,
                           cloud_cover_percent, aoi_coverage_percent,
                           file_path, downloaded, downloaded_date,
                           msavi_processed, msavi_file_path, msavi_date, msavi_version
                    FROM satelit.s2_products
                    WHERE scene_id = :scene_id
                """),
                {"scene_id": scene_id}
            ).fetchone()
            
            if result is None:
                return None
            
            return dict(result._mapping)
            
    except Exception as e:
        logger.error(f"Failed to get S2 status for {scene_id}: {e}")
        return None


def update_s2(scene_id: str, **kwargs) -> bool:
    """
    Update a Sentinel-2 product with new flags and timestamps.
    
    Args:
        scene_id: Sentinel-2 scene identifier
        **kwargs: Fields to update
        
    Returns:
        True if update succeeded, False otherwise
        
    Example:
        update_s2('S2A_MSIL2A_...', 
                  msavi_processed=True,
                  msavi_file_path='/path/to/msavi.tif',
                  msavi_date=datetime.now())
    """
    if not DB_AVAILABLE:
        return False
    
    if not kwargs:
        logger.warning("update_s2 called with no fields to update")
        return False
    
    try:
        with get_session() as session:
            set_clause = ", ".join([f"{key} = :{key}" for key in kwargs.keys()])
            params = {"scene_id": scene_id, **kwargs}
            
            result = session.execute(
                text(f"""
                    UPDATE satelit.s2_products
                    SET {set_clause}
                    WHERE scene_id = :scene_id
                """),
                params
            )
            
            session.commit()
            
            if result.rowcount == 0:
                logger.warning(f"No S2 product found with scene_id={scene_id}")
                return False
            
            logger.info(f"✓ Updated S2 {scene_id}: {list(kwargs.keys())}")
            return True
            
    except Exception as e:
        logger.error(f"Failed to update S2 {scene_id}: {e}")
        return False


def register_s2_download(
    scene_id: str,
    acquisition_date: datetime,
    file_path: str,
    satellite_id: Optional[str] = None,
    cloud_cover_percent: Optional[float] = None,
    aoi_coverage_percent: Optional[float] = None
) -> Optional[int]:
    """
    Register a downloaded Sentinel-2 product or update existing record.
    
    Args:
        scene_id: Sentinel-2 scene identifier
        acquisition_date: Acquisition datetime
        file_path: Path to downloaded product
        satellite_id: S2A or S2B (auto-extracted if None)
        cloud_cover_percent: Cloud coverage percentage
        aoi_coverage_percent: AOI coverage percentage
        
    Returns:
        Product ID if successful, None otherwise
    """
    if not DB_AVAILABLE:
        return None
    
    if satellite_id is None:
        satellite_id = scene_id[:3]
    
    try:
        with get_session() as session:
            # Check if already exists
            existing = session.execute(
                text("SELECT id FROM satelit.s2_products WHERE scene_id = :scene_id"),
                {"scene_id": scene_id}
            ).fetchone()
            
            if existing:
                # Update existing record
                session.execute(
                    text("""
                        UPDATE satelit.s2_products
                        SET file_path = :file_path,
                            downloaded = true,
                            downloaded_date = :downloaded_date,
                            cloud_cover_percent = COALESCE(:cloud_cover, cloud_cover_percent),
                            aoi_coverage_percent = COALESCE(:aoi_coverage, aoi_coverage_percent)
                        WHERE scene_id = :scene_id
                    """),
                    {
                        "scene_id": scene_id,
                        "file_path": file_path,
                        "downloaded_date": datetime.now(),
                        "cloud_cover": cloud_cover_percent,
                        "aoi_coverage": aoi_coverage_percent
                    }
                )
                session.commit()
                logger.info(f"✓ Updated S2 download: {scene_id}")
                return existing[0]
            else:
                # Insert new record
                result = session.execute(
                    text("""
                        INSERT INTO satelit.s2_products
                        (scene_id, acquisition_date, satellite_id, file_path,
                         downloaded, downloaded_date, cloud_cover_percent, aoi_coverage_percent)
                        VALUES (:scene_id, :acq_date, :sat_id, :file_path,
                                true, :downloaded_date, :cloud_cover, :aoi_coverage)
                        RETURNING id
                    """),
                    {
                        "scene_id": scene_id,
                        "acq_date": acquisition_date,
                        "sat_id": satellite_id,
                        "file_path": file_path,
                        "downloaded_date": datetime.now(),
                        "cloud_cover": cloud_cover_percent,
                        "aoi_coverage": aoi_coverage_percent
                    }
                )
                session.commit()
                product_id = result.fetchone()[0]
                logger.info(f"✓ Registered S2 download: {scene_id} (ID: {product_id})")
                return product_id
                
    except Exception as e:
        logger.error(f"Failed to register S2 download for {scene_id}: {e}")
        return None


def find_msavi_for_date(
    target_date: datetime,
    window_days: int = 15,
    max_cloud_cover: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """
    Find Sentinel-2 product with MSAVI closest to a specific date.
    
    Args:
        target_date: Target date to find closest S2 product
        window_days: Search window in days (default: ±15 days)
        max_cloud_cover: Optional maximum cloud cover percentage filter
        
    Returns:
        Dictionary with S2 product information, or None if not found
        
    Example:
        {
            'id': 789,
            'scene_id': 'S2A_MSIL2A_...',
            'acquisition_date': datetime(...),
            'cloud_cover_percent': 12.5,
            'msavi_file_path': '/path/to/msavi.tif',
            'date_offset_days': 3  # Days from target_date
        }
    """
    if not DB_AVAILABLE:
        return None
    
    try:
        with get_session() as session:
            cloud_filter = "AND cloud_cover_percent <= :max_cloud" if max_cloud_cover else ""
            params = {
                "target_date": target_date,
                "window_days": window_days
            }
            if max_cloud_cover:
                params["max_cloud"] = max_cloud_cover
            
            result = session.execute(
                text(f"""
                    SELECT 
                        id, scene_id, acquisition_date, satellite_id,
                        cloud_cover_percent, aoi_coverage_percent,
                        msavi_file_path, msavi_date, msavi_version,
                        ABS(EXTRACT(DAY FROM acquisition_date - :target_date)) as date_offset_days
                    FROM satelit.s2_products
                    WHERE msavi_processed = true
                      AND ABS(EXTRACT(DAY FROM acquisition_date - :target_date)) <= :window_days
                      {cloud_filter}
                    ORDER BY date_offset_days ASC
                    LIMIT 1
                """),
                params
            ).fetchone()
            
            if result is None:
                return None
            
            return dict(result._mapping)
            
    except Exception as e:
        logger.error(f"Failed to find MSAVI for date {target_date}: {e}")
        return None


def register_pair_msavi(
    insar_pair_id: int,
    master_s2_id: int,
    slave_s2_id: int,
    master_msavi_file: str,
    slave_msavi_file: str,
    master_date_offset_days: int,
    slave_date_offset_days: int
) -> Optional[int]:
    """
    Link Sentinel-2 MSAVI products to an InSAR pair.
    
    Args:
        insar_pair_id: InSAR pair ID from insar_pairs table
        master_s2_id: S2 product ID for master date
        slave_s2_id: S2 product ID for slave date
        master_msavi_file: Path to master MSAVI file
        slave_msavi_file: Path to slave MSAVI file
        master_date_offset_days: Days between InSAR master and S2 master
        slave_date_offset_days: Days between InSAR slave and S2 slave
        
    Returns:
        Integration record ID if successful, None otherwise
    """
    if not DB_AVAILABLE:
        return None
    
    try:
        with get_session() as session:
            # Get aligned date (use InSAR master date as reference)
            aligned_date_result = session.execute(
                text("""
                    SELECT m.acquisition_date
                    FROM satelit.insar_pairs ip
                    JOIN satelit.slc_products m ON ip.master_slc_id = m.id
                    WHERE ip.id = :pair_id
                """),
                {"pair_id": insar_pair_id}
            ).fetchone()
            
            if not aligned_date_result:
                logger.error(f"InSAR pair not found: {insar_pair_id}")
                return None
            
            aligned_date = aligned_date_result[0]
            
            # Insert or update
            result = session.execute(
                text("""
                    INSERT INTO satelit.insar_pair_msavi
                    (insar_pair_id, master_s2_id, slave_s2_id,
                     master_msavi_file, slave_msavi_file,
                     master_date_offset_days, slave_date_offset_days,
                     aligned_date)
                    VALUES (:pair_id, :master_s2, :slave_s2,
                            :master_file, :slave_file,
                            :master_offset, :slave_offset,
                            :aligned_date)
                    ON CONFLICT (insar_pair_id)
                    DO UPDATE SET
                        master_s2_id = EXCLUDED.master_s2_id,
                        slave_s2_id = EXCLUDED.slave_s2_id,
                        master_msavi_file = EXCLUDED.master_msavi_file,
                        slave_msavi_file = EXCLUDED.slave_msavi_file,
                        master_date_offset_days = EXCLUDED.master_date_offset_days,
                        slave_date_offset_days = EXCLUDED.slave_date_offset_days,
                        aligned_date = EXCLUDED.aligned_date
                    RETURNING id
                """),
                {
                    "pair_id": insar_pair_id,
                    "master_s2": master_s2_id,
                    "slave_s2": slave_s2_id,
                    "master_file": master_msavi_file,
                    "slave_file": slave_msavi_file,
                    "master_offset": master_date_offset_days,
                    "slave_offset": slave_date_offset_days,
                    "aligned_date": aligned_date
                }
            )
            session.commit()
            
            integration_id = result.fetchone()[0]
            logger.info(f"✓ Registered InSAR-MSAVI integration (ID: {integration_id})")
            return integration_id
            
    except Exception as e:
        logger.error(f"Failed to register InSAR-MSAVI integration: {e}")
        return None


# ============================================================================
# Convenience Functions
# ============================================================================

def get_slc_by_id(slc_id: int) -> Optional[Dict[str, Any]]:
    """Get SLC product by database ID."""
    if not DB_AVAILABLE:
        return None
    
    try:
        with get_session() as session:
            result = session.execute(
                text("SELECT * FROM satelit.slc_products WHERE id = :id"),
                {"id": slc_id}
            ).fetchone()
            
            return dict(result._mapping) if result else None
    except Exception as e:
        logger.error(f"Failed to get SLC by ID {slc_id}: {e}")
        return None


def get_s2_by_id(s2_id: int) -> Optional[Dict[str, Any]]:
    """Get S2 product by database ID."""
    if not DB_AVAILABLE:
        return None
    
    try:
        with get_session() as session:
            result = session.execute(
                text("SELECT * FROM satelit.s2_products WHERE id = :id"),
                {"id": s2_id}
            ).fetchone()
            
            return dict(result._mapping) if result else None
    except Exception as e:
        logger.error(f"Failed to get S2 by ID {s2_id}: {e}")
        return None
