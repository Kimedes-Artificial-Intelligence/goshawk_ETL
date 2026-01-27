# Estructura de Directorios - goshawk_ETL

**Referencia de directorios y rutas para Smart Workflow**

---

## 📁 Estructura General

```
goshawk_ETL/                          # Repositorio principal
├── aoi/                              # Áreas de interés (GeoJSON)
│   ├── arenys_de_munt.geojson
│   ├── vilademuls.geojson
│   └── ...
│
├── data -> /mnt/satelit_data         # SYMLINK a almacenamiento externo
│   ├── sentinel1_slc/                # SLCs descargados (4-8 GB cada uno)
│   ├── processed_products/           # Repositorio de productos procesados
│   ├── preprocessed_slc/             # SLCs preprocesados por subswath
│   ├── sentinel2_l2a/                # Productos Sentinel-2
│   └── orbits/                       # Archivos de órbitas
│
├── processing/                       # Directorio de procesamiento temporal
│   ├── selected_products_desc_iw1.json
│   ├── insar_desc_iw1/
│   ├── polarimetry_desc_iw1/
│   └── ...
│
├── scripts/                          # Scripts Python
│   ├── smart_workflow_planner.py
│   ├── run_smart_workflow.py
│   └── ...
│
├── docs/                             # Documentación
└── logs/                             # Logs de ejecución
```

---

## 🔗 Symlink 'data'

El directorio `data/` es un **symlink** a `/mnt/satelit_data`:

```bash
$ ls -la data
lrwxrwxrwx 1 jmiro jmiro 17 dic 22 10:50 data -> /mnt/satelit_data
```

**Beneficios:**
- Almacenamiento en disco grande separado
- Compartido entre múltiples repositorios
- Fácil de respaldar/migrar

---

## 📂 Directorios Principales

### 1. `aoi/` - Áreas de Interés

Contiene archivos GeoJSON que definen las áreas de estudio:

```bash
aoi/
├── arenys_de_munt.geojson     # AOI de Arenys de Munt
├── vilademuls.geojson          # AOI de Vilademuls
└── ...
```

**Uso en Smart Workflow:**
```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  ...
```

---

### 2. `data/sentinel1_slc/` - SLCs Descargados

Productos SLC descargados de Copernicus (vía symlink):

```bash
data/sentinel1_slc/
├── S1A_IW_SLC__1SDV_20230111T060136_20230111T060203_046714_059C5B_F5B0.SAFE/
├── S1A_IW_SLC__1SDV_20230123T060136_20230123T060203_046889_059F92_1A2B.SAFE/
└── ...
```

**Tamaño:** ~4-8 GB por producto
**Total:** Puede ser cientos de GB

**Ruta por defecto en Smart Workflow:** `data/sentinel1_slc`

---

### 3. `data/processed_products/` - Repositorio de Productos

Productos InSAR y Polarimetría procesados organizados por track:

```bash
data/processed_products/
├── desc_iw1_t088/
│   ├── metadata.json
│   ├── insar_short/
│   │   ├── 20230111_20230123/
│   │   └── ...
│   ├── insar_long/
│   │   ├── 20230111_20230216/
│   │   └── ...
│   └── polarimetry/
│       ├── 20230111/
│       └── ...
└── desc_iw2_t088/
    └── ...
```

**Ruta por defecto en Smart Workflow:** `data/processed_products`

---

### 4. `processing/` - Directorio de Procesamiento

Directorio temporal donde se procesan los productos antes de añadirse al repositorio:

```bash
processing/
├── selected_products_desc_iw1.json    # Configuración de serie
├── selected_products_desc_iw2.json
├── processing_manifest_descending.json # Manifest global
│
├── insar_desc_iw1/                    # Procesamiento InSAR IW1
│   ├── insar_short/
│   ├── insar_long/
│   └── aoi_crop/                       # Recortado a AOI
│
├── insar_desc_iw2/                    # Procesamiento InSAR IW2
│   └── ...
│
├── polarimetry_desc_iw1/              # Procesamiento Polarimetría IW1
│   └── aoi_crop/
│
└── polarimetry_desc_iw2/
    └── ...
```

**Ruta por defecto en Smart Workflow:** `processing/`

**Nota:** Este directorio puede eliminarse después de añadir productos al repositorio.

