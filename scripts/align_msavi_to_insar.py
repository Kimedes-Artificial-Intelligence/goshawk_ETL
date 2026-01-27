#!/usr/bin/env python3
"""
Script: align_msavi_to_insar.py
Description: Aligns Sentinel-2 products (indices and raw bands) to InSAR pairs

ISSUE #7: Implement MSAVI-InSAR Alignment and DB Linking (Enhanced)

Processes for each InSAR pair:
- Vegetation indices: MSAVI, NDVI, NDMI
- Raw bands: B04 (RED), B08 (NIR), B11 (SWIR)

All products aligned to InSAR grid (pixel-perfect match) using bilinear interpolation.

Workflow:
1. Query all processed InSAR pairs from database
2. For each pair (master_date, slave_date):
   - Find closest S2 product with MSAVI within ±N days (configurable window)
   - For master and slave:
     a. Align MSAVI (pre-processed)
     b. Extract and align raw bands B04, B08, B11 from .SAFE
     c. Calculate NDVI = (NIR - RED) / (NIR + RED)
     d. Calculate NDMI = (NIR - SWIR) / (NIR + SWIR)
   - Register relationship in insar_pair_msavi table
3. Report statistics: aligned pairs, missing S2, products created

Usage:
  # Process all InSAR pairs
  python scripts/align_msavi_to_insar.py --track 110 --orbit ASCENDING --subswath IW1

  # Process specific date range
  python scripts/align_msavi_to_insar.py --track 110 --orbit ASCENDING --subswath IW1 \
      --start-date 2025-01-01 --end-date 2025-12-31

  # Custom temporal window
  python scripts/align_msavi_to_insar.py --track 110 --orbit ASCENDING --subswath IW1 \
      --window-days 5 --max-cloud-cover 20

  # Dry run (no DB updates)
  python scripts/align_msavi_to_insar.py --track 110 --orbit ASCENDING --subswath IW1 --dry-run
"""

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling, calculate_default_transform
from rasterio.mask import mask as rasterio_mask
from shapely.geometry import box
import glob

# Add scripts to path
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from logging_utils import LoggerConfig

# Database integration
try:
    from db_queries import (
        get_insar_pairs, find_msavi_for_date, register_pair_msavi,
        get_slc_status, get_s2_status
    )
    from db_integration import init_db
    DB_AVAILABLE = init_db()
except ImportError as e:
    print(f"❌ Database not available: {e}")
    print("   Make sure satelit_db is installed and configured")
    sys.exit(1)

logger = None


def extract_insar_grid_info(insar_dim_path: Path) -> Optional[Dict]:
    """
    Extract grid information from InSAR product for alignment.

    Args:
        insar_dim_path: Path to InSAR .dim file

    Returns:
        Dict with transform, crs, width, height, bounds
    """
    try:
        # Find a geotiff in the .data directory
        data_dir = insar_dim_path.with_suffix('.data')
        if not data_dir.exists():
            logger.warning(f"InSAR .data directory not found: {data_dir}")
            return None

        # Look for any .img or .tif file
        img_files = list(data_dir.glob('*.img')) + list(data_dir.glob('*.tif'))
        if not img_files:
            logger.warning(f"No raster files found in {data_dir}")
            return None

        # Use first file to get grid info
        with rasterio.open(img_files[0]) as src:
            return {
                'transform': src.transform,
                'crs': src.crs,
                'width': src.width,
                'height': src.height,
                'bounds': src.bounds
            }
    except Exception as e:
        logger.error(f"Failed to extract InSAR grid info: {e}")
        return None


def find_s2_band_file(s2_safe_path: Path, band_name: str) -> Optional[Path]:
    """
    Find Sentinel-2 band file within .SAFE directory.

    Args:
        s2_safe_path: Path to S2 .SAFE directory
        band_name: Band name (e.g., 'B04', 'B08', 'B11')

    Returns:
        Path to band JP2 file or None
    """
    # S2 structure: .SAFE/GRANULE/*/IMG_DATA/R10m/*_B04_10m.jp2
    #                                        /R20m/*_B11_20m.jp2

    resolution_map = {
        'B04': 'R10m',  # RED - 10m
        'B08': 'R10m',  # NIR - 10m
        'B11': 'R20m',  # SWIR - 20m
        'B8A': 'R20m',  # NIR narrow - 20m
    }

    resolution = resolution_map.get(band_name, 'R10m')

    # Search in GRANULE subdirectories
    pattern = str(s2_safe_path / 'GRANULE' / '*' / 'IMG_DATA' / resolution / f'*_{band_name}_*.jp2')
    matches = glob.glob(pattern)

    if matches:
        return Path(matches[0])

    logger.warning(f"  ⚠️  Band {band_name} not found in {s2_safe_path.name}")
    return None


