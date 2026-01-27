# Integración con Satelit Metadata Database

**Trazabilidad completa de productos SAR/InSAR**

Este documento describe la integración de `goshawk_ETL` con el sistema de trazabilidad `satelit_metadata`.

---

## 🎯 Características

La integración proporciona:

- ✅ **Prevención de descargas duplicadas**: Verifica en BD antes de descargar
- ✅ **Trazabilidad completa**: Cada producto registrado con metadata completa
- ✅ **Decisiones de cleanup inteligentes**: Saber qué SLCs pueden borrarse
- ✅ **Queries espaciales y temporales**: PostGIS para búsquedas avanzadas
- ✅ **Compartir productos entre proyectos**: Reutilización sin duplicar procesamiento
- ✅ **Degradación graciosa**: Si BD no disponible, funciona en modo legacy

---

## 📦 Setup

### 1. Iniciar Satelit Metadata Database

```bash
cd ../satelit_metadata
make setup
```

Esto levantará PostgreSQL + PostGIS en Docker y creará el schema.

### 2. Instalar Dependencias en goshawk_ETL

```bash
# Actualizar conda environment (ya incluye satelit_db)
conda env update -f environment.yml

# Reactivar environment
conda deactivate
conda activate goshawk_etl
```

### 3. Verificar Instalación

```bash
python scripts/db_example_usage.py
```

Deberías ver:
```
✅ Database integration is ENABLED
```

---

## 🔧 Uso Automático

La integración está **completamente automática**. Los scripts existentes detectan automáticamente si la BD está disponible y la usan si es posible.

### Download Script (download_copernicus.py)

Cambios automáticos:

**ANTES de descargar:**
- ✓ Verifica en BD si el producto ya está registrado
- ✓ Si existe y el archivo local también, omite descarga

**DESPUÉS de descargar:**
- ✓ Registra producto en BD con metadata completa
- ✓ Registra ubicación de almacenamiento

**Ejemplo de uso (sin cambios):**

```bash
python scripts/download_copernicus.py \
  --collection SENTINEL-1 \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --orbit-direction DESCENDING \
  --start-date 2025-01-01 \
  --end-date 2025-01-31
```

Verás en el log:
```
🗄️  Database integration: ENABLED (satelit_metadata)
   - Preventing duplicate downloads
   - Tracking product traceability
```

### InSAR Repository (insar_repository.py)

Cambios automáticos:

**AL añadir productos InSAR:**
- ✓ Registra productos InSAR en BD
- ✓ Crea relaciones de linaje (InSAR → 2 SLCs)
- ✓ Registra metadata de coherencia y baselines

**Ejemplo de uso (sin cambios):**

```bash
python scripts/insar_repository.py \
  --add-products processing/arenys/insar_desc_iw1 \
  --orbit DESCENDING \
  --subswath IW1 \
  --track 88
```

---

## 🐍 Uso Programático

### Importar módulo de integración

```python
from db_integration import get_db_integration

db = get_db_integration()
```

### Ejemplos

#### 1. Verificar si SLC descargado

```python
scene_id = "S1A_IW_SLC__1SDV_20230111T060136_..."

if db.is_slc_downloaded(scene_id):
    print("Ya descargado!")
else:
    print("Necesita descargarse")
```

#### 2. Estadísticas de track

```python
stats = db.get_track_statistics(
    orbit_direction="DESCENDING",
    subswath="IW1",
    track_number=88
)

print(f"Total SLCs: {stats['total_slc']}")
print(f"InSAR pairs: {stats['total_insar_short'] + stats['total_insar_long']}")
print(f"Size: {stats['total_size_gb']:.2f} GB")
```

#### 3. Verificar si SLC puede borrarse

```python
slc_path = "/mnt/satelit_data/sentinel1_slc/S1A_IW_SLC__..."

can_delete, reason = db.can_delete_slc(slc_path)

if can_delete:
    print(f"Puede borrarse: {reason}")
    # shutil.rmtree(slc_path)  # Borrar de forma segura
else:
    print(f"NO borrar: {reason}")
```

#### 4. Registrar SLC manualmente

```python
from datetime import datetime

product_id = db.register_slc_download(
    scene_id="S1A_IW_SLC__1SDV_20230111T060136_...",
    acquisition_date=datetime(2023, 1, 11, 6, 1, 36),
    file_path="/mnt/satelit_data/sentinel1_slc/S1A_...",
    orbit_direction="DESCENDING",
    relative_orbit=88,
    absolute_orbit=46714,
    subswath="IW1",
)

print(f"Registered with ID: {product_id}")
```

---

## 💻 Comandos CLI

La base de datos incluye un CLI completo para consultas:

### Estadísticas generales

```bash
satelit-db stats
```

### Listar productos

```bash
# SLCs de un track
satelit-db list-products --type SLC --track 88 --subswath IW1 --orbit DESCENDING

# Productos de enero 2023
satelit-db list-products --start-date 2023-01-01 --end-date 2023-01-31

# Primeros 20 productos
satelit-db list-products --limit 20
```

### Estadísticas de track

```bash
satelit-db track-stats --orbit DESCENDING --subswath IW1 --track 88
```

Salida:
```
Track: desc_iw1_t088

Total SLC products            45
Processed SLCs               45
InSAR short pairs            88
InSAR long pairs             86
Polarimetry products         45
Total size (GB)              234.56
First acquisition            2023-01-11
Last acquisition             2023-12-30
```

