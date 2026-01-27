# Smart Workflow - goshawk_ETL + satelit_metadata

**Sistema de trazabilidad y workflow optimizado para procesamiento SAR/InSAR**

---

## 🎉 ¿Qué es esto?

El **Smart Workflow** es una evolución del workflow tradicional de goshawk_ETL que:

✅ **Consulta la base de datos** antes de procesar
✅ **Decide automáticamente** qué es necesario hacer
✅ **Evita reprocesamiento** innecesario
✅ **Ahorra hasta 99% de tiempo** si productos ya están procesados
✅ **Reutiliza productos** entre diferentes proyectos

---

## ⚡ Quick Start

### 1. Iniciar base de datos (una vez)

```bash
cd ../satelit_metadata
make setup
cd ../goshawk_ETL
conda env update -f environment.yml
conda deactivate && conda activate goshawk_etl
```

### 2. Ver qué se necesita hacer

```bash
python scripts/smart_workflow_planner.py \
  --aoi-geojson aoi/tu_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

**Salida ejemplo:**
```
SMART WORKFLOW PLAN
================================================================================

📊 Track: desc_iw1_t088
   Decision: ✅ All products processed - CROP ONLY
   Actions:
      ✂️  CROP to AOI only (FAST!)

SUMMARY:
  ✂️  Crop only (fastest):     1 tracks

⏱️  Tiempo estimado: 10-15 minutos (en lugar de 6-8 horas!)
```

### 3. Ejecutar workflow optimizado

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/tu_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

---

## 📊 Ahorro de Tiempo

| Escenario | Tradicional | Smart Workflow | Ahorro |
|-----------|-------------|----------------|--------|
| **Mismo track, nuevo AOI** | 6-8 horas | 10-15 min | **99%** ⚡ |
| **SLCs descargados** | 6-8 horas | 2-3 horas | **60%** |
| **Productos parciales** | 6-8 horas | 3-4 horas | **50%** |
| **Primera vez** | 6-8 horas | 6-8 horas | 0% |

---

## 🎯 Ejemplos de Uso

### Ejemplo 1: Nuevo AOI, mismo track

Ya procesaste **Arenys de Munt**, ahora quieres **Vilademuls** (mismo track 88):

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/vilademuls.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

**Resultado:** Solo crop → **15 minutos** en lugar de 6-8 horas

---

### Ejemplo 2: Ampliar período temporal

Ya tienes Enero-Junio, ahora quieres Julio-Diciembre:

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/arenys.geojson \
  --start-date 2023-07-01 \
  --end-date 2023-12-31
```

**Resultado:** Solo procesa nuevos productos

---

### Ejemplo 3: Ver plan sin ejecutar

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --dry-run
```

**Resultado:** Muestra plan detallado, no ejecuta nada

---

## 🔍 Cómo Funciona

### Paso 1: Consulta Base de Datos

```
Usuario selecciona AOI + fechas
    ↓
Smart Workflow consulta BD:
  - ¿Qué productos existen?
  - ¿Qué está procesado?
  - ¿Qué falta?
```

### Paso 2: Decisión Inteligente

```
SI todo procesado:
  → CROP ONLY (15 min) ✂️

SI SLCs descargados pero no procesados:
  → PROCESS ONLY (2-3h) ⚡

SI faltan productos:
  → FULL WORKFLOW (6-8h) 🔄
```

### Paso 3: Ejecución Optimizada

Solo ejecuta las etapas necesarias.

---

## 📚 Documentación

- **Quick Start**: `QUICKSTART_SMART_WORKFLOW.md` - 5 minutos
- **Guía Completa**: `docs/SMART_WORKFLOW_USAGE.md` - Todos los detalles
- **Conceptos**: `docs/SMART_WORKFLOW.md` - Cómo funciona
- **Integración BD**: `docs/DB_INTEGRATION.md` - Setup de base de datos

---

## 🛠️ Comandos Principales

### Planificación

```bash
# Ver qué se necesita hacer
python scripts/smart_workflow_planner.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

