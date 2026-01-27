# Smart Workflow - Procesamiento Optimizado con BD

**Nuevo workflow inteligente que evita reprocesamiento innecesario**

---

## 🎯 Problema Resuelto

### Workflow Tradicional (Antes)

```
Usuario selecciona AOI + fechas
    ↓
SIEMPRE:
1. Descargar SLCs (puede que ya existan)
2. Procesar InSAR (puede que ya esté procesado)
3. Procesar Polarimetría (puede que ya esté procesado)
4. Crop a AOI
```

**Problemas:**
- ❌ Re-descarga productos que ya existen
- ❌ Re-procesa InSAR que ya está en el repositorio
- ❌ Desperdicia horas de procesamiento
- ❌ No aprovecha productos compartidos entre proyectos

### Smart Workflow (Ahora)

```
Usuario selecciona AOI + fechas
    ↓
Consulta BD: ¿Qué productos YA existen?
    ↓
    ├─ ✅ TODO procesado → SOLO CROP (5 min)
    ├─ ⚡ SLCs descargados → SOLO PROCESAR (2-3 horas)
    └─ 🔄 Productos faltantes → WORKFLOW COMPLETO
```

**Beneficios:**
- ✅ Evita re-descargas (ahorra GB + tiempo)
- ✅ Evita reprocesamiento (ahorra horas)
- ✅ Reutiliza productos entre proyectos
- ✅ Decisión inteligente basada en datos reales

---

## 📊 Lógica de Decisión

El sistema consulta la BD y decide automáticamente:

### Caso 1: TODO Procesado → CROP ONLY ✂️

**Condiciones:**
- ✅ SLCs descargados y procesados
- ✅ InSAR short pairs completos
- ✅ InSAR long pairs completos
- ✅ Polarimetría procesada

**Acción:**
```bash
# Solo ejecuta crop a AOI (~5 minutos)
python scripts/crop_insar_to_aoi.py
python scripts/crop_polarimetry_to_aoi.py
```

**Tiempo ahorrado:** ~4-6 horas de procesamiento

---

### Caso 2: SLCs Descargados → PROCESS ONLY ⚡

**Condiciones:**
- ✅ SLCs ya descargados
- ❌ InSAR no procesado (o incompleto)
- ❌ Polarimetría no procesada

**Acción:**
```bash
# SKIP download, SOLO procesar
python scripts/process_insar_series.py
python scripts/process_polarimetry.py
python scripts/crop_to_aoi.py
```

**Tiempo ahorrado:** ~30 min - 1 hora de descarga

---

### Caso 3: Productos Parciales → COMPLETE WORKFLOW 🔄

**Condiciones:**
- ⚠️ Algunos SLCs presentes, otros faltan
- ⚠️ Procesamiento incompleto

**Acción:**
```bash
# Workflow completo
download → process → crop
```

**Beneficio:** Completa lo que falta sin duplicar lo existente

---

### Caso 4: Track Vacío → FULL WORKFLOW 🆕

**Condiciones:**
- ❌ No hay productos en BD para este track

**Acción:**
```bash
# Workflow completo desde cero
download → process → crop
```

---

## 🛠️ Uso del Smart Workflow Planner

### Opción 1: Consulta Previa (Recomendado)

Antes de ejecutar el workflow completo, consulta qué se necesita:

```bash
python scripts/smart_workflow_planner.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
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
   Decision: ✅ All products processed (45 SLCs, 88 InSAR short, 86 long, 45 polarimetry) - CROP ONLY
   Existing: {'slc': 45, 'insar_short': 88, 'insar_long': 86, 'polarimetry': 45}
   Actions:
      ✂️  CROP to AOI only (FAST!)

📊 Track: desc_iw2_t088
   Decision: ⚡ SLCs already downloaded (42) - SKIP DOWNLOAD, PROCESS ONLY
   Existing: {'slc': 42, 'insar_short': 0, 'insar_long': 0, 'polarimetry': 0}
   Actions:
      ⚙️  PROCESS InSAR + Polarimetry

SUMMARY:
  ✂️  Crop only (fastest):     1 tracks
  ⚡ Process only (no download): 1 tracks
  🔄 Full workflow:             0 tracks
```

