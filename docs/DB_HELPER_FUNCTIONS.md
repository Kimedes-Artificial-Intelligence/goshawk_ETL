# Database Helper Functions - API Documentation

## Overview

This document describes the database query and update helper functions for interacting with the granular tracking tables.

## Module: `scripts/db_queries.py`

### Sentinel-1 Functions

#### `get_slc_status(scene_id: str) -> Optional[Dict[str, Any]]`

Get the complete status of an SLC product.

**Parameters:**
- `scene_id` - Sentinel-1 scene identifier

**Returns:**
Dictionary with all SLC fields including processing flags, or None if not found.

**Example:**
```python
from scripts.db_queries import get_slc_status

status = get_slc_status('S1A_IW_SLC__1SDV_...')
if status:
    print(f"Downloaded: {status['downloaded']}")
    print(f"IW1 processed: {status['fullswath_iw1_processed']}")
    print(f"IW2 processed: {status['fullswath_iw2_processed']}")
    print(f"Polarimetry: {status['polarimetry_processed']}")
```

---

#### `update_slc(scene_id: str, **kwargs) -> bool`

Update an SLC product with new flags and timestamps.

**Parameters:**
- `scene_id` - Sentinel-1 scene identifier
- `**kwargs` - Fields to update (any column from slc_products table)

**Returns:**
True if update succeeded, False otherwise.

**Example:**
```python
from scripts.db_queries import update_slc
from datetime import datetime

# Mark IW1 as processed
update_slc(
    'S1A_IW_SLC__1SDV_...',
    fullswath_iw1_processed=True,
    fullswath_iw1_date=datetime.now(),
    fullswath_iw1_version='1.0.0'
)

# Mark polarimetry as processed
update_slc(
    'S1A_IW_SLC__1SDV_...',
    polarimetry_processed=True,
    polarimetry_date=datetime.now(),
    polarimetry_version='1.0.0'
)
```

---

#### `register_slc_download(...) -> Optional[int]`

Register a downloaded SLC product or update existing record.

**Parameters:**
- `scene_id` - Sentinel-1 scene identifier
- `acquisition_date` - Acquisition datetime
- `orbit_direction` - ASCENDING or DESCENDING
- `track_number` - Track number (1-175)
- `file_path` - Path to downloaded SAFE file
- `satellite_id` - Optional: S1A, S1B, S1C (auto-detected if None)
- `subswath` - Optional: IW1, IW2, IW3, or IW (default: IW)

**Returns:**
Product database ID, or None on failure.

**Example:**
```python
from scripts.db_queries import register_slc_download
from datetime import datetime

slc_id = register_slc_download(
    scene_id='S1A_IW_SLC__1SDV_20230115T060136_20230115T060203_046714_059C5B_F5B0',
    acquisition_date=datetime(2023, 1, 15, 6, 1, 36),
    orbit_direction='ASCENDING',
    track_number=117,
    file_path='/data/slc/S1A_IW_SLC__1SDV_20230115T060136_20230115T060203_046714_059C5B_F5B0.SAFE'
)
print(f"SLC registered with ID: {slc_id}")
```

---

#### `insar_pair_exists(...) -> bool`

Check if an InSAR pair already exists in the database.

**Parameters:**
- `master_scene_id` - Master SLC scene ID
- `slave_scene_id` - Slave SLC scene ID
- `subswath` - IW1, IW2, or IW3
- `pair_type` - 'short' or 'long'

**Returns:**
True if pair exists, False otherwise.

**Example:**
```python
from scripts.db_queries import insar_pair_exists

exists = insar_pair_exists(
    master_scene_id='S1A_IW_SLC__1SDV_...',
    slave_scene_id='S1A_IW_SLC__1SDV_...',
    subswath='IW1',
    pair_type='short'
)

if not exists:
    # Process the pair
    pass
```

---

#### `register_insar_pair(...) -> Optional[int]`

Register a successfully processed InSAR pair.

**Parameters:**
- `master_scene_id` - Master SLC scene ID
- `slave_scene_id` - Slave SLC scene ID
- `pair_type` - 'short' or 'long'
- `subswath` - IW1, IW2, or IW3
- `temporal_baseline_days` - Temporal baseline in days
- `file_path` - Path to processed .dim file
- `perpendicular_baseline_m` - Optional: Perpendicular baseline in meters
- `coherence_mean` - Optional: Mean coherence value
- `coherence_std` - Optional: Standard deviation of coherence
- `processing_version` - Optional: Processing software version

**Returns:**
InSAR pair database ID, or None on failure.

**Example:**
```python
from scripts.db_queries import register_insar_pair

pair_id = register_insar_pair(
    master_scene_id='S1A_IW_SLC__1SDV_...',
    slave_scene_id='S1A_IW_SLC__1SDV_...',
    pair_type='short',
    subswath='IW1',
    temporal_baseline_days=12,
    file_path='/data/insar/master_slave_IW1.dim',
    perpendicular_baseline_m=45.2,
    coherence_mean=0.65,
    coherence_std=0.18,
    processing_version='SNAP-10.0'
)
```

