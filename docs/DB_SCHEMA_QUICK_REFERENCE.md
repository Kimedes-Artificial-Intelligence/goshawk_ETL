# Database Schema Quick Reference

## Quick Commands

```bash
# Apply migration
cd ../satelit_metadata && alembic upgrade head

# Verify schema
cd ../goshawk_ETL && python scripts/test_db_schema.py

# Rollback (if needed)
cd ../satelit_metadata && alembic downgrade -1
```

## Tables Overview

### slc_products
```sql
-- Track Sentinel-1 SLC products
SELECT scene_id, acquisition_date,
       fullswath_iw1_processed, fullswath_iw2_processed, fullswath_iw3_processed
FROM satelit.slc_products
WHERE downloaded = true;
```

### insar_pairs
```sql
-- Track InSAR pair processing
SELECT ip.id, m.scene_id as master, s.scene_id as slave, 
       ip.subswath, ip.pair_type, ip.coherence_mean
FROM satelit.insar_pairs ip
JOIN satelit.slc_products m ON ip.master_slc_id = m.id
JOIN satelit.slc_products s ON ip.slave_slc_id = s.id;
```

### s2_products
```sql
-- Track Sentinel-2 with MSAVI
SELECT scene_id, acquisition_date, cloud_cover_percent,
       msavi_processed, msavi_file_path
FROM satelit.s2_products
WHERE msavi_processed = true;
```

### insar_pair_msavi
```sql
-- Track InSAR-MSAVI integration
SELECT ipm.id, ip.id as insar_pair,
       s2m.scene_id as master_s2, s2s.scene_id as slave_s2,
       ipm.master_date_offset_days, ipm.slave_date_offset_days
FROM satelit.insar_pair_msavi ipm
JOIN satelit.insar_pairs ip ON ipm.insar_pair_id = ip.id
JOIN satelit.s2_products s2m ON ipm.master_s2_id = s2m.id
JOIN satelit.s2_products s2s ON ipm.slave_s2_id = s2s.id;
```

## Key Columns

### Processing Flags
- `downloaded` - Product downloaded from Copernicus
- `fullswath_iw1_processed` - IW1 full-swath processing complete
- `fullswath_iw2_processed` - IW2 full-swath processing complete
- `fullswath_iw3_processed` - IW3 full-swath processing complete
- `polarimetry_processed` - Polarimetry processing complete
- `msavi_processed` - MSAVI processing complete

### Relationships
- `insar_pairs.master_slc_id` → `slc_products.id`
- `insar_pairs.slave_slc_id` → `slc_products.id`
- `insar_pair_msavi.insar_pair_id` → `insar_pairs.id`
- `insar_pair_msavi.master_s2_id` → `s2_products.id`
- `insar_pair_msavi.slave_s2_id` → `s2_products.id`

## Common Queries

### Find unprocessed SLCs
```sql
SELECT scene_id, subswath
FROM satelit.slc_products
WHERE downloaded = true
  AND fullswath_iw1_processed = false;
```

### Find InSAR pairs without MSAVI
```sql
SELECT ip.*
FROM satelit.insar_pairs ip
LEFT JOIN satelit.insar_pair_msavi ipm ON ip.id = ipm.insar_pair_id
WHERE ipm.id IS NULL;
```

### Count processing status
```sql
SELECT 
    COUNT(*) FILTER (WHERE fullswath_iw1_processed) as iw1_done,
    COUNT(*) FILTER (WHERE fullswath_iw2_processed) as iw2_done,
    COUNT(*) FILTER (WHERE fullswath_iw3_processed) as iw3_done,
    COUNT(*) as total
FROM satelit.slc_products;
```

## Python Integration

```python
from scripts.db_integration import init_db

# Check schema
if init_db():
    print("Database ready!")
else:
    print("Run: cd ../satelit_metadata && alembic upgrade head")
```

## Troubleshooting

### Tables don't exist
```bash
cd ../satelit_metadata
alembic upgrade head
```

### Check current migration version
```bash
cd ../satelit_metadata
alembic current
```

### View migration history
```bash
cd ../satelit_metadata
alembic history
```

### Manual table check
```bash
docker exec -it satelit_metadata_postgres psql -U satelit -d satelit_db

\dt satelit.*
\d satelit.slc_products
```

## Documentation

- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) - Complete schema documentation
- [DB_MIGRATION_GUIDE.md](DB_MIGRATION_GUIDE.md) - Migration guide
- [ISSUE_1_COMPLETION.md](ISSUE_1_COMPLETION.md) - Implementation summary
