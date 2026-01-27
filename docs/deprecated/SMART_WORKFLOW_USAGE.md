# Guía de Uso - Smart Workflow

**Workflow inteligente optimizado con base de datos**

---

## 🎯 Introducción

El **Smart Workflow** es una evolución del workflow tradicional que consulta la base de datos antes de procesar para evitar trabajo innecesario.

### Comparación

| Aspecto | Workflow Tradicional | Smart Workflow |
|---------|---------------------|----------------|
| **Consulta BD** | ❌ No | ✅ Sí |
| **Decisión** | Siempre procesa todo | Decide qué es necesario |
| **Tiempo (todo procesado)** | 6-8 horas | 10-15 minutos |
| **Tiempo (SLCs descargados)** | 6-8 horas | 2-3 horas |
| **Reutilización entre proyectos** | ❌ No | ✅ Sí |

---

## 🚀 Quick Start

### Opción 1: Consulta Previa (Recomendado)

Antes de ejecutar el workflow, consulta qué se necesita:

```bash
# Ver plan SIN ejecutar
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
   Actions:
      ✂️  CROP to AOI only (FAST!)

📊 Track: desc_iw2_t088
   Decision: ⚡ SLCs already downloaded (42) - SKIP DOWNLOAD, PROCESS ONLY
   Actions:
      ⚙️  PROCESS InSAR + Polarimetry

SUMMARY:
  ✂️  Crop only (fastest):     1 tracks
  ⚡ Process only (no download): 1 tracks
  🔄 Full workflow:             0 tracks
```

### Opción 2: Ejecutar Workflow Completo

Ejecuta el workflow optimizado con confirmación:

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

El script:
1. Consulta la BD automáticamente
2. Muestra el plan de ejecución
3. Pide confirmación
4. Ejecuta solo las etapas necesarias

### Opción 3: Dry Run (Ver sin ejecutar)

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2 \
  --dry-run
```

---

## 📋 Parámetros

### Obligatorios

- `--aoi-geojson PATH`: Archivo GeoJSON del área de interés
- `--start-date YYYY-MM-DD`: Fecha de inicio
- `--end-date YYYY-MM-DD`: Fecha de fin

### Opcionales

- `--orbit {ASCENDING,DESCENDING}`: Dirección de órbita (default: DESCENDING)
- `--subswaths IW1 IW2 ...`: Subswaths a procesar (default: IW1 IW2)
- `--slc-dir PATH`: Directorio de SLCs (default: /mnt/satelit_data/sentinel1_slc)
- `--processing-dir PATH`: Directorio de procesamiento (default: processing)
- `--repo-dir PATH`: Directorio del repositorio (default: data/processed_products)
- `--log-dir PATH`: Directorio de logs (default: logs)
- `--dry-run`: Mostrar plan sin ejecutar
- `--force-full`: Forzar workflow completo (ignorar BD)

---

## 🎓 Casos de Uso

### Caso 1: Primera vez - Nuevo AOI

**Escenario:** Quieres analizar Arenys de Munt por primera vez

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

**Resultado:**
- BD detecta que no hay productos procesados
- Ejecuta workflow completo: DOWNLOAD → PROCESS → CROP
- Tiempo: ~6-8 horas
- Todos los productos quedan registrados en BD

---

### Caso 2: Mismo AOI, Ampliar Fechas

**Escenario:** Ya procesaste Enero-Junio, ahora quieres Julio-Diciembre

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --start-date 2023-07-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

**Resultado:**
- BD detecta que Enero-Junio ya están procesados
- Solo procesa Julio-Diciembre (productos nuevos)
- Tiempo: ~3-4 horas (solo nuevos productos)

---

### Caso 3: Nuevo AOI, Mismo Track

**Escenario:** Ya procesaste Arenys, ahora quieres Vilademuls (mismo track 88)

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/vilademuls.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2
```

**Resultado:**
- BD detecta que track 88 ya está completamente procesado
- Solo ejecuta CROP a nuevo AOI
- Tiempo: ~10-15 minutos ⚡
- **Ahorro: 99% de tiempo!**

---

### Caso 4: Re-análisis con Mejores Parámetros

**Escenario:** Quieres reprocesar con filtros diferentes

```bash
# Opción 1: Forzar reprocesamiento
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2 \
  --force-full
```

**Resultado:**
- Ignora BD, procesa todo desde cero
- Útil para cambios de algoritmos o parámetros

---

### Caso 5: Consulta Previa sin Ejecutar

**Escenario:** Solo quieres ver qué se necesita hacer

```bash
# Opción A: Usar planner directamente
python scripts/smart_workflow_planner.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2

# Opción B: Usar workflow con dry-run
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --orbit DESCENDING \
  --subswaths IW1 IW2 \
  --dry-run
```

**Resultado:**
- Muestra plan de ejecución
- No ejecuta nada
- Útil para planificación y estimación de tiempos

---

## 🔍 Lógica de Decisión

El Smart Workflow consulta la BD y decide automáticamente:

