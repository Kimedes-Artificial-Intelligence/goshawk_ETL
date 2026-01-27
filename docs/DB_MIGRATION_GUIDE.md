# Database Schema Setup Instructions

## Quick Start

### 1. Apply the Migration

```bash
# Navigate to satelit_metadata repository
cd ../satelit_metadata

# Run the migration
alembic upgrade head
```

### 2. Verify the Schema

```bash
# Return to goshawk_ETL
cd ../goshawk_ETL

# Run verification script
python scripts/test_db_schema.py
```

Expected output:
```
✓ All granular tracking tables exist
✓ Database schema is properly initialized
```

## What This Migration Does

This migration adds four new tables to the `satelit` schema:

1. **slc_products** - Tracks Sentinel-1 SLC products with per-subswath processing flags
2. **insar_pairs** - Tracks full-swath InSAR pair processing results
3. **s2_products** - Tracks Sentinel-2 products with MSAVI processing status
4. **insar_pair_msavi** - Links InSAR pairs with corresponding MSAVI products

## Rollback (if needed)

If you need to rollback the migration:

```bash
cd ../satelit_metadata
alembic downgrade -1
```

This will remove the four new tables.

## Troubleshooting

### Database Connection Issues

If you see database connection errors:

```bash
# Check if PostgreSQL is running
cd ../satelit_metadata
docker-compose ps

# If not running, start it
docker-compose up -d

# Wait a few seconds, then retry migration
alembic upgrade head
```

### Migration Conflicts

If Alembic reports conflicts with existing migrations:

```bash
# Check current database version
alembic current

# Check migration history
alembic history

# If needed, stamp the database to match your state
alembic stamp head
```

### Manual Schema Check

To manually verify tables exist:

```bash
# Connect to database
docker exec -it satelit_metadata_postgres psql -U satelit -d satelit_db

# List tables in satelit schema
\dt satelit.*

# Expected output should include:
#   satelit.slc_products
#   satelit.insar_pairs
#   satelit.s2_products
#   satelit.insar_pair_msavi
```

## Next Steps

After successful migration:

1. Read `docs/DATABASE_SCHEMA.md` for detailed schema documentation
2. Update download scripts to use new tables
3. Update processing scripts to set processing flags
4. Implement cleanup logic based on processing status

## Related Documentation

- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) - Complete schema documentation
- [../satelit_metadata/README.md](../../satelit_metadata/README.md) - Database setup guide
