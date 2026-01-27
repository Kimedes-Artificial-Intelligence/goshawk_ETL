#!/usr/bin/env python3
"""
Script: batch_aoi_crop.py
Description: Batch crop InSAR products to AOI using database queries
             Fast "view operation" that doesn't need persistent DB tracking

This script:
1. Queries the database for processed InSAR pairs
2. Filters by track, orbit direction, subswath, pair type
3. Crops all matching products to the AOI
4. Saves cropped GeoTIFFs for urban analysis

Author: goshawk_ETL
Version: 1.0 (Issue #8)
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

import rasterio
from rasterio.mask import mask
from shapely import wkt
from shapely.geometry import mapping

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent))

from logging_utils import LoggerConfig
from db_queries import get_insar_pairs
from db_integration import init_db

logger = None


def crop_insar_to_aoi(
    insar_file: str,
    aoi_wkt: str,
    output_dir: str,
    band_pattern: str = 'coh'
) -> Optional[str]:
    """
    Crop a single InSAR product to AOI.
    
    Args:
        insar_file: Path to .dim file or .data directory
        aoi_wkt: WKT string of AOI geometry
        output_dir: Output directory for cropped products
        band_pattern: Pattern to find band (default: 'coh' for coherence)
        
    Returns:
        Path to cropped file if successful, None otherwise
    """
    try:
        # Determine product name
        if insar_file.endswith('.dim'):
            basename = os.path.basename(insar_file).replace('.dim', '')
            data_dir = insar_file.replace('.dim', '.data')
        else:
            # Assume it's already a .data directory
            data_dir = insar_file
            basename = os.path.basename(insar_file.replace('.data', ''))
        
        output_file = os.path.join(output_dir, f"{basename}_cropped.tif")
        
        # Skip if already exists
        if os.path.exists(output_file):
            logger.info(f"  ⏭️  Already cropped: {basename}")
            return output_file
        
        # Find coherence band (or other specified band)
        band_file = None
        if os.path.isdir(data_dir):
            for root, dirs, files in os.walk(data_dir):
                for f in files:
                    if band_pattern in f.lower() and f.endswith('.img'):
                        band_file = os.path.join(root, f)
                        break
                if band_file:
                    break
        
        if not band_file:
            logger.warning(f"  ⚠️  No {band_pattern} band found: {basename}")
            return None
        
        # Parse AOI geometry
        aoi_geom = wkt.loads(aoi_wkt)
        geoms = [mapping(aoi_geom)]
        
        # Open raster and crop
        with rasterio.open(band_file) as src:
            if src.crs is None:
                logger.warning(f"  ⚠️  No CRS in product: {basename}")
                return None
            
            # Crop to AOI
            out_image, out_transform = mask(src, geoms, crop=True, all_touched=True)
            out_meta = src.meta.copy()
            
            # Update metadata
            out_meta.update({
                "driver": "GTiff",
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "compress": "lzw",
                "tiled": True,
                "blockxsize": 512,
                "blockysize": 512
            })
            
            # Save cropped product
            with rasterio.open(output_file, "w", **out_meta) as dest:
                dest.write(out_image)
            
            original_size = src.shape[0] * src.shape[1]
            cropped_size = out_image.shape[1] * out_image.shape[2]
            reduction = (1 - cropped_size / original_size) * 100
            
            logger.info(f"  ✓ Cropped: {basename}")
            logger.info(f"    {src.shape[0]}x{src.shape[1]} → {out_image.shape[1]}x{out_image.shape[2]} "
                       f"({reduction:.1f}% reduction)")
            
            return output_file
            
    except Exception as e:
        logger.error(f"  ✗ Error cropping {basename}: {e}")
        return None


def batch_crop_by_query(
    track_number: int,
    orbit_direction: str,
    subswath: str,
    aoi_wkt: str,
    output_dir: str,
    pair_type: Optional[str] = None,
    band_pattern: str = 'coh'
) -> Dict[str, int]:
    """
    Batch crop InSAR products based on database query.
    
    Args:
        track_number: Track number (1-175)
        orbit_direction: ASCENDING or DESCENDING
        subswath: IW1, IW2, or IW3
        aoi_wkt: WKT string of AOI
        output_dir: Output directory
        pair_type: Optional filter for 'short' or 'long'
        band_pattern: Band to extract (default: 'coh')
        
    Returns:
        Dictionary with statistics
    """
    logger.info("=" * 80)
    logger.info("DATABASE QUERY")
    logger.info("=" * 80)
    logger.info(f"Track: {track_number}")
    logger.info(f"Orbit: {orbit_direction}")
    logger.info(f"Subswath: {subswath}")
    logger.info(f"Pair type: {pair_type or 'ALL'}")
    logger.info("")
    
    # Query database for InSAR pairs
    pairs = get_insar_pairs(
        track_number=track_number,
        orbit_direction=orbit_direction,
        subswath=subswath,
        pair_type=pair_type
    )
    
    if not pairs:
        logger.warning("No InSAR pairs found in database matching query")
        logger.info("\nPossible reasons:")
        logger.info("  - No products processed yet")
        logger.info("  - Wrong track/orbit/subswath combination")
        logger.info("  - Database not populated (run processing first)")
        return {"found": 0, "cropped": 0, "failed": 0, "skipped": 0}
    
    logger.info(f"✓ Found {len(pairs)} InSAR pair(s) in database")
    logger.info("")
    
    # Show summary of found pairs
    logger.info("Pairs to process:")
    for pair in pairs[:5]:  # Show first 5
        logger.info(f"  - {pair['master_scene_id'][:30]}... → {pair['slave_scene_id'][:30]}...")
        logger.info(f"    Temporal baseline: {pair['temporal_baseline_days']} days")
        if pair.get('coherence_mean'):
            logger.info(f"    Coherence: {pair['coherence_mean']:.2f}")
    
    if len(pairs) > 5:
        logger.info(f"  ... and {len(pairs) - 5} more")
    logger.info("")
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Process each pair
    logger.info("=" * 80)
    logger.info("CROPPING PRODUCTS")
    logger.info("=" * 80)
    
    stats = {
        "found": len(pairs),
        "cropped": 0,
        "failed": 0,
        "skipped": 0
    }
    
    for i, pair in enumerate(pairs, 1):
        logger.info(f"\n[{i}/{len(pairs)}] Processing pair {pair['id']}")
        logger.info(f"  Master: {pair['master_scene_id'][:40]}...")
        logger.info(f"  Slave:  {pair['slave_scene_id'][:40]}...")
        logger.info(f"  File: {os.path.basename(pair['file_path'])}")
        
        # Check if file exists
        if not os.path.exists(pair['file_path']):
            logger.warning(f"  ⚠️  File not found: {pair['file_path']}")
            stats['failed'] += 1
            continue
        
        # Crop the product
        result = crop_insar_to_aoi(
            insar_file=pair['file_path'],
            aoi_wkt=aoi_wkt,
            output_dir=output_dir,
            band_pattern=band_pattern
        )
        
        if result:
            if "Already cropped" in str(result):
                stats['skipped'] += 1
            else:
                stats['cropped'] += 1
        else:
            stats['failed'] += 1
    
    return stats


def main():
    global logger
    
    parser = argparse.ArgumentParser(
        description='Batch crop InSAR products using database query',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Crop all IW1 pairs for track 110 DESCENDING
  python scripts/batch_aoi_crop.py --track 110 --orbit DESCENDING --subswath IW1 --aoi-wkt "POLYGON(...)"
  
  # Crop only short-baseline pairs
  python scripts/batch_aoi_crop.py --track 110 --orbit DESCENDING --subswath IW1 --pair-type short --aoi-wkt "POLYGON(...)"
  
  # Use AOI from workspace config.txt
  python scripts/batch_aoi_crop.py --track 110 --orbit DESCENDING --subswath IW1 --workspace /path/to/aoi
        """
    )
    
    # Query parameters
    parser.add_argument('--track', type=int, required=True,
                       help='Track number (1-175)')
    parser.add_argument('--orbit', choices=['ASCENDING', 'DESCENDING'], required=True,
                       help='Orbit direction')
    parser.add_argument('--subswath', choices=['IW1', 'IW2', 'IW3'], required=True,
                       help='Subswath')
    parser.add_argument('--pair-type', choices=['short', 'long'],
                       help='Filter by pair type (optional)')
    
    # AOI specification
    aoi_group = parser.add_mutually_exclusive_group(required=True)
    aoi_group.add_argument('--aoi-wkt', type=str,
                          help='AOI as WKT string')
    aoi_group.add_argument('--workspace', type=str,
                          help='Workspace directory (reads AOI from config.txt)')
    
    # Output
    parser.add_argument('--output', type=str,
                       help='Output directory (default: data/cropped/{track}_{orbit}_{subswath})')
    parser.add_argument('--band', type=str, default='coh',
                       help='Band pattern to extract (default: coh)')
    
    args = parser.parse_args()
    
    # Setup logger
    logger = LoggerConfig.setup_script_logger('batch_aoi_crop')
    
    logger.info("=" * 80)
    logger.info("BATCH AOI CROP - DATABASE-DRIVEN")
    logger.info("=" * 80)
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    
    # Check database availability
    if not init_db():
        logger.error("Database not available!")
        logger.error("Please ensure:")
        logger.error("  1. satelit_metadata database is running")
        logger.error("  2. Alembic migration is applied: cd ../satelit_metadata && alembic upgrade head")
        logger.error("  3. InSAR products have been processed and registered")
        return 1
    
    logger.info("✓ Database connection established")
    logger.info("")
    
    # Get AOI
    if args.workspace:
        config_file = os.path.join(args.workspace, 'config.txt')
        if not os.path.exists(config_file):
            logger.error(f"Config file not found: {config_file}")
            return 1
        
        # Read AOI from config
        aoi_wkt = None
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('AOI='):
                    aoi_wkt = line.split('=', 1)[1].strip().strip('"')
                    break
        
        if not aoi_wkt:
            logger.error("AOI not found in config.txt")
            return 1
        
        logger.info(f"✓ Loaded AOI from workspace: {args.workspace}")
    else:
        aoi_wkt = args.aoi_wkt
        logger.info(f"✓ Using provided AOI WKT")
    
    logger.info(f"  AOI: {aoi_wkt[:80]}...")
    logger.info("")
    
    # Determine output directory
    if args.output:
        output_dir = args.output
    else:
        # Default: data/cropped/{track}_{orbit}_{subswath}
        output_dir = os.path.join(
            'data', 'cropped',
            f"T{args.track:03d}_{args.orbit}_{args.subswath}"
        )
    
    logger.info(f"Output directory: {output_dir}")
    logger.info("")
    
    # Run batch crop
    stats = batch_crop_by_query(
        track_number=args.track,
        orbit_direction=args.orbit,
        subswath=args.subswath,
        aoi_wkt=aoi_wkt,
        output_dir=output_dir,
        pair_type=args.pair_type,
        band_pattern=args.band
    )
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Pairs found in DB:    {stats['found']}")
    logger.info(f"Successfully cropped: {stats['cropped']}")
    logger.info(f"Already existed:      {stats['skipped']}")
    logger.info(f"Failed:               {stats['failed']}")
    logger.info(f"Output directory:     {output_dir}")
    logger.info("")
    
    if stats['cropped'] > 0 or stats['skipped'] > 0:
        logger.info("✓ Cropping completed successfully")
        logger.info(f"  Total products available: {stats['cropped'] + stats['skipped']}")
    
    if stats['failed'] > 0:
        logger.warning(f"⚠️  {stats['failed']} product(s) failed to crop")
    
    logger.info("")
    logger.info(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)
    
    return 0 if stats['failed'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