def calculate_index(
    band1_data: np.ndarray,
    band2_data: np.ndarray,
    index_type: str
) -> np.ndarray:
    """
    Calculate vegetation/water index from band data.

    Args:
        band1_data: First band array
        band2_data: Second band array
        index_type: 'NDVI', 'NDMI', or 'MSAVI'

    Returns:
        Index array (float32)
    """
    # Avoid division by zero
    epsilon = 1e-10

    if index_type == 'NDVI':
        # NDVI = (NIR - RED) / (NIR + RED)
        nir = band1_data.astype(np.float32)
        red = band2_data.astype(np.float32)
        ndvi = (nir - red) / (nir + red + epsilon)
        return np.clip(ndvi, -1, 1)

    elif index_type == 'NDMI':
        # NDMI = (NIR - SWIR) / (NIR + SWIR)
        nir = band1_data.astype(np.float32)
        swir = band2_data.astype(np.float32)
        ndmi = (nir - swir) / (nir + swir + epsilon)
        return np.clip(ndmi, -1, 1)

    elif index_type == 'MSAVI':
        # MSAVI = (2*NIR + 1 - sqrt((2*NIR + 1)² - 8*(NIR - RED))) / 2
        nir = band1_data.astype(np.float32)
        red = band2_data.astype(np.float32)

        term1 = 2 * nir + 1
        term2 = np.sqrt(term1**2 - 8 * (nir - red) + epsilon)
        msavi = (term1 - term2) / 2

        return np.clip(msavi, -1, 1)

    else:
        raise ValueError(f"Unknown index type: {index_type}")


def align_raster_to_insar(
    source_path: Path,
    insar_grid: Dict,
    output_path: Path,
    band_name: str = None
) -> bool:
    """
    Align any raster to InSAR grid (reproject + resample).

    Args:
        source_path: Path to source raster (GeoTIFF or JP2)
        insar_grid: Grid info from InSAR product (transform, crs, etc.)
        output_path: Path for aligned output
        band_name: Optional band description for logging

    Returns:
        True if successful, False otherwise
    """
    try:
        if not source_path.exists():
            logger.error(f"Source file not found: {source_path}")
            return False

        # Create output directory
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with rasterio.open(source_path) as src:
            # Read data
            source_data = src.read(1)

            # Prepare output array
            aligned_data = np.zeros((insar_grid['height'], insar_grid['width']), dtype=np.float32)

            # Reproject to InSAR grid
            reproject(
                source=source_data,
                destination=aligned_data,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=insar_grid['transform'],
                dst_crs=insar_grid['crs'],
                resampling=Resampling.bilinear
            )

            # Write aligned raster
            profile = {
                'driver': 'GTiff',
                'dtype': np.float32,
                'width': insar_grid['width'],
                'height': insar_grid['height'],
                'count': 1,
                'crs': insar_grid['crs'],
                'transform': insar_grid['transform'],
                'compress': 'lzw',
                'tiled': True,
                'blockxsize': 256,
                'blockysize': 256
            }

            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(aligned_data, 1)

        band_desc = f" ({band_name})" if band_name else ""
        logger.info(f"  ✓ Aligned{band_desc}: {output_path.name}")
        return True

    except Exception as e:
        logger.error(f"Failed to align raster: {e}")
        return False


