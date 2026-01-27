# Database Helper Functions - Quick Start

## Installation

Database functions are ready to use. No additional setup required beyond Issue #1 (database schema).

## Quick Import

```python
from scripts.db_queries import (
    # Sentinel-1
    get_slc_status, update_slc, register_slc_download,
    insar_pair_exists, register_insar_pair, get_insar_pairs,
    # Sentinel-2
    get_s2_status, update_s2, register_s2_download,
    find_msavi_for_date, register_pair_msavi
)
```

## Common Use Cases

### 1. Register Downloaded SLC

```python
from scripts.db_queries import register_slc_download
from datetime import datetime

slc_id = register_slc_download(
    scene_id='S1A_IW_SLC__1SDV_...',
    acquisition_date=datetime(2023, 1, 15, 6, 1, 36),
    orbit_direction='ASCENDING',
    track_number=117,
    file_path='/data/slc/product.SAFE'
)
```

### 2. Check Processing Status

```python
from scripts.db_queries import get_slc_status

status = get_slc_status('S1A_IW_SLC__1SDV_...')
if status and not status['fullswath_iw1_processed']:
    # Process IW1
    process_iw1()
```

### 3. Mark Processing Complete

```python
from scripts.db_queries import update_slc
from datetime import datetime

update_slc(
    'S1A_IW_SLC__1SDV_...',
    fullswath_iw1_processed=True,
    fullswath_iw1_date=datetime.now(),
    fullswath_iw1_version='1.0.0'
)
```

### 4. Register InSAR Pair

```python
from scripts.db_queries import register_insar_pair

pair_id = register_insar_pair(
    master_scene_id='S1A_IW_SLC__1SDV_...',
    slave_scene_id='S1A_IW_SLC__1SDV_...',
    pair_type='short',
    subswath='IW1',
    temporal_baseline_days=12,
    file_path='/data/insar/pair.dim',
    coherence_mean=0.65
)
```

### 5. Find MSAVI for Date

```python
from scripts.db_queries import find_msavi_for_date
from datetime import datetime

msavi = find_msavi_for_date(
    target_date=datetime(2023, 1, 22),
    window_days=15,
    max_cloud_cover=20.0
)

if msavi:
    print(f"Found MSAVI: {msavi['scene_id']}")
    print(f"Date offset: {msavi['date_offset_days']} days")
```

## All Available Functions

### Sentinel-1 (6 functions)
- `get_slc_status(scene_id)` - Get SLC status
- `update_slc(scene_id, **kwargs)` - Update SLC flags
- `register_slc_download(...)` - Register SLC download
- `insar_pair_exists(...)` - Check if pair exists
- `register_insar_pair(...)` - Register InSAR pair
- `get_insar_pairs(...)` - Get pairs for track

### Sentinel-2 (5 functions)
- `get_s2_status(scene_id)` - Get S2 status
- `update_s2(scene_id, **kwargs)` - Update S2 flags
- `register_s2_download(...)` - Register S2 download
- `find_msavi_for_date(...)` - Find closest MSAVI
- `register_pair_msavi(...)` - Link MSAVI to InSAR

### Utilities (2 functions)
- `get_slc_by_id(slc_id)` - Get SLC by ID
- `get_s2_by_id(s2_id)` - Get S2 by ID

## Examples

Run complete examples:
```bash
python scripts/db_example_usage.py
```

## Documentation

- [DB_HELPER_FUNCTIONS.md](DB_HELPER_FUNCTIONS.md) - Complete API documentation
- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) - Schema reference
- [ISSUE_2_COMPLETION.md](ISSUE_2_COMPLETION.md) - Implementation details

## Error Handling

All functions return `None`, `False`, or `[]` on error:

```python
status = get_slc_status('S1A_...')
if status is None:
    print("Not found or database error")

if not update_slc('S1A_...', processed=True):
    print("Update failed")
```

## Database Availability

Functions gracefully handle database unavailability:

```python
from scripts.db_integration import init_db

if init_db():
    # Database available
    status = get_slc_status('S1A_...')
else:
    # Database not available
    print("Skipping database tracking")
```
