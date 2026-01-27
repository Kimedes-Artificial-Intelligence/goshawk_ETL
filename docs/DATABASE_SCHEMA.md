# Database Schema Implementation - Granular Tracking

## Overview

This implementation adds granular tracking tables to the `satelit_metadata` database for improved tracking of Sentinel-1 SLC, InSAR, and Sentinel-2 products.

## Schema Design

### 1. `slc_products` (Sentinel-1)

Tracks individual SLC products with per-subswath processing status.

**Columns:**
- `id`: Primary key
- `scene_id`: Unique scene identifier (e.g., S1A_IW_SLC__1SDV_...)
- `acquisition_date`: Acquisition timestamp
- `satellite_id`: S1A, S1B, or S1C
- `orbit_direction`: ASCENDING or DESCENDING
- `track_number`: Relative orbit track (1-175)
- `subswath`: IW1, IW2, IW3, or IW (full)
- `file_path`: Path to downloaded SAFE file
- `downloaded`: Boolean flag for download status
- `downloaded_date`: When the product was downloaded
- `fullswath_iw1_processed`: Boolean flag for IW1 full-swath processing
- `fullswath_iw1_date`: Processing date for IW1
- `fullswath_iw1_version`: Processing version for IW1
- `fullswath_iw2_processed`: Boolean flag for IW2 full-swath processing
- `fullswath_iw2_date`: Processing date for IW2
- `fullswath_iw2_version`: Processing version for IW2
- `fullswath_iw3_processed`: Boolean flag for IW3 full-swath processing
- `fullswath_iw3_date`: Processing date for IW3
- `fullswath_iw3_version`: Processing version for IW3
- `polarimetry_processed`: Boolean flag for polarimetry processing
- `polarimetry_date`: Polarimetry processing date
- `polarimetry_version`: Polarimetry processing version
- `created_at`, `updated_at`: Timestamps

**Indexes:**
- `idx_slc_scene_id`: Fast lookup by scene ID
- `idx_slc_acquisition_date`: Temporal queries
- `idx_slc_track`: Track-based queries

### 2. `insar_pairs` (Full-Swath InSAR Results)

Tracks InSAR pair processing with full-swath results.

**Columns:**
- `id`: Primary key
- `master_slc_id`: Foreign key to `slc_products`
- `slave_slc_id`: Foreign key to `slc_products`
- `pair_type`: 'short' or 'long' (baseline type)
- `subswath`: IW1, IW2, or IW3
- `temporal_baseline_days`: Days between master and slave
- `perpendicular_baseline_m`: Perpendicular baseline in meters
- `file_path`: Path to InSAR product
- `processed_date`: When processing completed
- `processing_version`: Software version used
- `coherence_mean`: Mean coherence value
- `coherence_std`: Standard deviation of coherence
- `created_at`, `updated_at`: Timestamps

**Constraints:**
- Unique on `(master_slc_id, slave_slc_id, subswath, pair_type)`
- Foreign keys with CASCADE delete
- Check constraint: `pair_type IN ('short', 'long')`

**Indexes:**
- `idx_insar_master_slc`: Fast lookup by master
- `idx_insar_slave_slc`: Fast lookup by slave

### 3. `s2_products` (Sentinel-2)

Tracks Sentinel-2 products with MSAVI processing status.

**Columns:**
- `id`: Primary key
- `scene_id`: Unique scene identifier
- `acquisition_date`: Acquisition timestamp
- `satellite_id`: S2A or S2B
- `cloud_cover_percent`: Cloud coverage percentage
- `aoi_coverage_percent`: AOI coverage percentage
- `file_path`: Path to downloaded product
- `downloaded`: Boolean flag for download status
- `downloaded_date`: When the product was downloaded
- `msavi_processed`: Boolean flag for MSAVI processing
- `msavi_file_path`: Path to MSAVI product
- `msavi_date`: MSAVI processing date
- `msavi_version`: MSAVI processing version
- `created_at`, `updated_at`: Timestamps

**Indexes:**
- `idx_s2_scene_id`: Fast lookup by scene ID
- `idx_s2_acquisition_date`: Temporal queries

### 4. `insar_pair_msavi` (Integration Table)

Links InSAR pairs with corresponding MSAVI products for temporal alignment.