### Opción 2: Uso Programático

En tu script:

```python
from smart_workflow_planner import SmartWorkflowPlanner
from datetime import datetime

planner = SmartWorkflowPlanner()

# Analizar un track específico
decision = planner.analyze_track_coverage(
    orbit_direction="DESCENDING",
    subswath="IW1",
    track_number=88,
    start_date=datetime(2023, 1, 1),
    end_date=datetime(2023, 12, 31),
)

if decision.needs_crop_only:
    print("✅ Solo crop necesario - ejecutando...")
    # run_crop_workflow()
elif not decision.needs_download and decision.needs_processing:
    print("⚡ Solo procesamiento necesario...")
    # run_processing_workflow()
else:
    print("🔄 Workflow completo necesario...")
    # run_complete_workflow()
```

---

## 🔄 Integración con run_complete_workflow.py

### Modificación Sugerida

Añadir al inicio de `run_complete_workflow.py`:

```python
from smart_workflow_planner import SmartWorkflowPlanner

# Después de seleccionar AOI y fechas...
planner = SmartWorkflowPlanner()

decisions = planner.plan_workflow(
    aoi_geojson=aoi_file,
    start_date=start_date,
    end_date=end_date,
    orbit_directions=orbit_directions,
    subswaths=subswaths,
)

# Mostrar plan
planner.print_workflow_plan(decisions)

# Pedir confirmación
confirm = input("\nProceder con este plan? (y/n): ")
if confirm.lower() != 'y':
    sys.exit(0)

# Ejecutar según decisión
for track_id, decision in decisions.items():
    if decision.needs_crop_only:
        # FAST PATH: Solo crop
        run_crop_only_workflow(track_id, aoi_file)
    elif not decision.needs_download:
        # MEDIUM PATH: Solo procesamiento
        run_processing_workflow(track_id, skip_download=True)
    else:
        # FULL PATH: Workflow completo
        run_full_workflow(track_id)
```

---

## 📈 Ejemplos de Ahorro de Tiempo

### Ejemplo Real 1: Proyecto Arenys (track 88)

**Escenario:** Usuario quiere analizar Arenys de Munt para 2023 completo

**Workflow Tradicional:**
```
1. Download SLCs: 1-2 horas
2. Process InSAR: 3-4 horas
3. Process Polarimetry: 1-2 horas
4. Crop to AOI: 10-15 min
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL: ~6-8 horas
```

**Smart Workflow (productos ya procesados):**
```
1. Query BD: 2 segundos
2. Decision: CROP ONLY
3. Crop to AOI: 10-15 min
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL: ~15 minutos ⚡
```

**Ahorro:** ~7 horas (99% más rápido!)

---

### Ejemplo Real 2: Nuevo AOI, Track Compartido

**Escenario:** Usuario quiere analizar Vilademuls (mismo track 88 que Arenys)

**Workflow Tradicional:**
```
Procesa TODO desde cero (6-8 horas)
```

**Smart Workflow:**
```
1. Query BD: Track 88 ya tiene productos procesados
2. Decision: CROP ONLY
3. Crop to nuevo AOI: 10-15 min
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL: ~15 minutos
```

**Beneficio:** Reutilización instantánea entre proyectos

---

## 🔍 Queries para Diagnóstico

### Ver qué tracks tienen productos procesados

```bash
satelit-db list-products --type INSAR_SHORT --limit 100
```

### Estadísticas de un track

```bash
satelit-db track-stats --orbit DESCENDING --subswath IW1 --track 88
```

### Productos en un rango de fechas

