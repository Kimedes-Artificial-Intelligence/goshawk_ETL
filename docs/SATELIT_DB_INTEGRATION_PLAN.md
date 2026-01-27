# Satelit_Metadata Integration Plan

**Date:** 2026-01-27  
**Context:** After implementing Issues #1, #2, #6, #8 with simple approach  
**Goal:** Migrate to robust PostgreSQL + SQLAlchemy architecture from `satelit_metadata` repo

## Executive Summary

The current implementation used a **simplified approach** for rapid prototyping. The production-ready `satelit_metadata` repository provides a **superior PostgreSQL + SQLAlchemy architecture** with:

- ✅ Single Table Inheritance (STI) strategy (1 `products` table)
- ✅ PostgreSQL with PostGIS for spatial queries
- ✅ Proper relationships (`ProductLineage`, `StorageLocation`)
- ✅ Processing run tracking
- ✅ Download queue management
- ✅ Repository track organization

## Current State Analysis

### What We Have (goshawk_ETL)

**Files Created (Issues #1, #2, #6, #8):**
```
scripts/db_integration.py       # Simple SQLite wrapper
scripts/db_queries.py            # Helper functions
scripts/db_example_usage.py      # Examples
scripts/batch_aoi_crop.py        # Database-driven crop
```

**Database Approach:**
- Simple SQLite with 4 separate tables
- Direct SQL queries
- File-based storage
- No spatial indexing
- No lineage tracking

**Status:** ✅ Works for prototyping, ⚠️ Not production-ready

### What We Need (satelit_metadata)

**Architecture:**
```
satelit_metadata/
├── satelit_db/
│   ├── models.py              # SQLAlchemy models (PostgreSQL)
│   ├── api.py                 # High-level API
│   ├── database.py            # Session management
│   └── migrations/            # Alembic migrations
```

**Database Approach:**
- PostgreSQL with PostGIS
- SQLAlchemy ORM
- Single Table Inheritance
- Spatial indexing (GiST)
- Full lineage tracking
- Processing run history
- Download queue management

**Status:** ✅ Production-ready, needs extension for S2/MSAVI

## Gap Analysis

### Schema Gaps

| Feature | Current (Simple) | Target (Satelit_metadata) | Gap |
|---------|------------------|---------------------------|-----|
| **Database** | SQLite | PostgreSQL | Migration needed |
| **ORM** | None (raw SQL) | SQLAlchemy | Full refactor |
| **S1 Products** | `slc_products` table | `Product` (type='SLC') | Mapping needed |
| **InSAR Products** | `insar_pairs` table | `Product` (type='INSAR_*') | Mapping + lineage |
| **S2 Products** | `s2_products` table | `Product` (type='SENTINEL2_L2A') | ✅ Already in schema! |
| **MSAVI** | `s2_products.msavi_*` | Needs new product_type | Extension needed |
| **Lineage** | None | `ProductLineage` table | Implementation needed |
| **Storage** | `file_path` column | `StorageLocation` table | Implementation needed |
| **Spatial** | No indexing | PostGIS + GiST | Already available! |

### API Gaps

| Function | Current | Target | Status |
|----------|---------|--------|--------|
| `register_slc` | ✅ Implemented | ✅ In `api.py` | Ready to use |
| `register_insar` | ✅ Implemented | ✅ In `api.py` | Ready to use |
| `register_s2` | ✅ Implemented | ❌ Missing | Need to add |
| `register_msavi` | ✅ Implemented | ❌ Missing | Need to add |
| `find_closest_product` | ❌ Missing | ❌ Missing | Need to add |
| `get_insar_pairs` | ✅ Implemented | Needs query builder | Need to add |

## Migration Strategy

### Phase 1: Extend satelit_metadata (Prerequisites)

**Goal:** Add missing S2/MSAVI support to `satelit_metadata` repo

#### Issue 1.1: Extend Product Type Enum for MSAVI
**Repository:** `satelit_metadata`  
**File:** `satelit_db/models.py`

**Changes:**
```python
# Current enum
product_type: Mapped[str] = mapped_column(
    Enum(
        "SLC",
        "GRD",
        "INSAR_SHORT",
        "INSAR_LONG",
        "POLARIMETRY",
        "SENTINEL2_L2A",  # ✅ Already exists!
        name="product_type_enum",
    ),
    nullable=False,
)

# ADD:
product_type: Mapped[str] = mapped_column(
    Enum(
        "SLC",
        "GRD",
        "INSAR_SHORT",
        "INSAR_LONG",
        "POLARIMETRY",
        "SENTINEL2_L2A",
        "SENTINEL2_MSAVI",        # NEW!
        "URBAN_CROP",             # NEW! (for final deliverable)
        name="product_type_enum",
    ),
    nullable=False,
)
```

**Alembic Migration:**
```python
# migrations/versions/xxx_add_s2_msavi_types.py
def upgrade():
    op.execute("ALTER TYPE product_type_enum ADD VALUE 'SENTINEL2_MSAVI'")
    op.execute("ALTER TYPE product_type_enum ADD VALUE 'URBAN_CROP'")
```

#### Issue 1.2: Add S2-Specific Metadata Fields
**Repository:** `satelit_metadata`  
**File:** `satelit_db/models.py`

**Changes:**
```python
# Add to Product class (around line 100)

# Sentinel-2 specific
cloud_cover_percent: Mapped[Optional[float]] = mapped_column(Float)
aoi_coverage_percent: Mapped[Optional[float]] = mapped_column(Float)
tile_id: Mapped[Optional[str]] = mapped_column(String(10))  # e.g., "31TBF"

# MSAVI specific (stored in quality_metrics JSON, but add helpers)
@property
def msavi_valid_pixels_pct(self) -> Optional[float]:
    """Get MSAVI valid pixels percentage from quality metrics."""
    return self.quality_metrics.get('msavi_valid_pixels_pct') if self.quality_metrics else None

@msavi_valid_pixels_pct.setter
def msavi_valid_pixels_pct(self, value: float):
    """Set MSAVI valid pixels percentage in quality metrics."""
    if self.quality_metrics is None:
        self.quality_metrics = {}
    self.quality_metrics['msavi_valid_pixels_pct'] = value
```

#### Issue 1.3: Implement S2/MSAVI Registration API
**Repository:** `satelit_metadata`  
**File:** `satelit_db/api.py`

**Add these methods:**
```python
def register_s2_product(
    self,
    scene_id: str,
    acquisition_date: datetime,
    satellite_id: str,  # S2A or S2B
    tile_id: str,
    cloud_cover_percent: float,
    aoi_coverage_percent: float,
    footprint_wkt: Optional[str] = None,
    download_source: str = "copernicus",
) -> Product:
    """Register Sentinel-2 L2A product."""
    
    existing = (
        self.session.query(Product)
        .filter(
            Product.scene_id == scene_id,
            Product.product_type == "SENTINEL2_L2A"
        )
        .first()
    )
    
    if existing:
        return existing
    
    product = Product(
        scene_id=scene_id,
        product_type="SENTINEL2_L2A",
        acquisition_date=acquisition_date,
        satellite_id=satellite_id,
        tile_id=tile_id,
        cloud_cover_percent=cloud_cover_percent,
        aoi_coverage_percent=aoi_coverage_percent,
        footprint=footprint_wkt,
        processing_status="DISCOVERED",
        download_source=download_source,
    )
    
    self.session.add(product)
    self.session.flush()
    return product


def register_msavi_product(
    self,
    parent_s2_id: int,
    scene_id: str,
    acquisition_date: datetime,
    valid_pixels_pct: float,
) -> Product:
    """
    Register MSAVI product derived from S2.
    
    Args:
        parent_s2_id: Database ID of parent S2 product
        scene_id: Generated scene ID (e.g., "MSAVI_S2A_20230601...")
        acquisition_date: Same as parent S2
        valid_pixels_pct: Percentage of valid MSAVI pixels
        
    Returns:
        Created MSAVI Product instance
    """
    parent_s2 = self.session.query(Product).get(parent_s2_id)
    if not parent_s2:
        raise ValueError(f"Parent S2 product not found: {parent_s2_id}")
    
    existing = (
        self.session.query(Product)
        .filter(Product.scene_id == scene_id)
        .first()
    )
    
    if existing:
        return existing
    
    product = Product(
        scene_id=scene_id,
        product_type="SENTINEL2_MSAVI",
        acquisition_date=acquisition_date,
        satellite_id=parent_s2.satellite_id,
        tile_id=parent_s2.tile_id,
        footprint=parent_s2.footprint,
        processing_status="PROCESSED",
        quality_metrics={'msavi_valid_pixels_pct': valid_pixels_pct},
    )
    
    self.session.add(product)
    self.session.flush()
    
    # Create lineage
    lineage = ProductLineage(
        parent_product_id=parent_s2_id,
        child_product_id=product.id,
        relationship_type="DERIVED",
        processing_level="L3",
    )
    self.session.add(lineage)
    self.session.flush()
    
    return product


def find_closest_product(
    self,
    target_date: datetime,
    product_type: str,
    max_days_diff: int = 30,
    **filters
) -> Optional[Tuple[Product, int]]:
    """
    Find product closest to target date within time window.
    
    Args:
        target_date: Target acquisition date
        product_type: Product type to search
        max_days_diff: Maximum days difference allowed
        **filters: Additional filters (tile_id, track_number, etc.)
        
    Returns:
        Tuple of (Product, days_offset) or None if not found
    """
    query = self.session.query(
        Product,
        func.abs(
            func.extract('epoch', Product.acquisition_date - target_date) / 86400
        ).label('days_diff')
    ).filter(
        Product.product_type == product_type,
        Product.processing_status.in_(['DOWNLOADED', 'PROCESSED']),
        func.abs(
            func.extract('epoch', Product.acquisition_date - target_date) / 86400
        ) <= max_days_diff
    )
    
    # Apply additional filters
    for key, value in filters.items():
        if hasattr(Product, key):
            query = query.filter(getattr(Product, key) == value)
    
    # Order by closest date
    query = query.order_by('days_diff')
    
    result = query.first()
    if result:
        return (result[0], int(result[1]))
    return None


def get_insar_pairs(
    self,
    track: Optional[int] = None,
    orbit_direction: Optional[str] = None,
    subswath: Optional[str] = None,
    pair_type: Optional[str] = None,
    status: str = "PROCESSED",
) -> List[Product]:
    """
    Query InSAR pairs with filters.
    
    Args:
        track: Track number (1-175)
        orbit_direction: ASCENDING or DESCENDING
        subswath: IW1, IW2, or IW3
        pair_type: 'short' or 'long'
        status: Processing status filter
        
    Returns:
        List of InSAR Product instances
    """
    query = self.session.query(Product).filter(
        or_(
            Product.product_type == "INSAR_SHORT",
            Product.product_type == "INSAR_LONG"
        ),
        Product.processing_status == status
    )
    
    if track is not None:
        query = query.filter(Product.track_number == track)
    
    if orbit_direction is not None:
        query = query.filter(Product.orbit_direction == orbit_direction)
    
    if subswath is not None:
        query = query.filter(Product.subswath == subswath)
    
    if pair_type is not None:
        product_type_filter = (
            "INSAR_SHORT" if pair_type == "short" else "INSAR_LONG"
        )
        query = query.filter(Product.product_type == product_type_filter)
    
    return query.order_by(Product.acquisition_date).all()
```

### Phase 2: Migrate goshawk_ETL to Use satelit_metadata

**Goal:** Replace simple SQLite implementation with PostgreSQL/SQLAlchemy

#### Issue 2.1: Setup PostgreSQL Database
**Repository:** `goshawk_ETL`  
**Action:** Infrastructure setup

**Tasks:**
1. Install PostgreSQL + PostGIS
2. Create database:
   ```sql
   CREATE DATABASE satelit_metadata;
   CREATE SCHEMA satelit;
   CREATE EXTENSION postgis;
   ```
3. Run Alembic migrations from `satelit_metadata`
4. Create `.env` file:
   ```
   DATABASE_URL=postgresql://user:pass@localhost/satelit_metadata
   ```

#### Issue 2.2: Add satelit_metadata Dependency
**Repository:** `goshawk_ETL`  
**Files:** `environment.yml`, `requirements.txt`

**Changes:**
```yaml
# environment.yml
dependencies:
  - python=3.10
  - postgresql
  - postgis
  - pip:
    - sqlalchemy>=2.0
    - geoalchemy2
    - psycopg2-binary
    - alembic
    # Add satelit_metadata as local package
    - -e ../satelit_metadata
```

**Or install from git:**
```yaml
- pip:
  - git+https://github.com/your-org/satelit_metadata.git@main
```

#### Issue 2.3: Create DB Connection Wrapper
**Repository:** `goshawk_ETL`  
**File:** `scripts/db_connection.py` (NEW)

```python
"""
Database connection wrapper for goshawk_ETL.

Provides simple access to satelit_metadata database API.
"""

import os
from contextlib import contextmanager
from typing import Generator

from satelit_db.api import SatelitDBAPI
from satelit_db.database import get_session


@contextmanager
def get_db_api() -> Generator[SatelitDBAPI, None, None]:
    """
    Context manager for database API access.
    
    Usage:
        with get_db_api() as db:
            products = db.get_insar_pairs(track=110)
    """
    session = next(get_session())
    try:
        api = SatelitDBAPI(session)
        yield api
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_api_simple() -> SatelitDBAPI:
    """
    Get DB API without context manager.
    
    WARNING: You must manually commit() and close() the session!
    
    Usage:
        db = get_db_api_simple()
        try:
            products = db.get_insar_pairs(track=110)
            db.session.commit()
        finally:
            db.session.close()
    """
    session = next(get_session())
    return SatelitDBAPI(session)
```

#### Issue 2.4: Refactor download_copernicus.py
**Repository:** `goshawk_ETL`  
**File:** `scripts/download_copernicus.py`

**Changes:**
```python
# Add at top
from db_connection import get_db_api

# In download_sentinel1() function, after successful download:
def download_sentinel1(...):
    # ... existing download logic ...
    
    if download_successful:
        # Register in database
        with get_db_api() as db:
            product = db.register_slc_product(
                scene_id=scene_id,
                acquisition_date=acquisition_date,
                satellite_id=satellite_id,
                orbit_direction=orbit_direction,
                relative_orbit=relative_orbit,
                absolute_orbit=absolute_orbit,
                subswath=subswath,
                polarization=polarization,
                footprint_wkt=footprint_wkt,
            )
            
            # Register storage location
            storage = db.register_storage_location(
                product_id=product.id,
                file_path=str(download_path),
                file_format="SAFE",
                size_bytes=get_dir_size(download_path),
            )
            
            logger.info(f"Registered product in database: ID={product.id}")

# Similarly for download_sentinel2():
def download_sentinel2(...):
    # ... existing download logic ...
    
    if download_successful:
        with get_db_api() as db:
            product = db.register_s2_product(
                scene_id=scene_id,
                acquisition_date=acquisition_date,
                satellite_id=satellite_id,
                tile_id=tile_id,
                cloud_cover_percent=cloud_cover,
                aoi_coverage_percent=aoi_coverage,
                footprint_wkt=footprint_wkt,
            )
            
            storage = db.register_storage_location(
                product_id=product.id,
                file_path=str(download_path),
                file_format="SAFE",
                size_bytes=get_dir_size(download_path),
            )
```

#### Issue 2.5: Refactor process_insar_gpt.py
**Repository:** `goshawk_ETL`  
**File:** `scripts/process_insar_gpt.py`

**Changes:**
```python
from db_connection import get_db_api

# After successful InSAR processing:
def process_insar_pair(master_slc, slave_slc, ...):
    # ... existing processing logic ...
    
    if processing_successful:
        with get_db_api() as db:
            # Register InSAR product
            insar_product = db.register_insar_product(
                master_scene_id=master_slc.scene_id,
                slave_scene_id=slave_slc.scene_id,
                pair_type='short',  # or 'long'
                temporal_baseline_days=temporal_baseline,
                perpendicular_baseline_m=perp_baseline,
                subswath=subswath,
                orbit_direction=orbit_direction,
                track_number=track,
            )
            
            # Register storage
            db.register_storage_location(
                product_id=insar_product.id,
                file_path=str(output_dim_path),
                file_format="BEAM-DIMAP",
                size_bytes=get_dir_size(output_dir),
            )
            
            logger.info(f"Registered InSAR pair: ID={insar_product.id}")
```

#### Issue 2.6: Refactor process_sentinel2_msavi.py
**Repository:** `goshawk_ETL`  
**File:** `scripts/process_sentinel2_msavi.py`

**Changes:**
```python
from db_connection import get_db_api

# After MSAVI calculation:
def calculate_msavi(s2_product_path, ...):
    # ... existing MSAVI logic ...
    
    if calculation_successful:
        with get_db_api() as db:
            # Find parent S2 product in DB
            parent_s2 = db.session.query(Product).filter(
                Product.scene_id == s2_scene_id
            ).first()
            
            if parent_s2:
                # Register MSAVI product
                msavi_scene_id = f"MSAVI_{s2_scene_id}"
                msavi_product = db.register_msavi_product(
                    parent_s2_id=parent_s2.id,
                    scene_id=msavi_scene_id,
                    acquisition_date=parent_s2.acquisition_date,
                    valid_pixels_pct=valid_pixels_percentage,
                )
                
                # Register storage
                db.register_storage_location(
                    product_id=msavi_product.id,
                    file_path=str(msavi_output_path),
                    file_format="GeoTIFF",
                    size_bytes=os.path.getsize(msavi_output_path),
                )
```

#### Issue 2.7: Refactor batch_aoi_crop.py
**Repository:** `goshawk_ETL`  
**File:** `scripts/batch_aoi_crop.py`

**Changes:**
```python
from db_connection import get_db_api

# Replace simple query with SQLAlchemy query:
def main():
    with get_db_api() as db:
        # Get all InSAR pairs matching criteria
        insar_pairs = db.get_insar_pairs(
            track=args.track,
            orbit_direction=args.orbit,
            subswath=args.subswath,
            pair_type=args.pair_type,
            status="PROCESSED"
        )
        
        logger.info(f"Found {len(insar_pairs)} InSAR pairs to crop")
        
        for pair in insar_pairs:
            # Get storage location
            storage = pair.storage_locations[0]  # Primary storage
            input_file = Path(storage.file_path)
            
            # Crop logic...
            output_file = crop_to_aoi(input_file, aoi_wkt)
            
            # Optional: Register cropped product
            if args.register_crops:
                crop_product = Product(
                    scene_id=f"CROP_{pair.scene_id}",
                    product_type="URBAN_CROP",
                    acquisition_date=pair.acquisition_date,
                    processing_status="PROCESSED",
                )
                db.session.add(crop_product)
                
                # Create lineage
                lineage = ProductLineage(
                    parent_product_id=pair.id,
                    child_product_id=crop_product.id,
                    relationship_type="SPATIAL_SUBSET",
                )
                db.session.add(lineage)
```

#### Issue 2.8: Deprecate Old DB Files
**Repository:** `goshawk_ETL`  
**Action:** Archive old implementation

**Tasks:**
1. Move old files to `scripts/deprecated/`:
   ```
   scripts/db_integration.py → scripts/deprecated/
   scripts/db_queries.py → scripts/deprecated/
   ```
2. Update imports throughout codebase
3. Add deprecation warnings if old imports detected
4. Update all documentation

### Phase 3: Advanced Features

#### Issue 3.1: Implement MSAVI-InSAR Alignment
**Repository:** `goshawk_ETL`  
**File:** `scripts/align_msavi_to_insar.py` (NEW)

```python
"""
Align MSAVI products to InSAR pairs based on temporal proximity.

For each InSAR pair, finds the closest MSAVI to master and slave dates
and creates linkage metadata.
"""

from datetime import datetime
from db_connection import get_db_api


def align_msavi_to_insar(track: int, orbit: str, subswath: str, max_days: int = 30):
    """
    Align MSAVI products to InSAR pairs.
    
    Args:
        track: Track number
        orbit: Orbit direction
        subswath: Subswath
        max_days: Maximum days difference for MSAVI alignment
    """
    with get_db_api() as db:
        # Get all InSAR pairs
        insar_pairs = db.get_insar_pairs(
            track=track,
            orbit_direction=orbit,
            subswath=subswath,
            status="PROCESSED"
        )
        
        alignments_created = 0
        
        for pair in insar_pairs:
            # Get master/slave dates from lineage
            parents = pair.parent_relationships
            master = next((p.parent for p in parents if 'master' in p.relationship_type.lower()), None)
            slave = next((p.parent for p in parents if 'slave' in p.relationship_type.lower()), None)
            
            if not master or not slave:
                continue
            
            # Find closest MSAVI to master
            master_msavi, master_offset = db.find_closest_product(
                target_date=master.acquisition_date,
                product_type="SENTINEL2_MSAVI",
                max_days_diff=max_days,
                # Add spatial filter if needed
            ) or (None, None)
            
            # Find closest MSAVI to slave
            slave_msavi, slave_offset = db.find_closest_product(
                target_date=slave.acquisition_date,
                product_type="SENTINEL2_MSAVI",
                max_days_diff=max_days,
            ) or (None, None)
            
            if master_msavi and slave_msavi:
                # Store alignment in quality_metrics
                if pair.quality_metrics is None:
                    pair.quality_metrics = {}
                
                pair.quality_metrics['msavi_alignment'] = {
                    'master_msavi_id': master_msavi.id,
                    'slave_msavi_id': slave_msavi.id,
                    'master_offset_days': master_offset,
                    'slave_offset_days': slave_offset,
                    'aligned_at': datetime.utcnow().isoformat(),
                }
                
                alignments_created += 1
        
        db.session.commit()
        print(f"Created {alignments_created} MSAVI alignments")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--track', type=int, required=True)
    parser.add_argument('--orbit', choices=['ASCENDING', 'DESCENDING'], required=True)
    parser.add_argument('--subswath', choices=['IW1', 'IW2', 'IW3'], required=True)
    parser.add_argument('--max-days', type=int, default=30)
    args = parser.parse_args()
    
    align_msavi_to_insar(args.track, args.orbit, args.subswath, args.max_days)
```

#### Issue 3.2: Add Spatial Query Capabilities
**Repository:** `goshawk_ETL`  
**File:** `scripts/query_by_aoi.py` (NEW)

```python
"""Query products by Area of Interest using PostGIS spatial functions."""

from shapely.geometry import shape
from geoalchemy2 import func as gis_func
from db_connection import get_db_api


def query_products_by_aoi(aoi_geojson_path: str, product_type: str = None):
    """
    Find all products intersecting an AOI.
    
    Args:
        aoi_geojson_path: Path to AOI GeoJSON file
        product_type: Optional filter by product type
        
    Returns:
        List of Product instances
    """
    # Load AOI
    with open(aoi_geojson_path) as f:
        aoi_data = json.load(f)
    
    aoi_geom = shape(aoi_data['features'][0]['geometry'])
    aoi_wkt = aoi_geom.wkt
    
    with get_db_api() as db:
        query = db.session.query(Product).filter(
            gis_func.ST_Intersects(
                Product.footprint,
                gis_func.ST_GeomFromText(aoi_wkt, 4326)
            )
        )
        
        if product_type:
            query = query.filter(Product.product_type == product_type)
        
        products = query.all()
        
        print(f"Found {len(products)} products intersecting AOI")
        return products
```

## Migration Checklist

### Phase 1: Extend satelit_metadata
- [ ] Add `SENTINEL2_MSAVI` and `URBAN_CROP` to product_type enum
- [ ] Add S2-specific fields (cloud_cover, tile_id, etc.)
- [ ] Implement `register_s2_product()` API method
- [ ] Implement `register_msavi_product()` API method
- [ ] Implement `find_closest_product()` API method
- [ ] Implement `get_insar_pairs()` API method
- [ ] Create and run Alembic migration
- [ ] Test all new API methods

### Phase 2: Migrate goshawk_ETL
- [ ] Setup PostgreSQL + PostGIS database
- [ ] Add satelit_metadata dependency
- [ ] Create `db_connection.py` wrapper
- [ ] Refactor `download_copernicus.py`
- [ ] Refactor `process_insar_gpt.py`
- [ ] Refactor `process_sentinel2_msavi.py`
- [ ] Refactor `batch_aoi_crop.py`
- [ ] Deprecate old SQLite implementation
- [ ] Update all documentation
- [ ] Test end-to-end workflow

### Phase 3: Advanced Features
- [ ] Implement MSAVI-InSAR alignment script
- [ ] Add spatial query capabilities
- [ ] Add processing run tracking
- [ ] Add quality metrics dashboard
- [ ] Performance optimization (indexes, caching)

## Benefits of Migration

### Technical Benefits
- ✅ PostgreSQL performance and scalability
- ✅ PostGIS spatial queries and indexing
- ✅ SQLAlchemy ORM (type safety, relations)
- ✅ Proper schema migrations (Alembic)
- ✅ Full lineage tracking
- ✅ Multi-user support
- ✅ Transaction safety

### Operational Benefits
- ✅ Single source of truth across all projects
- ✅ Spatial queries (find products by AOI)
- ✅ Advanced relationship tracking
- ✅ Processing run history
- ✅ Download queue management
- ✅ Better error handling
- ✅ Production-ready architecture

### Development Benefits
- ✅ Separation of concerns (DB logic separate)
- ✅ Reusable across projects
- ✅ Type hints and IDE support
- ✅ Easier testing (mock sessions)
- ✅ Standard Python packaging
- ✅ Version controlled schema

## Estimated Timeline

| Phase | Tasks | Estimated Time |
|-------|-------|----------------|
| **Phase 1** | Extend satelit_metadata | 1-2 days |
| **Phase 2** | Migrate goshawk_ETL | 2-3 days |
| **Phase 3** | Advanced features | 1-2 days |
| **Testing** | End-to-end validation | 1 day |
| **Total** | | **5-8 days** |

## Rollback Plan

If migration issues arise:

1. Keep old SQLite implementation in `scripts/deprecated/`
2. Environment variable to switch backends:
   ```python
   USE_LEGACY_DB = os.getenv('USE_LEGACY_DB', 'false').lower() == 'true'
   ```
3. Gradual migration (one script at a time)
4. Parallel running during transition period

## Conclusion

The migration from simple SQLite to `satelit_metadata` PostgreSQL architecture provides:

- **Production-ready** infrastructure
- **Scalability** for large projects
- **Spatial capabilities** with PostGIS
- **Proper lineage** tracking
- **Reusability** across projects

The current simple implementation served well for prototyping Issues #1, #2, #6, #8, but the production system should use the robust `satelit_metadata` architecture.

**Recommendation:** Proceed with migration in phases, starting with Phase 1 (extend satelit_metadata) to ensure foundation is solid before migrating goshawk_ETL.

---

**Next Steps:**
1. Review and approve this plan
2. Create GitHub issues for Phase 1
3. Begin implementation with `satelit_metadata` extensions
4. Test thoroughly before migrating goshawk_ETL
