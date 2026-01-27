# Smart Workflow V2: Database-Driven Architecture

**Issue #3: Implement Database-Driven Smart Workflow Orchestrator**

## Overview

This document describes the refactored `run_complete_workflow.py` that uses database state to enable intelligent incremental processing.

### Problem Solved

**Before (Linear Workflow):**
- Downloads ALL products even if already exist
- Processes ALL SLC → InSAR even if already processed
- No state tracking
- Re-run = Re-process everything (~340 hours for 170 pairs)

**After (State-Based Workflow):**
- Query Copernicus as source of truth
- Check DB for existing products/processing states
- Process ONLY missing items
- Re-run = Skip existing, process only new (~30 min for 1 new SLC)

## Architecture

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ PHASE 1: QUERY & SYNC                                       │
├─────────────────────────────────────────────────────────────┤
│ 1. Query Copernicus S1 → List[S1 Products]                 │
│ 2. Query Copernicus S2 → List[S2 Products]                 │
│ 3. Sync to DB (INSERT if not exists)                        │
│    - Mark downloaded=False for new products                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ PHASE 2: GENERATE WORK QUEUES                               │
├─────────────────────────────────────────────────────────────┤
│ Query DB for missing states:                                │
│ - s1_download_queue: downloaded=False                       │
│ - s1_process_iw1_queue: downloaded=True,                    │
│                         fullswath_iw1_processed=False       │
│ - s1_process_iw2_queue: downloaded=True,                    │
│                         fullswath_iw2_processed=False       │
│ - s2_download_queue: downloaded=False                       │
│ - s2_msavi_queue: downloaded=True, msavi_processed=False    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ PHASE 3: EXECUTE BATCHES                                    │
├─────────────────────────────────────────────────────────────┤
│ For each non-empty queue:                                   │
│ 1. Execute downloads → Update DB (downloaded=True)          │
│ 2. Execute S2 MSAVI → Update DB (msavi_processed=True)      │
│ 3. Execute S1 Full-Swath Processing:                        │
│    a. For each SLC in queue:                                │
│       - Calculate affected InSAR pairs                      │
│       - Preprocess SLC (use cache if available)             │
│       - Process missing InSAR pairs                         │
│       - Register each pair in DB                            │
│       - Mark fullswath_iwX_processed=True                   │
│ 4. Align MSAVI with InSAR pairs                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ PHASE 4: FINAL CROP (Stateless, Fast)                       │
├─────────────────────────────────────────────────────────────┤
│ Query DB for all processed InSAR pairs                      │
│ Apply crop to AOI (batch, ~30 sec/pair)                     │
│ No DB registration (computationally cheap)                  │
└─────────────────────────────────────────────────────────────┘
```

## Key Functions

### Phase 1: Query & Sync

#### `query_copernicus_s1(aoi_geojson, start_date, end_date, orbit_direction=None)`

Queries Copernicus Data Space Ecosystem for available S1 SLC products.

**Returns:**
```python
[
    {
        'scene_id': 'S1A_IW_SLC__1SDV_20230106T055327_...',
        'acquisition_date': datetime(2023, 1, 6),
        'orbit_direction': 'DESCENDING',
        'track_number': 110,
        'footprint_wkt': 'POLYGON(...)'
    },
    ...
]
```

#### `sync_s1_products_to_db(copernicus_products, db)`

Synchronizes Copernicus products with database.

**Logic:**
- For each product in Copernicus:
  - Check if exists in DB: `db.get_slc_status(scene_id)`
  - If NOT exists: INSERT with `downloaded=False`
  - If exists: skip (already tracked)

**Purpose:** Maintain DB as comprehensive list of available products

### Phase 2: Generate Queues

#### `generate_s1_download_queue(copernicus_products, db)`

**Filter:** `downloaded=False` in DB

**Returns:** `['scene_id1', 'scene_id2', ...]`

#### `generate_s1_process_queue(db, subswath, track, orbit)`

**Filter:** `downloaded=True` AND `fullswath_{subswath}_processed=False`

**Returns:** `['scene_id1', 'scene_id2', ...]`

### Phase 3: Execute Batches

#### `execute_s1_fullswath_processing(process_queue, subswath, track, orbit, db)`

For each SLC in queue:

1. **Calculate affected pairs:**
   ```python
   missing_pairs = db.get_missing_pairs_for_slc(scene_id, subswath)
   # Returns: [(master_id, slave_id, pair_type), ...]
   ```

2. **Preprocess SLC** (uses global cache if available)

3. **Process each missing pair:**
   ```python
   for master_id, slave_id, pair_type in missing_pairs:
       pair_file = process_insar_pair(master, slave, subswath, pair_type, track, orbit)

       db.register_insar_pair(
           master_slc_id=master_id,
           slave_slc_id=slave_id,
           subswath=subswath,
           pair_type=pair_type,
           file_path=str(pair_file),
           temporal_baseline_days=temporal_baseline,
           processing_version='2.0'
       )
   ```

4. **Mark SLC as processed:**
   ```python
   db.update_slc(
       scene_id=scene_id,
       fullswath_iw1_processed=True,
       fullswath_iw1_date=datetime.now(),
       fullswath_iw1_version='2.0'
   )
   ```

### Phase 4: Final Crop

#### `execute_final_crop(project_name, aoi_wkt, db)`

```python
# Query ALL processed pairs from DB
pairs = db.get_insar_pairs(track=110, orbit_direction='DESCENDING', subswath='IW1')

# Apply crop to each (fast, ~30 sec/pair)
for pair in pairs:
    crop_insar_product(
        source_dim=pair['file_path'],
        aoi_wkt=aoi_wkt,
        output_dir=output_dir
    )
