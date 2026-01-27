# How Goshawk_ETL Works - Complete Overview

## Purpose

**Urban Water Leak Detection using Satellite InSAR**

The Goshawk_ETL repository processes satellite data to detect underground water leaks in urban distribution pipelines by analyzing ground deformation patterns and soil moisture changes.

## Core Technologies

- **Sentinel-1** (SAR/Radar) - Ground deformation via InSAR interferometry
- **Sentinel-2** (Optical) - Soil moisture via MSAVI vegetation index
- **Database** - Centralized tracking of all processing states
- **SNAP GPT** - ESA's Sentinel Application Platform for InSAR processing

## Complete Workflow

### Phase 1: Download Satellite Data

**Sentinel-1 SLC (Single Look Complex)**
```bash
python scripts/download_copernicus.py \
    --collection SENTINEL-1 --product-type SLC \
    --track 110 --orbit DESCENDING \
    --date-start 2023-01-01 --date-end 2023-12-31
```
- Downloads to: `data/slc/`
- Registers in database: `slc_products` table
- ~4GB per image, 6-day repeat cycle

**Sentinel-2 L2A (Optical)**
```bash
python scripts/download_copernicus.py \
    --collection SENTINEL-2 --product-type L2A \
    --date-start 2023-01-01 --date-end 2023-12-31
```
- Downloads to: `data/sentinel2_l2a/`
- Registers in database: `s2_products` table
- ~800MB per image, 5-day repeat cycle

### Phase 2: Process InSAR Interferometry

**Short Baseline Pairs (Leak Detection)**
```bash
python scripts/process_insar_gpt.py \
    --track 110 --orbit DESCENDING --subswath IW1 \
    --short-baseline
```

What it does:
1. **Coregistration** - Align master and slave SLC images
2. **Interferogram** - Calculate phase difference between acquisitions
3. **Coherence** - Measure correlation quality (0-1 scale)
4. **Topographic Removal** - Remove elevation-induced phase
5. **Filtering** - Reduce noise
6. **Unwrapping** - Convert phase to deformation

Output:
- Location: `data/insar/short/`
- Database: Registers in `insar_pairs` table
- Time: ~8-12 hours for full year
- Products: Coherence, phase, interferograms

### Phase 3: Calculate MSAVI (Soil Moisture)

```bash
python scripts/process_sentinel2_msavi.py \
    --date-start 2023-01-01 --date-end 2023-12-31 \
    --aoi-geojson aoi/study_area.geojson
```

MSAVI Formula:
```
MSAVI = (2×NIR + 1 - √((2×NIR + 1)² - 8×(NIR - RED))) / 2
```

Output:
- Location: `data/sentinel2_msavi/`
- Database: Updates `s2_products.msavi_processed`
- Purpose: Differentiate leak signals from vegetation

### Phase 4: Crop to Urban AOI (NEW! Issue #8)

```bash
python scripts/batch_aoi_crop.py \
    --track 110 --orbit DESCENDING --subswath IW1 \
    --workspace /path/to/urban_area
```

What makes this special:
- **Database-driven**: Queries `get_insar_pairs()` to find products
- **No reprocessing**: Uses already-processed InSAR products
- **120-450x faster**: 2-5 minutes vs 10-15 hours
- **Flexible filtering**: By track, orbit, subswath, pair type

Output:
- Location: `data/cropped/T110_DESCENDING_IW1/`
- Format: Compressed GeoTIFF (LZW, tiled)
- Ready for leak detection analysis

### Phase 5: Leak Detection (Future)

Time series analysis of cropped products:
- Coherence loss patterns (water causes decorrelation)
- Phase anomalies (ground subsidence/uplift)
- MSAVI correlation (distinguish from vegetation)
- Leak probability mapping

## Database Architecture

### Central Database: satelit_metadata

**Why it's powerful:**
- Single source of truth for all satellite products
- Instant queries (milliseconds vs minutes of file scanning)
- Smart workflow (skip already processed)
- Complete processing history
- Shared across multiple repositories