**Columns:**
- `id`: Primary key
- `insar_pair_id`: Foreign key to `insar_pairs`
- `master_s2_id`: Foreign key to `s2_products` (for master date)
- `slave_s2_id`: Foreign key to `s2_products` (for slave date)
- `master_msavi_file`: Path to master MSAVI file
- `slave_msavi_file`: Path to slave MSAVI file
- `master_date_offset_days`: Days between InSAR master and S2 master
- `slave_date_offset_days`: Days between InSAR slave and S2 slave
- `aligned_date`: Reference date for alignment
- `created_at`, `updated_at`: Timestamps

**Foreign Keys:**
- All with CASCADE delete to maintain referential integrity

**Indexes:**
- `idx_msavi_insar_pair`: Fast lookup by InSAR pair
- `idx_msavi_master_s2`: Fast lookup by master S2
- `idx_msavi_slave_s2`: Fast lookup by slave S2

## Implementation

### Migration File

**Location:** `satelit_metadata/migrations/versions/42fcecff687f_add_granular_tracking_tables.py`

This Alembic migration creates all four tables with proper constraints, indexes, and foreign keys.

### Integration Function

**Location:** `scripts/db_integration.py`

The `init_db()` function:
1. Checks if `satelit_db` is available
2. Verifies all required tables exist
3. Returns status and guidance if migration needed

## Usage

### 1. Run Migration (One-time setup)

```bash
cd ../satelit_metadata
alembic upgrade head
```

### 2. Verify Schema

```bash
python scripts/test_db_schema.py
```

### 3. Use in Code

```python
from scripts.db_integration import init_db

# Check/initialize schema
if init_db():
    print("Database ready!")
else:
    print("Migration needed")
```

## Design Rationale

### Why Separate Tables?

1. **Type Safety**: Specific columns for each product type prevent errors
2. **Performance**: Targeted indexes for common query patterns
3. **Clarity**: Explicit schema makes requirements clear
4. **Constraints**: Unique constraints enforce data integrity per product type

### Why PostgreSQL (via satelit_metadata)?

1. **Shared State**: Multiple repositories can query the same database
2. **ACID**: Transaction safety for concurrent access
3. **Spatial**: PostGIS support for future spatial queries
4. **Scalability**: Can handle large product catalogs

### Coexistence with Existing Schema

The new tables complement the existing `products` table:
- **Existing `products`**: Generic catalog for all product types
- **New tables**: Granular tracking for specific workflows

Both can be used simultaneously, with the new tables providing workflow-specific tracking while `products` maintains the overall catalog.

## Testing

### Schema Verification

```bash
# Check tables exist
python scripts/test_db_schema.py

# Connect to database
docker exec -it satelit_metadata_postgres psql -U satelit -d satelit_db

# List tables
\dt satelit.*

# Check table structure
\d satelit.slc_products
\d satelit.insar_pairs
\d satelit.s2_products
\d satelit.insar_pair_msavi
```

### Example Queries

```sql
-- Count SLCs by processing status
SELECT 
    subswath,
    COUNT(*) FILTER (WHERE fullswath_iw1_processed) as iw1_done,
    COUNT(*) FILTER (WHERE fullswath_iw2_processed) as iw2_done,
    COUNT(*) FILTER (WHERE fullswath_iw3_processed) as iw3_done
FROM satelit.slc_products
GROUP BY subswath;

-- Find InSAR pairs needing MSAVI
SELECT ip.id, ip.master_slc_id, ip.slave_slc_id
FROM satelit.insar_pairs ip
LEFT JOIN satelit.insar_pair_msavi ipm ON ip.id = ipm.insar_pair_id
WHERE ipm.id IS NULL;

-- S2 products with successful MSAVI
SELECT scene_id, acquisition_date, cloud_cover_percent
FROM satelit.s2_products
WHERE msavi_processed = true
ORDER BY acquisition_date DESC;
```

## Acceptance Criteria

- ✅ Migration creates all 4 tables if they don't exist
- ✅ Foreign keys properly configured with CASCADE delete
- ✅ Unique constraints prevent duplicate entries
- ✅ Indexes optimize common query patterns
- ✅ `init_db()` function in `scripts/db_integration.py`
- ✅ Documentation explains schema and usage

## Next Steps

After schema implementation:
1. Update download scripts to register products in new tables
2. Update processing scripts to mark processing flags
3. Implement cleanup logic using processing flags
4. Add API functions for common queries