### SLCs que pueden borrarse

```bash
# Ver cuáles pueden borrarse
satelit-db deletable-slcs --track 88 --subswath IW1

# Verificar uno específico
satelit-db can-delete 12345  # 12345 = product ID
```

### Cola de descargas

```bash
satelit-db list-downloads --status PENDING
```

### Procesamiento reciente

```bash
satelit-db recent-processing --limit 20
```

---

## 🗄️ Queries SQL Avanzadas

Para usuarios avanzados, pueden acceder directamente a PostgreSQL:

```bash
# Abrir shell PostgreSQL
cd ../satelit_metadata
make db-shell
```

### Ejemplos de queries

#### 1. SLCs que pueden borrarse

```sql
SELECT p.id, p.scene_id, p.acquisition_date,
       SUM(sl.file_size_gb) as size_gb
FROM satelit.products p
LEFT JOIN satelit.storage_locations sl ON sl.product_id = p.id
WHERE p.product_type = 'SLC'
  AND p.processing_status = 'PROCESSED'
  AND NOT EXISTS (
      -- No tiene InSAR incompletos
      SELECT 1 FROM satelit.products ip
      JOIN satelit.product_lineage pl ON pl.child_product_id = ip.id
      WHERE pl.parent_product_id = p.id
        AND ip.product_type IN ('INSAR_SHORT', 'INSAR_LONG')
        AND ip.processing_status != 'PROCESSED'
  )
GROUP BY p.id, p.scene_id, p.acquisition_date
ORDER BY size_gb DESC;
```

#### 2. Productos que cubren un AOI

```sql
SELECT scene_id, acquisition_date, product_type
FROM satelit.products
WHERE ST_Intersects(
    footprint,
    ST_MakeEnvelope(2.49, 41.58, 2.57, 41.64, 4326)
)
ORDER BY acquisition_date DESC;
```

#### 3. Cobertura temporal de tracks

```sql
SELECT
    orbit_direction,
    subswath,
    track_number,
    COUNT(*) AS num_scenes,
    MIN(acquisition_date) AS first_date,
    MAX(acquisition_date) AS last_date,
    MAX(acquisition_date) - MIN(acquisition_date) AS coverage_days
FROM satelit.products
WHERE product_type = 'SLC'
GROUP BY orbit_direction, subswath, track_number
ORDER BY track_number, subswath;
```

---

## 🔧 Troubleshooting

### Error: "Database integration: DISABLED"

**Causa**: satelit_db no instalado o BD no corriendo

**Solución**:
```bash
# 1. Verificar que satelit_metadata está corriendo
cd ../satelit_metadata
docker compose ps  # Debe mostrar postgres corriendo

# Si no está corriendo:
make db-up

# 2. Reinstalar environment
cd ../goshawk_ETL
conda env update -f environment.yml
conda deactivate
conda activate goshawk_etl
```

### Error: "Could not connect to database"

**Causa**: PostgreSQL no está corriendo o variables de entorno incorrectas

**Solución**:
```bash
cd ../satelit_metadata

# Verificar estado
docker compose ps

# Ver logs
docker compose logs postgres

# Reiniciar
docker compose restart postgres
```

### Error: "satelit_db module not found"

**Causa**: Paquete no instalado en conda environment

**Solución**:
```bash
conda env update -f environment.yml --prune
```

---

## 📊 Schema de Base de Datos

### Tablas principales

1. **products** - Catálogo universal
   - SLC, GRD, InSAR, Polarimetry, Sentinel-2

2. **product_lineage** - Relaciones
   - InSAR → 2 SLCs (master + slave)

3. **processing_runs** - Historial
   - Todos los procesamientos con parámetros

4. **repository_tracks** - Tracks
   - Metadata agregada por track

5. **download_queue** - Descargas
   - Gestión de cola de descargas

6. **storage_locations** - Archivos
   - Ubicaciones físicas + tamaños

---

## 🎓 Casos de Uso

### Caso 1: Liberar 100 GB de espacio

```bash
# 1. Ver qué SLCs pueden borrarse
satelit-db deletable-slcs --track 88 --subswath IW1

# 2. Ejecutar script de cleanup (en satelit_metadata)
cd ../satelit_metadata
python scripts/cleanup_slc.py --track 88 --subswath IW1 --dry-run

# 3. Si OK, ejecutar realmente
python scripts/cleanup_slc.py --track 88 --subswath IW1 --execute
```

### Caso 2: Verificar progreso de track

```bash
satelit-db track-stats --orbit DESCENDING --subswath IW1 --track 88
```

### Caso 3: Evitar re-descargar productos

Los scripts automáticamente evitan re-descargas si el producto está en BD.

---

## 📚 Documentación Adicional

- **README principal**: `../satelit_metadata/README.md`
- **Quick Start**: `../satelit_metadata/QUICKSTART.md`
- **Ejemplos**: `scripts/db_example_usage.py`
- **Integración**: `../satelit_metadata/docs/integration_examples.md`

---

## ✅ Checklist de Integración

- [x] PostgreSQL + PostGIS corriendo
- [x] satelit_db instalado en conda environment
- [x] Scripts detectan automáticamente BD
- [x] download_copernicus.py registra descargas
- [x] insar_repository.py registra procesamiento
- [x] CLI satelit-db funcional
- [x] Queries funcionan correctamente

---

**Versión**: 1.0
**Fecha**: 2025-01-21
**Mantenedor**: goshawk_ETL Team