def align_msavi_to_insar(
    msavi_path: Path,
    insar_grid: Dict,
    output_path: Path
) -> bool:
    """
    Align MSAVI raster to InSAR grid (reproject + resample).

    DEPRECATED: Use align_raster_to_insar instead.

    Args:
        msavi_path: Path to MSAVI GeoTIFF
        insar_grid: Grid info from InSAR product (transform, crs, etc.)
        output_path: Path for aligned MSAVI output

    Returns:
        True if successful, False otherwise
    """
    return align_raster_to_insar(msavi_path, insar_grid, output_path, "MSAVI")
    try:
        if not msavi_path.exists():
            logger.error(f"MSAVI file not found: {msavi_path}")
            return False

        # Create output directory
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with rasterio.open(msavi_path) as src:
            # Read MSAVI data
            msavi_data = src.read(1)

            # Prepare output array
            aligned_data = np.zeros((insar_grid['height'], insar_grid['width']), dtype=np.float32)

            # Reproject MSAVI to InSAR grid
            reproject(
                source=msavi_data,
                destination=aligned_data,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=insar_grid['transform'],
                dst_crs=insar_grid['crs'],
                resampling=Resampling.bilinear
            )

            # Write aligned MSAVI
            profile = {
                'driver': 'GTiff',
                'dtype': np.float32,
                'width': insar_grid['width'],
                'height': insar_grid['height'],
                'count': 1,
                'crs': insar_grid['crs'],
                'transform': insar_grid['transform'],
                'compress': 'lzw',
                'tiled': True,
                'blockxsize': 256,
                'blockysize': 256
            }

            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(aligned_data, 1)

        logger.info(f"  ✓ Aligned MSAVI saved: {output_path.name}")
        return True

    except Exception as e:
        logger.error(f"Failed to align MSAVI: {e}")
        return False


