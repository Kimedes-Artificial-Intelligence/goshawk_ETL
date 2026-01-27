#!/usr/bin/env python3
"""
Example usage script for database query and update functions.

This script demonstrates all the database interaction functions
for Sentinel-1 and Sentinel-2 products.

Usage:
    python scripts/db_example_usage.py
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.db_queries import (
    get_slc_status, update_slc, register_slc_download,
    insar_pair_exists, register_insar_pair, get_insar_pairs,
    get_s2_status, update_s2, register_s2_download,
    find_msavi_for_date, register_pair_msavi,
)
from scripts.db_integration import init_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def example_sentinel1():
    print("\n" + "="*80)
    print("SENTINEL-1 WORKFLOW")
    print("="*80)
    
    master = "S1A_IW_SLC__1SDV_20230115T060136_20230115T060203_046714_059C5B_F5B0"
    slave = "S1A_IW_SLC__1SDV_20230127T060136_20230127T060203_046889_059F82_A3D1"
    
    print("\n1. Registering SLC downloads...")
    m_id = register_slc_download(master, datetime(2023,1,15,6,1,36), "ASCENDING", 117, f"/data/{master}.SAFE")
    s_id = register_slc_download(slave, datetime(2023,1,27,6,1,36), "ASCENDING", 117, f"/data/{slave}.SAFE")
    
    if m_id and s_id:
        print(f"   ✓ Master: ID={m_id}, Slave: ID={s_id}")
    
        print("\n2. Checking SLC status...")
        status = get_slc_status(master)
        if status:
            print(f"   Downloaded: {status['downloaded']}, IW1: {status['fullswath_iw1_processed']}")
        
        print("\n3. Updating processing status...")
        if update_slc(master, fullswath_iw1_processed=True, fullswath_iw1_date=datetime.now()):
            print("   ✓ Updated IW1 status")
        
        print("\n4. Registering InSAR pair...")
        pair_id = register_insar_pair(master, slave, "short", "IW1", 12, f"/data/insar_{master}_{slave}.dim", 
                                       perpendicular_baseline_m=45.2, coherence_mean=0.65)
        if pair_id:
            print(f"   ✓ Pair ID: {pair_id}")
        
        print("\n5. Getting InSAR pairs for track...")
        pairs = get_insar_pairs(117, "ASCENDING", "IW1")
        print(f"   Found {len(pairs)} pair(s)")


def example_sentinel2():
    print("\n" + "="*80)
    print("SENTINEL-2 WORKFLOW")
    print("="*80)
    
    s2_scene = "S2A_MSIL2A_20230120T105321_N0509_R051_T31TDF_20230120T170301"
    
    print("\n1. Registering S2 download...")
    s2_id = register_s2_download(s2_scene, datetime(2023,1,20,10,53,21), f"/data/{s2_scene}.SAFE",
                                  cloud_cover_percent=12.5)
    if s2_id:
        print(f"   ✓ S2 ID: {s2_id}")
        
        print("\n2. Updating MSAVI status...")
        if update_s2(s2_scene, msavi_processed=True, msavi_file_path=f"/data/msavi_{s2_scene}.tif"):
            print("   ✓ MSAVI processed")
        
        print("\n3. Finding MSAVI for date...")
        msavi = find_msavi_for_date(datetime(2023,1,22), window_days=15)
        if msavi:
            print(f"   ✓ Found: offset={msavi['date_offset_days']} days")


def main():
    print("\n" + "="*80)
    print("DATABASE QUERY FUNCTIONS - EXAMPLE USAGE")
    print("="*80)
    
    if not init_db():
        print("\n⚠️ Database not initialized. Run: cd ../satelit_metadata && alembic upgrade head")
        return 1
    
    print("✓ Database ready\n")
    
    try:
        example_sentinel1()
        example_sentinel2()
        print("\n" + "="*80)
        print("✓ EXAMPLES COMPLETED")
        print("="*80 + "\n")
        return 0
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