### Tables

**slc_products** - Sentinel-1 raw products
- Tracks: downloads, processing per subswath (IW1/IW2/IW3)
- Fields: scene_id, track, orbit, downloaded, processed flags

**insar_pairs** - Processed interferograms
- Tracks: master-slave combinations, baselines, coherence
- Fields: file_path, temporal_baseline, coherence_mean
- Unique constraint: (master, slave, subswath, pair_type)

**s2_products** - Sentinel-2 optical products
- Tracks: downloads, MSAVI processing, cloud cover
- Fields: scene_id, cloud_cover, msavi_processed, msavi_file_path

**insar_pair_msavi** - InSAR-MSAVI integration (Future)
- Links InSAR pairs with temporally-matched MSAVI products

## Key Scripts

### Download
- `download_copernicus.py` - Download S1/S2 from Copernicus

### Database
- `db_integration.py` - Database initialization (Issue #1)
- `db_queries.py` - 13 query functions (Issue #2)
- `db_example_usage.py` - Complete examples

### Processing
- `process_insar_gpt.py` - InSAR interferometry (SNAP GPT)
- `process_sentinel2_msavi.py` - MSAVI calculation (Issue #6)
- `batch_aoi_crop.py` - Batch crop using database (Issue #8)

### Analysis
- `crop_insar_to_aoi.py` - Single product crop
- `extract_metrics_aoi.py` - Extract statistics

## Recent Improvements (Issues Implemented)

### Issue #1: Database Schema ✅
- Created tables: slc_products, insar_pairs, s2_products
- Foreign keys and unique constraints
- Migration via Alembic

### Issue #2: Database Helper Functions ✅
- 13 functions for querying and updating
- Full type hints and error handling
- Examples: `get_slc_status()`, `register_insar_pair()`, `get_insar_pairs()`

### Issue #6: Sentinel-2 Integration ✅
- Download registration in database
- MSAVI processing tracking
- Skip logic for processed products

### Issue #8: Batch AOI Crop ✅
- Database-driven cropping
- 120-450x faster than reprocessing
- Flexible filtering (track/orbit/subswath/pair-type)

## Why This Architecture Is Powerful

### Before (File-Based)
❌ Scan filesystem to find products (slow)  
❌ No visibility into processing status  
❌ Reprocess products unnecessarily  
❌ Hard to query specific combinations  
❌ No history or metadata  

### After (Database-Driven)
✅ Instant queries (milliseconds)  
✅ Full visibility of processing state  
✅ Smart skip logic (avoid redundant work)  
✅ Flexible queries (any combination of parameters)  
✅ Complete history and metadata  
✅ 120-450x faster workflows  

## Performance Comparison

### Scenario: Crop 50 InSAR pairs to urban AOI

**Old Method (Reprocess):**
- Download SLCs: 2-3 hours
- Process InSAR: 8-12 hours
- Crop manually: 1-2 hours
- **Total: 10-15 hours**

**New Method (Database):**
- Query database: <1 second
- Crop 50 products: 2-5 minutes
- **Total: 2-5 minutes**

**Speedup: 120-450x faster!** 🚀

## Quick Start for New Users

### 1. Setup Environment
```bash
conda env create -f environment.yml
conda activate goshawk
```

### 2. Setup Database
```bash
cd ../satelit_metadata
alembic upgrade head
cd ../goshawk_ETL
```

### 3. Download Test Data
```bash
python scripts/download_copernicus.py \
    --collection SENTINEL-1 --product-type SLC \
    --track 110 --orbit DESCENDING \
    --date-start 2023-01-01 --date-end 2023-01-31 \
    --aoi-geojson aoi/test_area.geojson
```

### 4. Process InSAR
```bash
python scripts/process_insar_gpt.py \
    --track 110 --orbit DESCENDING --subswath IW1 \
    --short-baseline
```

### 5. Crop to AOI
```bash
python scripts/batch_aoi_crop.py \
    --track 110 --orbit DESCENDING --subswath IW1 \
    --workspace /path/to/test_area
```

### 6. Check Results
```bash
ls -lh data/cropped/T110_DESCENDING_IW1/
```

## Directory Structure

```
goshawk_ETL/
├── data/                          # Satellite data storage
│   ├── slc/                       # Sentinel-1 SLC products
│   ├── insar/                     # Processed InSAR products
│   │   ├── short/                 # Short baseline pairs
│   │   └── long/                  # Long baseline pairs
│   ├── sentinel2_l2a/             # Sentinel-2 L2A products
│   ├── sentinel2_msavi/           # MSAVI indices
│   └── cropped/                   # Cropped to AOI
│       └── T110_DESCENDING_IW1/   # By track/orbit/subswath
│
├── aoi/                           # Areas of Interest
│   └── *.geojson                  # AOI definitions
│
├── scripts/                       # Processing scripts
│   ├── download_copernicus.py
│   ├── process_insar_gpt.py
│   ├── process_sentinel2_msavi.py
│   ├── batch_aoi_crop.py
│   ├── db_integration.py
│   └── db_queries.py
│
├── docs/                          # Documentation
│   ├── DATABASE_SCHEMA.md
│   ├── DB_HELPER_FUNCTIONS.md
│   ├── ISSUE_*_COMPLETION.md
│   └── *_QUICKSTART.md
│
└── logs/                          # Processing logs
```

## How to Check Status

### Check Downloaded SLCs
```python
from scripts.db_queries import get_slc_status

status = get_slc_status('S1A_IW_SLC__1SDV_20230115T061036_...')
if status:
    print(f"Downloaded: {status['downloaded']}")
    print(f"IW1 processed: {status['fullswath_iw1_processed']}")
```

### Check Processed InSAR Pairs
```python
from scripts.db_queries import get_insar_pairs

pairs = get_insar_pairs(110, 'DESCENDING', 'IW1', 'short')
print(f"Found {len(pairs)} short-baseline pairs")
for p in pairs[:3]:
    print(f"Baseline: {p['temporal_baseline_days']} days")
    print(f"Coherence: {p['coherence_mean']:.2f}")
```

### Check Sentinel-2 Products
```python
from scripts.db_queries import get_s2_status

status = get_s2_status('S2A_MSIL2A_20230115T105321_...')
if status:
    print(f"Cloud cover: {status['cloud_cover_percent']}%")
    print(f"MSAVI processed: {status['msavi_processed']}")
```

## Documentation

### Quick Start Guides
- `DB_QUICKSTART.md` - Database functions quick reference
- `BATCH_AOI_CROP_QUICKSTART.md` - Batch crop usage
- `S2_DATABASE_INTEGRATION.md` - S2 tracking overview

### Complete Documentation
- `DATABASE_SCHEMA.md` - Full schema definition
- `DB_HELPER_FUNCTIONS.md` - Complete API reference
- `ISSUE_*_COMPLETION.md` - Implementation details

### Examples
- `db_example_usage.py` - Complete workflow examples

## Related Repositories

**satelit_metadata:**
- Shared database schema and models
- Alembic migrations
- Common across all satellite processing repositories

**goshawk_ETL (this repo):**
- Sentinel-1 InSAR processing
- Sentinel-2 MSAVI processing
- Database integration
- Batch cropping

## Key Insights

1. **Database is Source of Truth** - Not the filesystem
2. **Query First** - Always check database before processing
3. **Register Everything** - Every download, every processing step
4. **Skip Smart** - Avoid redundant work automatically
5. **Batch Operations** - Process many products at once

## Questions?

- Check `docs/` directory for detailed guides
- Run scripts with `--help` for usage information
- See `db_example_usage.py` for complete examples
- Review issue completion documents for implementation details

---

**Repository Status:** Production-ready with database-driven workflows  
**Last Updated:** 2026-01-27  
**Recent Improvements:** Issues #1, #2, #6, #8 complete