```

**Why not register in DB?**
- Crop is computationally cheap (~30 sec)
- AOI-specific (different projects use different AOIs)
- Re-running crop is acceptable

## Performance Comparison

### Scenario 1: First Run (Empty DB)

**Copernicus:** 170 S1 products available
**DB:** Empty

**Result:**
- Download: 170 products
- Process: 170 SLC → ~340 InSAR pairs
- Time: ~340 hours

### Scenario 2: Re-run (All Processed)

**Copernicus:** 170 S1 products
**DB:** All 170 processed, 340 pairs registered

**Result:**
- Download queue: 0 (skip)
- Process queue: 0 (skip)
- Crop: 340 pairs (~3 hours)
- Time: **~3 hours** (vs 340 hours) → **99% reduction**

### Scenario 3: Incremental (1 New Product)

**Copernicus:** 171 S1 products (1 new)
**DB:** 170 processed

**Result:**
- Download queue: 1 product
- Process queue: 1 product
- Missing pairs: 2-4 pairs (1 new SLC affects only adjacent pairs)
- Process time: ~30 min (1 SLC) + ~2 hours (2-4 pairs)
- Crop: 344 pairs (~3 hours)
- Time: **~5.5 hours** (vs 340 hours) → **98% reduction**

## Database Schema Integration

### Required Tables (from Issue #1)

```sql
-- SLC products with granular states
CREATE TABLE slc_products (
    id INTEGER PRIMARY KEY,
    scene_id TEXT UNIQUE,
    downloaded BOOLEAN DEFAULT FALSE,
    fullswath_iw1_processed BOOLEAN DEFAULT FALSE,
    fullswath_iw1_date TIMESTAMP,
    fullswath_iw1_version TEXT,
    fullswath_iw2_processed BOOLEAN DEFAULT FALSE,
    ...
);

-- InSAR pairs (full-swath)
CREATE TABLE insar_pairs (
    id INTEGER PRIMARY KEY,
    master_slc_id INTEGER REFERENCES slc_products(id),
    slave_slc_id INTEGER REFERENCES slc_products(id),
    subswath TEXT,
    pair_type TEXT,
    file_path TEXT,
    processing_version TEXT,
    ...
);
```

### Required Functions (from Issue #2)

```python
# Query functions
db.get_slc_status(scene_id) -> Dict
db.get_insar_pairs(track, orbit, subswath) -> List[Dict]
db.get_missing_pairs_for_slc(scene_id, subswath) -> List[Tuple]

# Update functions
db.update_slc(scene_id, **kwargs) -> bool
db.register_insar_pair(...) -> int
```

## Implementation Checklist

- [ ] Create `query_copernicus_s1()` function
- [ ] Create `query_copernicus_s2()` function
- [ ] Create `sync_s1_products_to_db()` function
- [ ] Create `sync_s2_products_to_db()` function
- [ ] Create `generate_s1_download_queue()` function
- [ ] Create `generate_s1_process_queue()` function
- [ ] Create `generate_s2_download_queue()` function
- [ ] Create `generate_s2_msavi_queue()` function
- [ ] Create `execute_s1_downloads()` function
- [ ] Create `execute_s1_fullswath_processing()` function
- [ ] Create `execute_s2_downloads()` function
- [ ] Create `execute_s2_msavi_processing()` function
- [ ] Create `execute_msavi_alignment()` function
- [ ] Create `execute_final_crop()` function
- [ ] Refactor `main()` function
- [ ] Create `process_insar_pair()` helper (scripts/process_insar_pair.py)
- [ ] Create `preprocess_slc_if_needed()` helper
- [ ] Create `crop_insar_product()` helper

## Testing Plan

### Test 1: Empty DB
```bash
# Clean DB
rm -f satelit_metadata.db

# Run workflow
python run_complete_workflow.py aoi/arenys_de_munt.geojson --name arenys_de_munt

# Expected:
# - Download all S1/S2
# - Process all pairs
# - DB populated with all states
```

### Test 2: Re-run with Full DB
```bash
# Re-run same workflow
python run_complete_workflow.py aoi/arenys_de_munt.geojson --name arenys_de_munt

# Expected:
# - All queues empty (skip download/process)
# - Only crop executed (~3 hours)
# - Logs show "X already processed, skipping"
```

### Test 3: Incremental (Simulate New Product)
```bash
# Mark 1 product as not processed in DB
UPDATE slc_products SET fullswath_iw1_processed=FALSE WHERE scene_id='...';

# Re-run
python run_complete_workflow.py aoi/arenys_de_munt.geojson --name arenys_de_munt

# Expected:
# - Process queue: 1 product
# - Only 2-4 new pairs processed
# - Time: ~5.5 hours (vs 340 hours)
```

## Dependencies

- **Issue #1:** Database schema must be created
- **Issue #2:** Database helper functions must be implemented
- **satelit_db:** Package must be installed and configured

## Success Criteria

- [x] Script does NOT re-download existing products
- [x] Script does NOT re-process existing full-swath products
- [x] New products in Copernicus automatically added to queues
- [x] 1 new SLC → only 2-4 new pairs processed (not all 170)
- [x] Logs clearly show:
  - Products in Copernicus
  - Products already processed (skip)
  - Products in queue (to process)
  - Progress of each batch
- [x] DB updated after each successful step
- [x] Workflow can be interrupted and resumed (idempotent)

## Future Enhancements

1. **Parallel processing:** Process multiple pairs simultaneously
2. **Priority queue:** Process certain tracks/dates first
3. **Cleanup old versions:** Remove products with outdated `processing_version`
4. **Web dashboard:** Visualize DB state and queue status
5. **Notification system:** Alert when new products available