```bash
satelit-db list-products \
  --track 88 \
  --subswath IW1 \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

---

## 🎓 Casos de Uso

### Caso 1: Análisis Multi-temporal (mismo AOI, diferentes fechas)

```bash
# Primera vez: Enero-Marzo 2023
python run_complete_workflow.py
# → Workflow completo (6 horas)

# Segunda vez: Abril-Junio 2023
python run_complete_workflow.py
# → Smart planner detecta que Enero-Marzo ya está procesado
# → Solo procesa nuevos meses (2-3 horas en lugar de 6)
```

### Caso 2: Análisis Multi-AOI (mismo track, diferentes áreas)

```bash
# Primera vez: Arenys de Munt
python run_complete_workflow.py
# → Workflow completo (6 horas)

# Segunda vez: Vilademuls (mismo track 88!)
python run_complete_workflow.py
# → Smart planner detecta que track 88 ya procesado
# → Solo crop a nuevo AOI (15 minutos!)
```

### Caso 3: Re-análisis con Mejores Parámetros

```bash
# Usuario quiere re-procesar con filtros diferentes
# Opción manual: Forzar reprocesamiento
python run_complete_workflow.py --force-reprocess

# Smart planner avisa:
"⚠️  WARNING: Products exist but --force-reprocess enabled"
```

---

## ⚙️ Configuración

### Habilitar Smart Workflow

**Requisitos:**
1. ✅ satelit_metadata database corriendo
2. ✅ satelit_db instalado en environment
3. ✅ Productos registrados en BD

**Setup:**
```bash
# 1. Verificar BD corriendo
cd ../satelit_metadata
docker compose ps

# 2. Verificar integración
cd ../goshawk_ETL
python scripts/db_example_usage.py
# Debe mostrar: "✅ Database integration is ENABLED"
```

### Deshabilitar (Modo Legacy)

Si por alguna razón necesitas el comportamiento anterior:

```bash
# Parar database
cd ../satelit_metadata
docker compose down

# O forzar modo legacy en código:
db = get_db_integration(enabled=False)
```

---

## 📊 Métricas de Rendimiento

| Operación | Tradicional | Smart (Crop Only) | Smart (Process Only) | Ahorro |
|-----------|-------------|-------------------|---------------------|---------|
| Query BD | 0 min | 0.03 min | 0.03 min | - |
| Download | 60-120 min | 0 min | 0 min | 100% |
| Process | 240-360 min | 0 min | 240-360 min | 0-100% |
| Crop | 10-15 min | 10-15 min | 10-15 min | 0% |
| **TOTAL** | **310-495 min** | **10-15 min** | **250-375 min** | **95-97%** |

---

## 🔧 Troubleshooting

### "Smart planner no detecta productos existentes"

**Causa:** Productos no registrados en BD

**Solución:**
```bash
# Migrar datos existentes
cd ../satelit_metadata
python scripts/migrate_json_to_db.py --data-root /mnt/satelit_data
```

### "Decision muestra 'full workflow' cuando debería ser 'crop only'"

**Causa:** Productos no marcados como PROCESSED

**Solución:**
```bash
# Verificar estado de productos
satelit-db list-products --track 88 --status PROCESSED
```

---

## 📚 Documentación Relacionada

- **Integración BD**: `docs/DB_INTEGRATION.md`
- **API Reference**: `scripts/db_integration.py`
- **CLI Reference**: `satelit-db --help`

---

## ✅ Checklist de Implementación

Para implementar Smart Workflow en tu proyecto:

- [ ] Database corriendo y accesible
- [ ] Products registrados en BD
- [ ] Script `smart_workflow_planner.py` disponible
- [ ] Modificar `run_complete_workflow.py` para consultar BD primero
- [ ] Probar con AOI conocido
- [ ] Documentar decisiones en logs

---

**Versión:** 1.0
**Fecha:** 2025-01-21
**Autor:** goshawk_ETL Team + satelit_metadata integration

**¡Ahorra horas de procesamiento con Smart Workflow! ⚡**
