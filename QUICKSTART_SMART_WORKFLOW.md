# Quick Start - Smart Workflow

**Procesamiento optimizado en 3 pasos**

---

## ⚡ Instalación (Una vez)

```bash
# 1. Iniciar base de datos
cd ../satelit_metadata
make setup

# 2. Actualizar environment
cd ../goshawk_ETL
conda env update -f environment.yml
conda deactivate && conda activate goshawk_etl

# 3. Verificar
python scripts/db_example_usage.py
# Debe mostrar: "✅ Database integration is ENABLED"
```

---

## 🚀 Uso Básico

**IMPORTANTE:** Los scripts deben ejecutarse desde el directorio raíz de `goshawk_ETL`.

### Ver qué se necesita hacer (sin ejecutar)

```bash
# Asegúrate de estar en goshawk_ETL root
cd /home/jmiro/Github/goshawk_ETL

python scripts/smart_workflow_planner.py \
  --aoi-geojson aoi/tu_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

### Ejecutar workflow optimizado

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/tu_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

**Nota:** Las rutas son relativas al directorio del repositorio:
- `aoi/` → AOIs dentro de goshawk_ETL
- `data/` → Symlink a `/mnt/satelit_data` (SLCs, productos procesados)
- `processing/` → Directorio de procesamiento dentro de goshawk_ETL

---

## 📊 ¿Qué hace diferente el Smart Workflow?

| Escenario | Tradicional | Smart Workflow | Tiempo Ahorrado |
|-----------|-------------|----------------|-----------------|
| Productos ya procesados | Procesa todo (6-8h) | Solo crop (15 min) | **99%** |
| SLCs descargados | Procesa todo (6-8h) | Solo procesa (2-3h) | **60%** |
| Nuevo proyecto | Procesa todo (6-8h) | Procesa todo (6-8h) | 0% |

---

## 🎯 Casos de Uso Comunes

### 1. Nuevo AOI, mismo track que proyecto anterior

```bash
# Si ya procesaste Arenys, Vilademuls (mismo track) toma 15 minutos
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/vilademuls.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

**Resultado:** Solo crop (15 min en lugar de 6-8 horas)

---

### 2. Ampliar período temporal

```bash
# Ya tienes Enero-Junio, ahora quieres Julio-Diciembre
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-07-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

**Resultado:** Solo procesa productos nuevos

---

### 3. Ver plan sin ejecutar

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2 \
  --dry-run
```

**Resultado:** Muestra plan, no ejecuta nada

---

## 🔧 Comandos Útiles

### Ver estadísticas de un track

```bash
satelit-db track-stats --orbit DESCENDING --subswath IW1 --track 88
```

### Ver qué SLCs pueden borrarse

```bash
satelit-db deletable-slcs --track 88 --subswath IW1
```

### Ver productos procesados

```bash
satelit-db list-products --type INSAR_SHORT --track 88
```

---

## 📚 Documentación Completa

- **Guía detallada**: `docs/SMART_WORKFLOW_USAGE.md`
- **Conceptos**: `docs/SMART_WORKFLOW.md`
- **Integración BD**: `docs/DB_INTEGRATION.md`

---

## 🆘 Problemas Comunes

### "Database integration: DISABLED"

```bash
cd ../satelit_metadata && make db-up
```

### Smart workflow no detecta productos existentes

```bash
# Migrar datos históricos
cd ../satelit_metadata
python scripts/migrate_json_to_db.py --data-root /mnt/satelit_data
```

---

**¡Listo para ahorrar horas de procesamiento! ⚡**

Ejecuta:
```bash
python scripts/smart_workflow_planner.py --aoi-geojson aoi/tu_aoi.geojson --start-date 2023-01-01 --end-date 2023-12-31
```