---

### 5. `logs/` - Logs de Ejecución

Logs de todos los scripts:

```bash
logs/
├── smart_workflow_20250121_113045.log
├── download_copernicus_20250121_100530.log
└── ...
```

---

## 🛠️ Rutas en Smart Workflow

### Rutas Relativas (Recomendado)

Por defecto, el Smart Workflow usa **rutas relativas** al repositorio:

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \        # Relativa
  --slc-dir data/sentinel1_slc \             # Relativa (resuelve symlink)
  --processing-dir processing \              # Relativa
  --repo-dir data/processed_products         # Relativa (resuelve symlink)
```

**Ventajas:**
- Funciona desde cualquier ubicación si ejecutas desde repo root
- Portable entre diferentes instalaciones
- Más legible

---

### Rutas Absolutas (Alternativa)

También puedes usar rutas absolutas:

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson /home/jmiro/Github/goshawk_ETL/aoi/mi_aoi.geojson \
  --slc-dir /mnt/satelit_data/sentinel1_slc \
  --processing-dir /home/jmiro/Github/goshawk_ETL/processing \
  --repo-dir /mnt/satelit_data/processed_products
```

---

### Resolución Automática de Rutas

El Smart Workflow **automáticamente**:

1. Convierte rutas relativas a absolutas
2. Resuelve symlinks si existen
3. Verifica que los directorios existen

**Ejemplo:**
```python
# Input: "data/sentinel1_slc"
# Paso 1: repo_root / "data/sentinel1_slc"
#         → /home/jmiro/Github/goshawk_ETL/data/sentinel1_slc
# Paso 2: Detecta symlink en 'data'
# Paso 3: Resuelve a → /mnt/satelit_data/sentinel1_slc
```

---

## 📊 Espacio en Disco

### Estimaciones por Directorio

| Directorio | Tamaño Típico | Notas |
|------------|---------------|-------|
| `aoi/` | < 1 MB | Archivos GeoJSON pequeños |
| `data/sentinel1_slc/` | 100-500 GB | 4-8 GB por SLC × muchos productos |
| `data/processed_products/` | 50-200 GB | Productos procesados comprimidos |
| `processing/` | 20-100 GB | Temporal, se puede limpiar |
| `logs/` | < 100 MB | Logs de texto |

**Total estimado:** 200-800 GB (principalmente en `/mnt/satelit_data`)

---

## 🔧 Comandos Útiles

### Ver uso de disco

```bash
# Tamaño total de data (symlink resuelto)
du -sh /mnt/satelit_data

# Por subdirectorio
du -sh /mnt/satelit_data/*

# Número de SLCs
ls /mnt/satelit_data/sentinel1_slc | wc -l

# Tamaño de processing
du -sh processing/
```

### Verificar symlink

```bash
# Ver symlink
ls -la data

# Ver contenido
ls data/

# Verificar target existe
test -d /mnt/satelit_data && echo "OK" || echo "ERROR"
```

### Limpiar processing

```bash
# Después de añadir productos al repositorio
rm -rf processing/insar_*
rm -rf processing/polarimetry_*
```

---

## ⚠️ Importante

### Ejecutar desde repo root

**SIEMPRE** ejecuta los scripts desde el directorio raíz de `goshawk_ETL`:

```bash
# ✓ CORRECTO
cd /home/jmiro/Github/goshawk_ETL
python scripts/run_smart_workflow.py --aoi-geojson aoi/mi_aoi.geojson ...

# ✗ INCORRECTO
cd /home/jmiro/Github/goshawk_ETL/scripts
python run_smart_workflow.py --aoi-geojson ../aoi/mi_aoi.geojson ...
```

### Symlink 'data'

No elimines ni modifiques el symlink `data` sin antes:
1. Verificar que `/mnt/satelit_data` tiene espacio suficiente
2. Actualizar rutas en scripts si cambias el target

---

## 🔗 Referencias

- **Smart Workflow**: `docs/SMART_WORKFLOW_USAGE.md`
- **Quick Start**: `QUICKSTART_SMART_WORKFLOW.md`
- **Integración**: `INTEGRATION_SUMMARY.md`

---

**Versión**: 1.0
**Fecha**: 2025-01-21
