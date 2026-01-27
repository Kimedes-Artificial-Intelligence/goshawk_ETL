# Sentinel-2 Database Integration - Quick Reference

## Issue #6 Complete ✅

Sentinel-2 download and MSAVI processing are now tracked in the database.

## What Changed

### Download Script
- Checks database before downloading S2 products
- Registers S2 products after successful download
- Stores cloud cover percentage

### MSAVI Script
- Checks database before processing
- Skips if MSAVI already calculated
- Updates database after successful processing

## Quick Usage

### Download S2 (with database tracking)
```bash
python scripts/download_copernicus.py \
    --collection SENTINEL-2 \
    --product-type L2A \
    --date-start 2023-01-15 \
    --date-end 2023-01-20 \
    --aoi-geojson aoi/study_area.geojson
```

### Process MSAVI (with database tracking)
```bash
python scripts/process_sentinel2_msavi.py \
    --date 20230120 \
    --aoi-geojson aoi/study_area.geojson
```

### Check Status in Python
```python
from scripts.db_queries import get_s2_status

status = get_s2_status('S2A_MSIL2A_20230120T105321...')
if status:
    print(f"Downloaded: {status['downloaded']}")
    print(f"Cloud cover: {status['cloud_cover_percent']}%")
    print(f"MSAVI processed: {status['msavi_processed']}")
```

## Benefits

✅ **No More Re-downloads** - Skips already downloaded products  
✅ **No More Re-processing** - Skips already processed MSAVI  
✅ **Cloud Cover Tracking** - Helps filter suitable products  
✅ **Processing History** - Know when MSAVI was generated  
✅ **File Path Tracking** - Easy access to files  

## Database Fields

### Populated on Download
- `scene_id` - Product name
- `acquisition_date` - Sensing time
- `cloud_cover_percent` - Cloud percentage
- `file_path` - Path to .SAFE
- `downloaded` = True

### Populated on MSAVI Processing
- `msavi_processed` = True
- `msavi_file_path` - Path to .tif
- `msavi_date` - Processing time
- `msavi_version` - Version (1.0.0)

## Next Steps

Ready for:
- InSAR-MSAVI temporal alignment
- Cleanup scripts (delete after processing)
- Batch processing workflows

## Documentation

- [ISSUE_6_COMPLETION.md](ISSUE_6_COMPLETION.md) - Full implementation details
- [DB_HELPER_FUNCTIONS.md](DB_HELPER_FUNCTIONS.md) - API reference
- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) - Schema documentation