---

#### `get_insar_pairs(...) -> List[Dict[str, Any]]`

Get list of processed InSAR pairs for a specific track/orbit/subswath.

**Parameters:**
- `track_number` - Track number (1-175)
- `orbit_direction` - ASCENDING or DESCENDING
- `subswath` - IW1, IW2, or IW3
- `pair_type` - Optional: Filter for 'short' or 'long' (None = all)

**Returns:**
List of dictionaries with pair information.

**Example:**
```python
from scripts.db_queries import get_insar_pairs

pairs = get_insar_pairs(
    track_number=117,
    orbit_direction='ASCENDING',
    subswath='IW1',
    pair_type='short'
)

for pair in pairs:
    print(f"Pair {pair['id']}: {pair['master_scene_id']} -> {pair['slave_scene_id']}")
    print(f"  Temporal baseline: {pair['temporal_baseline_days']} days")
    print(f"  Coherence: {pair['coherence_mean']:.2f}")
    print(f"  File: {pair['file_path']}")
```

### Sentinel-2 Functions

#### `get_s2_status(scene_id: str) -> Optional[Dict[str, Any]]`

Get the status of a Sentinel-2 product.

**Parameters:**
- `scene_id` - Sentinel-2 scene identifier

**Returns:**
Dictionary with S2 product information, or None if not found.

**Example:**
```python
from scripts.db_queries import get_s2_status

status = get_s2_status('S2A_MSIL2A_...')
if status:
    print(f"Cloud cover: {status['cloud_cover_percent']}%")
    print(f"MSAVI processed: {status['msavi_processed']}")
```

---

#### `update_s2(scene_id: str, **kwargs) -> bool`

Update a Sentinel-2 product with new flags and timestamps.

**Parameters:**
- `scene_id` - Sentinel-2 scene identifier
- `**kwargs` - Fields to update

**Returns:**
True if update succeeded, False otherwise.

**Example:**
```python
from scripts.db_queries import update_s2
from datetime import datetime

update_s2(
    'S2A_MSIL2A_...',
    msavi_processed=True,
    msavi_file_path='/data/msavi/S2A_MSIL2A_..._MSAVI.tif',
    msavi_date=datetime.now(),
    msavi_version='1.0.0'
)
```

---

#### `register_s2_download(...) -> Optional[int]`

Register a downloaded Sentinel-2 product or update existing record.

**Parameters:**
- `scene_id` - Sentinel-2 scene identifier
- `acquisition_date` - Acquisition datetime
- `file_path` - Path to downloaded product
- `satellite_id` - Optional: S2A or S2B (auto-detected if None)
- `cloud_cover_percent` - Optional: Cloud coverage percentage
- `aoi_coverage_percent` - Optional: AOI coverage percentage

**Returns:**
Product database ID, or None on failure.

**Example:**
```python
from scripts.db_queries import register_s2_download
from datetime import datetime

s2_id = register_s2_download(
    scene_id='S2A_MSIL2A_20230120T105321_N0509_R051_T31TDF_20230120T170301',
    acquisition_date=datetime(2023, 1, 20, 10, 53, 21),
    file_path='/data/s2/S2A_MSIL2A_20230120T105321_N0509_R051_T31TDF_20230120T170301.SAFE',
    cloud_cover_percent=12.5,
    aoi_coverage_percent=98.3
)
```

---

#### `find_msavi_for_date(...) -> Optional[Dict[str, Any]]`

Find Sentinel-2 product with MSAVI closest to a specific date.

**Parameters:**
- `target_date` - Target date to find closest S2 product
- `window_days` - Search window in days (default: ±15 days)
- `max_cloud_cover` - Optional: Maximum cloud cover percentage filter

**Returns:**
Dictionary with S2 product information and date offset, or None if not found.

**Example:**
```python
from scripts.db_queries import find_msavi_for_date
from datetime import datetime

# Find MSAVI closest to January 22, 2023
msavi = find_msavi_for_date(
    target_date=datetime(2023, 1, 22),
    window_days=15,
    max_cloud_cover=20.0
)

if msavi:
    print(f"Found: {msavi['scene_id']}")
    print(f"Date offset: {msavi['date_offset_days']} days")
    print(f"Cloud cover: {msavi['cloud_cover_percent']}%")
    print(f"MSAVI file: {msavi['msavi_file_path']}")
```

---

#### `register_pair_msavi(...) -> Optional[int]`

Link Sentinel-2 MSAVI products to an InSAR pair.