def process_insar_pair(
    pair: Dict,
    window_days: int,
    max_cloud_cover: Optional[float],
    output_base_dir: Path,
    dry_run: bool
) -> Dict:
    """
    Process single InSAR pair: find MSAVI, align, register.

    Returns:
        Dict with status: 'aligned', 'no_msavi_master', 'no_msavi_slave', 'alignment_failed'
    """
    pair_id = pair['id']
    master_scene_id = pair['master_scene_id']
    slave_scene_id = pair['slave_scene_id']

    # Get acquisition dates from SLC products
    master_slc = get_slc_status(master_scene_id)
    slave_slc = get_slc_status(slave_scene_id)

    if not master_slc or not slave_slc:
        logger.error(f"  ✗ Cannot find SLC records for pair {pair_id}")
        return {'status': 'error', 'reason': 'missing_slc'}

    master_date = master_slc['acquisition_date']
    slave_date = slave_slc['acquisition_date']

    logger.info(f"\nProcessing pair {pair_id}: {master_date.date()} → {slave_date.date()}")

    # Find MSAVI for master date
    master_msavi = find_msavi_for_date(
        target_date=master_date,
        window_days=window_days,
        max_cloud_cover=max_cloud_cover
    )

    if not master_msavi:
        logger.warning(f"  ⚠️  No MSAVI found for master date (±{window_days} days)")
        return {'status': 'no_msavi_master'}

    # Find MSAVI for slave date
    slave_msavi = find_msavi_for_date(
        target_date=slave_date,
        window_days=window_days,
        max_cloud_cover=max_cloud_cover
    )

    if not slave_msavi:
        logger.warning(f"  ⚠️  No MSAVI found for slave date (±{window_days} days)")
        return {'status': 'no_msavi_slave'}

    logger.info(f"  ✓ Found MSAVI master: {master_msavi['scene_id'][:40]}... (offset: {master_msavi['date_offset_days']} days)")
    logger.info(f"  ✓ Found MSAVI slave:  {slave_msavi['scene_id'][:40]}... (offset: {slave_msavi['date_offset_days']} days)")

    if dry_run:
        logger.info(f"  [DRY RUN] Would align and register MSAVI pair")
        return {
            'status': 'would_align',
            'master_s2_id': master_msavi['id'],
            'slave_s2_id': slave_msavi['id']
        }

    # Extract InSAR grid info
    insar_path = Path(pair['file_path'])
    insar_grid = extract_insar_grid_info(insar_path)

    if not insar_grid:
        logger.error(f"  ✗ Cannot extract InSAR grid info")
        return {'status': 'alignment_failed', 'reason': 'no_grid_info'}

    # Prepare output directory
    pair_name = f"{master_date.strftime('%Y%m%d')}_{slave_date.strftime('%Y%m%d')}"
    aligned_dir = output_base_dir / f"aligned_s2/{pair['orbit_direction'].lower()}_{pair['subswath'].lower()}/t{pair['track_number']:03d}/{pair['pair_type']}"
    aligned_dir.mkdir(parents=True, exist_ok=True)

    # Get S2 .SAFE paths
    master_s2_path = Path(master_msavi['file_path'])  # S2 .SAFE directory
    slave_s2_path = Path(slave_msavi['file_path'])

    logger.info(f"  Processing S2 products:")
    logger.info(f"    Master: {master_s2_path.name[:50]}...")
    logger.info(f"    Slave:  {slave_s2_path.name[:50]}...")

    # === MASTER: Process indices and raw bands ===
    master_products = {}

    # 1. Align MSAVI (already processed)
    master_msavi_path = Path(master_msavi['msavi_file_path'])
    aligned_master_msavi = aligned_dir / f"MSAVI_{pair_name}_master.tif"
    if align_raster_to_insar(master_msavi_path, insar_grid, aligned_master_msavi, "MSAVI"):
        master_products['msavi'] = str(aligned_master_msavi)

    # 2. Extract and align raw bands (B04, B08, B11)
    for band_name in ['B04', 'B08', 'B11']:
        band_file = find_s2_band_file(master_s2_path, band_name)
        if band_file:
            aligned_band = aligned_dir / f"{band_name}_{pair_name}_master.tif"
            if align_raster_to_insar(band_file, insar_grid, aligned_band, band_name):
                master_products[band_name.lower()] = str(aligned_band)

    # 3. Calculate NDVI from aligned bands (if available)
    if 'b08' in master_products and 'b04' in master_products:
        logger.info(f"  Calculating NDVI (master)...")
        with rasterio.open(master_products['b08']) as nir_src, \
             rasterio.open(master_products['b04']) as red_src:
            nir_data = nir_src.read(1)
            red_data = red_src.read(1)
            ndvi_data = calculate_index(nir_data, red_data, 'NDVI')

            aligned_ndvi = aligned_dir / f"NDVI_{pair_name}_master.tif"
            profile = nir_src.profile.copy()
            profile.update(dtype=np.float32, compress='lzw')

            with rasterio.open(aligned_ndvi, 'w', **profile) as dst:
                dst.write(ndvi_data, 1)

            master_products['ndvi'] = str(aligned_ndvi)
            logger.info(f"  ✓ Calculated NDVI: {aligned_ndvi.name}")

    # 4. Calculate NDMI from aligned bands (if available)
    if 'b08' in master_products and 'b11' in master_products:
        logger.info(f"  Calculating NDMI (master)...")
        with rasterio.open(master_products['b08']) as nir_src, \
             rasterio.open(master_products['b11']) as swir_src:
            nir_data = nir_src.read(1)
            swir_data = swir_src.read(1)
            ndmi_data = calculate_index(nir_data, swir_data, 'NDMI')

            aligned_ndmi = aligned_dir / f"NDMI_{pair_name}_master.tif"
            profile = nir_src.profile.copy()
            profile.update(dtype=np.float32, compress='lzw')

            with rasterio.open(aligned_ndmi, 'w', **profile) as dst:
                dst.write(ndmi_data, 1)

            master_products['ndmi'] = str(aligned_ndmi)
            logger.info(f"  ✓ Calculated NDMI: {aligned_ndmi.name}")

    # === SLAVE: Process indices and raw bands ===
    slave_products = {}

    # 1. Align MSAVI
    slave_msavi_path = Path(slave_msavi['msavi_file_path'])
    aligned_slave_msavi = aligned_dir / f"MSAVI_{pair_name}_slave.tif"
    if align_raster_to_insar(slave_msavi_path, insar_grid, aligned_slave_msavi, "MSAVI"):
        slave_products['msavi'] = str(aligned_slave_msavi)

    # 2. Extract and align raw bands
    for band_name in ['B04', 'B08', 'B11']:
        band_file = find_s2_band_file(slave_s2_path, band_name)
        if band_file:
            aligned_band = aligned_dir / f"{band_name}_{pair_name}_slave.tif"
            if align_raster_to_insar(band_file, insar_grid, aligned_band, band_name):
                slave_products[band_name.lower()] = str(aligned_band)

    # 3. Calculate NDVI
    if 'b08' in slave_products and 'b04' in slave_products:
        logger.info(f"  Calculating NDVI (slave)...")
        with rasterio.open(slave_products['b08']) as nir_src, \
             rasterio.open(slave_products['b04']) as red_src:
            nir_data = nir_src.read(1)
            red_data = red_src.read(1)
            ndvi_data = calculate_index(nir_data, red_data, 'NDVI')

            aligned_ndvi = aligned_dir / f"NDVI_{pair_name}_slave.tif"
            profile = nir_src.profile.copy()
            profile.update(dtype=np.float32, compress='lzw')

            with rasterio.open(aligned_ndvi, 'w', **profile) as dst:
                dst.write(ndvi_data, 1)

            slave_products['ndvi'] = str(aligned_ndvi)
            logger.info(f"  ✓ Calculated NDVI: {aligned_ndvi.name}")

    # 4. Calculate NDMI
    if 'b08' in slave_products and 'b11' in slave_products:
        logger.info(f"  Calculating NDMI (slave)...")
        with rasterio.open(slave_products['b08']) as nir_src, \
             rasterio.open(slave_products['b11']) as swir_src:
            nir_data = nir_src.read(1)
            swir_data = swir_src.read(1)
            ndmi_data = calculate_index(nir_data, swir_data, 'NDMI')

            aligned_ndmi = aligned_dir / f"NDMI_{pair_name}_slave.tif"
            profile = nir_src.profile.copy()
            profile.update(dtype=np.float32, compress='lzw')

            with rasterio.open(aligned_ndmi, 'w', **profile) as dst:
                dst.write(ndmi_data, 1)

            slave_products['ndmi'] = str(aligned_ndmi)
            logger.info(f"  ✓ Calculated NDMI: {aligned_ndmi.name}")

    # Check minimum requirements
    if 'msavi' not in master_products or 'msavi' not in slave_products:
        return {'status': 'alignment_failed', 'reason': 'msavi_alignment'}

    # Register in database (using MSAVI files)
    integration_id = register_pair_msavi(
        insar_pair_id=pair_id,
        master_s2_id=master_msavi['id'],
        slave_s2_id=slave_msavi['id'],
        master_msavi_file=master_products['msavi'],
        slave_msavi_file=slave_products['msavi'],
        master_date_offset_days=master_msavi['date_offset_days'],
        slave_date_offset_days=slave_msavi['date_offset_days']
    )

    if integration_id:
        logger.info(f"  ✓ Registered in DB (integration_id={integration_id})")
        logger.info(f"  Products created:")
        logger.info(f"    Master: {len(master_products)} files (MSAVI, {', '.join([k.upper() for k in master_products.keys() if k != 'msavi'])})")
        logger.info(f"    Slave:  {len(slave_products)} files (MSAVI, {', '.join([k.upper() for k in slave_products.keys() if k != 'msavi'])})")

        return {
            'status': 'aligned',
            'integration_id': integration_id,
            'master_s2_id': master_msavi['id'],
            'slave_s2_id': slave_msavi['id'],
            'master_products': master_products,
            'slave_products': slave_products
        }
    else:
        logger.warning(f"  ⚠️  Failed to register in database")
        return {'status': 'registration_failed'}