### Decisión 1: CROP ONLY ✂️ (FAST PATH)

**Condiciones:**
- ✅ SLCs descargados y procesados
- ✅ InSAR short pairs completos
- ✅ InSAR long pairs completos
- ✅ Polarimetría procesada

**Acción:**
```bash
# Solo ejecuta crop a AOI (~5-15 minutos)
python scripts/crop_insar_to_aoi.py
python scripts/crop_polarimetry_to_aoi.py
```

**Tiempo ahorrado:** ~6-8 horas (99% más rápido!)

---

### Decisión 2: PROCESS ONLY ⚡ (MEDIUM PATH)

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

**Tiempo ahorrado:** ~1-2 horas (20-30% más rápido)

---

### Decisión 3: FULL WORKFLOW 🔄

**Condiciones:**
- ❌ Faltan SLCs
- ❌ Faltan productos procesados

**Acción:**
```bash
# Workflow completo
download → process → crop
```

**Beneficio:** Completa lo que falta sin duplicar lo existente

---

## 📊 Métricas de Rendimiento

| Escenario | Tradicional | Smart | Ahorro |
|-----------|-------------|-------|--------|
| **Todo procesado (nuevo AOI)** | 6-8 h | 10-15 min | 99% |
| **SLCs descargados** | 6-8 h | 2-3 h | 60% |
| **Productos parciales** | 6-8 h | 3-4 h | 50% |
| **Track vacío** | 6-8 h | 6-8 h | 0% |

---

## 🔧 Troubleshooting

### Error: "Database integration: DISABLED"

**Causa:** Base de datos no disponible

**Solución:**
```bash
# 1. Verificar BD corriendo
cd ../satelit_metadata
docker compose ps

# 2. Si no está corriendo, iniciar
make db-up

# 3. Verificar integración
cd ../goshawk_ETL
python scripts/db_example_usage.py
```

---

### Smart Workflow no detecta productos existentes

**Causa:** Productos no registrados en BD

**Solución:**
```bash
# Migrar datos existentes
cd ../satelit_metadata
python scripts/migrate_json_to_db.py --data-root /mnt/satelit_data
```

---

### "Track empty in database" pero productos existen localmente

**Causa:** Productos descargados antes de la integración con BD

**Solución:**
```bash
# Opción 1: Migrar datos existentes
cd ../satelit_metadata
python scripts/migrate_json_to_db.py --data-root /mnt/satelit_data

# Opción 2: Forzar workflow completo (reprocesa pero registra en BD)
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --force-full
```

---

## 📚 Archivos Generados

### Durante Planificación

- `logs/smart_workflow_YYYYMMDD_HHMMSS.log`: Log de ejecución

### Durante Ejecución

```
processing/
├── selected_products_desc_iw1.json     # Configuración serie IW1
├── selected_products_desc_iw2.json     # Configuración serie IW2
├── processing_manifest_descending.json # Manifest global
├── insar_desc_iw1/                     # Productos InSAR IW1
│   ├── insar_short/                    # Pares cortos
│   ├── insar_long/                     # Pares largos
│   └── aoi_crop/                       # Recortado a AOI
├── insar_desc_iw2/                     # Productos InSAR IW2
│   └── ...
├── polarimetry_desc_iw1/               # Productos polarimetría IW1
│   └── aoi_crop/
└── polarimetry_desc_iw2/               # Productos polarimetría IW2
    └── aoi_crop/
```

---

## ✅ Checklist de Uso

Antes de ejecutar el Smart Workflow:

- [ ] Base de datos corriendo (`cd ../satelit_metadata && docker compose ps`)
- [ ] Integración verificada (`python scripts/db_example_usage.py`)
- [ ] AOI GeoJSON existe
- [ ] Fechas en formato correcto (YYYY-MM-DD)
- [ ] Suficiente espacio en disco
- [ ] Credenciales Copernicus configuradas (si necesitas descargar)

---

## 🔗 Documentación Relacionada

- **Concepto Smart Workflow**: `docs/SMART_WORKFLOW.md`
- **Integración BD**: `docs/DB_INTEGRATION.md`
- **API Reference**: `scripts/smart_workflow_planner.py`
- **Repositorio BD**: `../satelit_metadata/README.md`

---

## 💡 Mejores Prácticas

1. **Consulta primero**: Usa `--dry-run` o `smart_workflow_planner.py` antes de ejecutar
2. **Mantén BD actualizada**: Asegúrate que nuevos productos se registren
3. **Usa BD para cleanup**: `satelit-db deletable-slcs` antes de borrar SLCs
4. **Reutiliza tracks**: Si varios AOIs usan el mismo track, procesa una vez, crop varias veces
5. **Monitorea logs**: Revisa `logs/` para diagnóstico

---

**Versión:** 2.0
**Fecha:** 2025-01-21
**Autor:** goshawk_ETL Team + satelit_metadata integration

**¡Ahorra horas de procesamiento con Smart Workflow! ⚡**