**Parameters:**
- `insar_pair_id` - InSAR pair ID from insar_pairs table
- `master_s2_id` - S2 product ID for master date
- `slave_s2_id` - S2 product ID for slave date
- `master_msavi_file` - Path to master MSAVI file
- `slave_msavi_file` - Path to slave MSAVI file
- `master_date_offset_days` - Days between InSAR master and S2 master
- `slave_date_offset_days` - Days between InSAR slave and S2 slave

**Returns:**
Integration record ID, or None on failure.

**Example:**
```python
from scripts.db_queries import register_pair_msavi, find_msavi_for_date
from datetime import datetime

# Find MSAVI products for InSAR dates
master_date = datetime(2023, 1, 15)
slave_date = datetime(2023, 1, 27)

master_msavi = find_msavi_for_date(master_date, window_days=10)
slave_msavi = find_msavi_for_date(slave_date, window_days=10)

if master_msavi and slave_msavi:
    integration_id = register_pair_msavi(
        insar_pair_id=123,  # From register_insar_pair()
        master_s2_id=master_msavi['id'],
        slave_s2_id=slave_msavi['id'],
        master_msavi_file=master_msavi['msavi_file_path'],
        slave_msavi_file=slave_msavi['msavi_file_path'],
        master_date_offset_days=int(master_msavi['date_offset_days']),
        slave_date_offset_days=int(slave_msavi['date_offset_days'])
    )
```

## Complete Workflow Example

### Processing an InSAR Pair with MSAVI Integration

```python
from scripts.db_queries import *
from datetime import datetime

# 1. Register SLC downloads
master_id = register_slc_download(
    scene_id='S1A_IW_SLC__1SDV_20230115T060136_20230115T060203_046714_059C5B_F5B0',
    acquisition_date=datetime(2023, 1, 15, 6, 1, 36),
    orbit_direction='ASCENDING',
    track_number=117,
    file_path='/data/slc/master.SAFE'
)

slave_id = register_slc_download(
    scene_id='S1A_IW_SLC__1SDV_20230127T060136_20230127T060203_046889_059F82_A3D1',
    acquisition_date=datetime(2023, 1, 27, 6, 1, 36),
    orbit_direction='ASCENDING',
    track_number=117,
    file_path='/data/slc/slave.SAFE'
)

# 2. Check if InSAR pair already processed
if not insar_pair_exists('S1A_IW_SLC__1SDV_20230115...', 'S1A_IW_SLC__1SDV_20230127...', 'IW1', 'short'):
    
    # 3. Process InSAR pair (your processing code here)
    # ...
    
    # 4. Register InSAR pair
    pair_id = register_insar_pair(
        master_scene_id='S1A_IW_SLC__1SDV_20230115...',
        slave_scene_id='S1A_IW_SLC__1SDV_20230127...',
        pair_type='short',
        subswath='IW1',
        temporal_baseline_days=12,
        file_path='/data/insar/pair_IW1.dim',
        coherence_mean=0.65
    )
    
    # 5. Find corresponding MSAVI products
    master_msavi = find_msavi_for_date(datetime(2023, 1, 15), window_days=10)
    slave_msavi = find_msavi_for_date(datetime(2023, 1, 27), window_days=10)
    
    # 6. Link MSAVI to InSAR pair
    if master_msavi and slave_msavi:
        register_pair_msavi(
            insar_pair_id=pair_id,
            master_s2_id=master_msavi['id'],
            slave_s2_id=slave_msavi['id'],
            master_msavi_file=master_msavi['msavi_file_path'],
            slave_msavi_file=slave_msavi['msavi_file_path'],
            master_date_offset_days=int(master_msavi['date_offset_days']),
            slave_date_offset_days=int(slave_msavi['date_offset_days'])
        )
    
    # 7. Update SLC processing status
    update_slc('S1A_IW_SLC__1SDV_20230115...', fullswath_iw1_processed=True,
               fullswath_iw1_date=datetime.now(), fullswath_iw1_version='1.0.0')
```

## Error Handling

All functions return `None` or `False` on error. Check return values:

```python
# Check for None
status = get_slc_status('S1A_...')
if status is None:
    print("SLC not found or database error")

# Check for False
if not update_slc('S1A_...', fullswath_iw1_processed=True):
    print("Update failed")

# Check for empty list
pairs = get_insar_pairs(117, 'ASCENDING', 'IW1')
if not pairs:
    print("No pairs found")
```

## Database Availability

Functions gracefully handle database unavailability:

```python
from scripts.db_integration import init_db

# Check if database is ready
if init_db():
    # Use database functions
    status = get_slc_status('S1A_...')
else:
    # Database not available, skip tracking
    print("Database not available")
```

## See Also

- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) - Complete schema documentation
- [DB_SCHEMA_QUICK_REFERENCE.md](DB_SCHEMA_QUICK_REFERENCE.md) - Quick reference
- `scripts/db_example_usage.py` - Runnable examples