def main():
    parser = argparse.ArgumentParser(
        description='Align Sentinel-2 MSAVI products to InSAR pairs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('--track', type=int, required=True,
                        help='Track number (1-175)')
    parser.add_argument('--orbit', choices=['ASCENDING', 'DESCENDING'], required=True,
                        help='Orbit direction')
    parser.add_argument('--subswath', choices=['IW1', 'IW2', 'IW3'], required=True,
                        help='Subswath to process')
    parser.add_argument('--pair-type', choices=['short', 'long'],
                        help='Process only specific pair type (default: all)')
    parser.add_argument('--start-date', type=str,
                        help='Start date filter (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str,
                        help='End date filter (YYYY-MM-DD)')
    parser.add_argument('--window-days', type=int, default=15,
                        help='Temporal window for MSAVI search (±days, default: 15)')
    parser.add_argument('--max-cloud-cover', type=float,
                        help='Maximum cloud cover percentage (default: no filter)')
    parser.add_argument('--output-dir', type=str,
                        default='/mnt/satelit_data/aligned_products',
                        help='Base directory for aligned MSAVI outputs')
    parser.add_argument('--dry-run', action='store_true',
                        help='Dry run: show what would be processed without executing')

    args = parser.parse_args()

    # Setup logging
    global logger
    log_config = LoggerConfig(
        log_file=f"logs/align_msavi_t{args.track:03d}_{args.orbit.lower()}_{args.subswath.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        console_level='INFO',
        file_level='DEBUG'
    )
    logger = log_config.get_logger()

    logger.info("=" * 80)
    logger.info("Sentinel-2 Products Alignment to InSAR (Issue #7 Enhanced)")
    logger.info("=" * 80)
    logger.info("Products: MSAVI, NDVI, NDMI, B04, B08, B11")
    logger.info(f"Track: {args.track}")
    logger.info(f"Orbit: {args.orbit}")
    logger.info(f"Subswath: {args.subswath}")
    logger.info(f"Temporal window: ±{args.window_days} days")
    if args.max_cloud_cover:
        logger.info(f"Max cloud cover: {args.max_cloud_cover}%")
    if args.dry_run:
        logger.info("DRY RUN MODE - No files will be created or DB updated")
    logger.info("")

    if not DB_AVAILABLE:
        logger.error("❌ Database not available - cannot proceed")
        return 1

    # Query InSAR pairs from database
    logger.info("Querying InSAR pairs from database...")
    pairs = get_insar_pairs(
        track_number=args.track,
        orbit_direction=args.orbit,
        subswath=args.subswath,
        pair_type=args.pair_type
    )

    if not pairs:
        logger.warning(f"No InSAR pairs found for track {args.track}, {args.orbit}, {args.subswath}")
        return 0

    logger.info(f"Found {len(pairs)} InSAR pairs to process\n")

    # Filter by date if specified
    if args.start_date or args.end_date:
        start_dt = datetime.strptime(args.start_date, '%Y-%m-%d') if args.start_date else datetime.min
        end_dt = datetime.strptime(args.end_date, '%Y-%m-%d') if args.end_date else datetime.max

        original_count = len(pairs)
        pairs = [p for p in pairs if start_dt <= p.get('master_acquisition_date', datetime.min) <= end_dt]
        logger.info(f"Date filter: {original_count} → {len(pairs)} pairs\n")

    # Process each pair
    output_base_dir = Path(args.output_dir)

    stats = {
        'aligned': 0,
        'no_msavi_master': 0,
        'no_msavi_slave': 0,
        'alignment_failed': 0,
        'registration_failed': 0,
        'error': 0,
        'would_align': 0
    }

    for idx, pair in enumerate(pairs, 1):
        logger.info(f"[{idx}/{len(pairs)}] Processing pair ID {pair['id']}")

        result = process_insar_pair(
            pair=pair,
            window_days=args.window_days,
            max_cloud_cover=args.max_cloud_cover,
            output_base_dir=output_base_dir,
            dry_run=args.dry_run
        )

        status = result['status']
        stats[status] = stats.get(status, 0) + 1

    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total pairs processed: {len(pairs)}")

    if args.dry_run:
        logger.info(f"  Would align: {stats['would_align']}")
    else:
        logger.info(f"  ✓ Successfully aligned: {stats['aligned']}")

    logger.info(f"  ⚠️  No MSAVI for master: {stats['no_msavi_master']}")
    logger.info(f"  ⚠️  No MSAVI for slave: {stats['no_msavi_slave']}")
    logger.info(f"  ✗ Alignment failed: {stats['alignment_failed']}")
    logger.info(f"  ✗ Registration failed: {stats['registration_failed']}")
    logger.info(f"  ✗ Errors: {stats['error']}")

    if stats['aligned'] > 0:
        logger.info(f"\n✅ {stats['aligned']} InSAR pairs successfully linked with MSAVI")

    if stats['no_msavi_master'] + stats['no_msavi_slave'] > 0:
        logger.info(f"\n⚠️  Consider downloading more Sentinel-2 products or increasing --window-days")

    return 0 if stats['error'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