### Ejecución

```bash
# Ejecutar workflow optimizado
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31

# Con dry-run (no ejecuta)
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --dry-run

# Forzar workflow completo (ignorar BD)
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --force-full
```

### Consultas BD

```bash
# Ver estadísticas de un track
satelit-db track-stats --orbit DESCENDING --subswath IW1 --track 88

# Ver productos procesados
satelit-db list-products --type INSAR_SHORT --track 88

# Ver qué SLCs pueden borrarse
satelit-db deletable-slcs --track 88
```

---

## 🔧 Troubleshooting

### "Database integration: DISABLED"

```bash
# Iniciar base de datos
cd ../satelit_metadata
make db-up

# Verificar
cd ../goshawk_ETL
python scripts/db_example_usage.py
```

### Smart Workflow no detecta productos existentes

```bash
# Migrar datos históricos a BD
cd ../satelit_metadata
python scripts/migrate_json_to_db.py --data-root /mnt/satelit_data
```

---

## 📊 Arquitectura

```
goshawk_ETL/
├── scripts/
│   ├── smart_workflow_planner.py    # Motor de decisión
│   ├── run_smart_workflow.py        # Orchestrator
│   ├── db_integration.py            # Integración con BD
│   └── ...
├── docs/
│   ├── SMART_WORKFLOW.md            # Conceptos
│   ├── SMART_WORKFLOW_USAGE.md      # Guía de uso
│   └── DB_INTEGRATION.md            # Setup BD
├── QUICKSTART_SMART_WORKFLOW.md     # Quick start
└── README_SMART_WORKFLOW.md         # Este archivo

satelit_metadata/
├── satelit_db/                      # Paquete Python
│   ├── models.py                    # Schema SQLAlchemy
│   ├── api.py                       # API de alto nivel
│   └── cli.py                       # Comandos CLI
├── docker-compose.yml               # PostgreSQL + PostGIS
└── scripts/
    ├── migrate_json_to_db.py        # Migración datos históricos
    └── cleanup_slc.py               # Cleanup inteligente
```

---

## ✅ Características

- ✅ Consulta automática de BD antes de procesar
- ✅ Tres estrategias optimizadas: CROP/PROCESS/FULL
- ✅ Ahorro hasta 99% de tiempo
- ✅ Reutilización entre proyectos
- ✅ Degradación graciosa (funciona sin BD)
- ✅ Dry-run para planificación
- ✅ Confirmación interactiva
- ✅ Logging completo
- ✅ 100% compatible con workflow tradicional

---

## 🎓 Casos de Uso

### ✅ Perfecto para:

- Analizar múltiples AOIs en el mismo track
- Ampliar período temporal de análisis existente
- Planificar procesamiento antes de ejecutar
- Reutilizar productos entre proyectos
- Optimizar uso de recursos computacionales

### ⚠️ No necesario para:

- Primera vez procesando un track nuevo
- Procesamiento único sin reutilización
- Desarrollo/testing de algoritmos (usa `--force-full`)

---

## 🚀 Comenzar Ahora

```bash
# 1. Setup (una vez)
cd ../satelit_metadata && make setup
cd ../goshawk_ETL && conda env update -f environment.yml

# 2. Tu primer workflow inteligente
python scripts/smart_workflow_planner.py \
  --aoi-geojson aoi/tu_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31

# 3. Ejecutar
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/tu_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

---

## 📞 Soporte

- **Documentación completa**: Ver `docs/SMART_WORKFLOW_USAGE.md`
- **Integración**: Ver `INTEGRATION_SUMMARY.md`
- **Issues**: Revisar troubleshooting en documentación

---

**Versión**: 2.0
**Fecha**: 2025-01-21
**Autor**: goshawk_ETL Team + satelit_metadata integration

**¡Ahorra horas de procesamiento con Smart Workflow! ⚡**
