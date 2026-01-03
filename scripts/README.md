# Scripts de Procesamiento SAR+InSAR con Snapista

Scripts automatizados 100% Python para procesar imágenes Sentinel-1 y detectar fugas de agua usando **snapista** (wrapper Python para SNAP).

## 🎯 ¿Qué es Snapista?

**Snapista** es un wrapper Python para SNAP GPT que permite crear y ejecutar workflows de procesamiento de manera programática, sin necesidad de XML o bash scripts.

**Ventajas:**
- ✅ Código Python legible y mantenible
- ✅ No más XML complejos
- ✅ Mejor manejo de errores
- ✅ Logging detallado
- ✅ Fácil de debuggear y extender

---

## 📁 Estructura

```
scripts/
├── process_insar.py         # Procesamiento InSAR (SLC → coherencia) con snapista
├── process_sar.py           # Procesamiento SAR (GRD → backscatter + texturas) con snapista
├── calculate_statistics.py  # Cálculo de estadísticas temporales
└── README.md                # Este archivo

run_complete_workflow.py     # ⭐ Script maestro (ejecuta todo)
```

**Scripts obsoletos eliminados:**
- ❌ `process_insar.sh` → Reemplazado por `process_insar.py`
- ❌ `process_sar.sh` → Reemplazado por `process_sar.py`
- ❌ `run_pipeline.sh` → Reemplazado por `run_pipeline.py`
- ❌ `snap_graphs/*.xml` → Los workflows ahora se crean en Python

---

## 🚀 Uso Rápido

### 0. Activar entorno conda

```bash
conda activate satelit_download
```

### 1. Configurar parámetros (opcional)

Edita `config.txt` en el directorio raíz:

```bash
# Área de interés (WKT)
AOI="POLYGON((2.52 41.59, 2.58 41.59, 2.58 41.64, 2.52 41.64, 2.52 41.59))"

# Directorios
SLC_DIR="data/sentinel1_slc"
GRD_DIR="data/sentinel1_grd"
OUTPUT_DIR="processed"

# Método: "weighted" o "ml"
DETECTION_METHOD="weighted"
```

### 2. Ejecutar pipeline completo

```bash
python run_pipeline.py
```

**Este script ejecutará automáticamente:**
1. ✅ Procesamiento InSAR (2-3 horas)
2. ✅ Procesamiento SAR (1-2 horas)
3. ✅ Cálculo de estadísticas temporales (10 min)
4. ✅ Generación de mapas GeoTIFF para QGIS

**Resultado:** Estadísticas temporales (`coherence_mean.tif`, `vv_std.tif`, `entropy_mean.tif`) listas para abrir en QGIS

---

## 📖 Scripts Individuales

### Script 1: Procesamiento InSAR con Snapista

```bash
python scripts/process_insar.py
```

**Entrada:** Imágenes SLC en `data/sentinel1_slc/`

**Salida:**
- `processed/insar/Ifg_*.dim` - Interferogramas
- `processed/coherence_mean.tif` - Coherencia media temporal
- `processed/coherence_std.tif` - Coherencia desv. estándar

**Qué hace:**
1. Lee pares consecutivos de imágenes SLC
2. Crea gráfico de procesamiento InSAR usando snapista:
   - Apply Orbit File
   - TOPSAR Split
   - Back-Geocoding
   - Interferogram (con coherencia)
   - TOPSAR Deburst
   - TopoPhaseRemoval
   - Multilook
   - GoldsteinPhaseFiltering
   - Terrain Correction
   - Subset (AOI)
3. Exporta bandas de coherencia a GeoTIFF
4. Calcula estadísticas temporales

**Tiempo:** ~1-2 horas para 5 pares

**Ejemplo de código (simplificado):**
```python
from snapista import Graph, Operator

# Crear gráfico
g = Graph()

# Añadir operador de lectura
read = Operator('Read')
read.file = 'path/to/slc.SAFE'
g.add_node(read, node_id='read')

# Añadir operador de órbita
orbit = Operator('Apply-Orbit-File')
orbit.orbitType = 'Sentinel Precise (Auto Download)'
g.add_node(orbit, node_id='orbit', source='read')

# ... más operadores ...

# Ejecutar
g.run()
```

---

### Script 2: Procesamiento SAR con Snapista

```bash
python scripts/process_sar.py
```

**Entrada:** Imágenes GRD en `data/sentinel1_grd/`

**Salida:**
- `processed/vv_mean.tif`, `processed/vv_std.tif` - Backscatter VV
- `processed/vh_mean.tif`, `processed/vh_std.tif` - Backscatter VH
- `processed/entropy_mean.tif` - Textura entropy
- `processed/contrast_mean.tif` - Textura contrast
- `processed/homogeneity_mean.tif` - Textura homogeneity

**Qué hace:**
1. Lee imágenes GRD individuales
2. Crea gráfico de procesamiento SAR usando snapista:
   - Apply Orbit File
   - Remove GRD Border Noise
   - Calibration
   - Speckle Filter
   - Subset (AOI)
   - Terrain Correction
3. Crea gráfico GLCM para texturas:
   - GLCM (Gray Level Co-occurrence Matrix)
4. Exporta bandas a GeoTIFF
5. Calcula estadísticas temporales

**Tiempo:** ~1-2 horas para 10 imágenes

---

### Script 3: Cálculo de Estadísticas Temporales

```bash
python scripts/calculate_statistics.py
```

**Requisitos previos:**
- Productos InSAR procesados (coherencia)
- Productos SAR procesados (backscatter, texturas)

**Salida:**
- `coherence_mean.tif` - Coherencia media temporal
- `coherence_std.tif` - Desviación estándar de coherencia
- `vv_std.tif` - Variabilidad temporal de backscatter VV
- `entropy_mean.tif` - Entropía media (textura)
- Otros archivos de estadísticas

**Qué hace:**
1. Lee todos los interferogramas procesados
2. Calcula estadísticas temporales (media, desviación estándar)
3. Genera mapas GeoTIFF para cada estadística
4. Guarda resultados en directorio `fusion/`

**Tiempo:** ~5-10 minutos

---

## 🎨 Visualización en QGIS

### Cargar estadísticas temporales

```bash
# Coherencia media
qgis processing/*/insar_*/fusion/coherence_mean.tif

# Variabilidad VV
qgis processing/*/insar_*/fusion/vv_std.tif

# Entropía
qgis processing/*/insar_*/fusion/entropy_mean.tif
```

### Estilizar

1. **Layer Properties → Symbology**
2. **Render type:** Singleband pseudocolor
3. **Color ramp:**
   - 0.0-0.4: Verde (Bajo riesgo)
   - 0.4-0.6: Amarillo (Riesgo medio)
   - 0.6-0.8: Naranja (Riesgo alto)
   - 0.8-1.0: Rojo (Riesgo crítico)
4. **Transparency:** 50%

---

## 🔧 Solución de Problemas

### Error: "ModuleNotFoundError: No module named 'snapista'"

**Solución:**
```bash
# Verifica que estás en el entorno correcto
conda activate satelit_download

# Reinstalar snapista
conda install -c terradue snapista
```

### Error: "No se encontró config.txt"

**Solución:** Los scripts usan valores por defecto si no encuentra config.txt. Puedes crear uno:

```bash
cat > config.txt <<EOF
AOI="POLYGON((2.52 41.59, 2.58 41.59, 2.58 41.64, 2.52 41.64, 2.52 41.59))"
SLC_DIR="data/sentinel1_slc"
GRD_DIR="data/sentinel1_grd"
OUTPUT_DIR="processed"
DETECTION_METHOD="weighted"
THRESHOLD_HIGH="0.7"
THRESHOLD_MEDIUM="0.5"
EOF
```

### Error: Graph execution failed

**Solución:** Revisa el log detallado. Los mensajes de error de snapista son más claros que GPT:

```python
# El script muestra exactamente qué operador falló
# Ejemplo:
# ERROR: Operator 'TOPSAR-Split' failed
# Causa: Invalid subswath 'IW3' for this image
```

### ADVERTENCIA: "No hay suficientes imágenes"

**Solución:** Descarga más datos

```bash
# Mínimos recomendados:
# - 5 imágenes SLC (para 4-5 pares InSAR)
# - 10 imágenes GRD (para estadísticas temporales robustas)

python download_copernicus.py --interactive
```

---

## 📊 Interpretación de Resultados

### Mapa de Probabilidad

| Valor | Interpretación | Acción |
|-------|----------------|--------|
| **0.8-1.0** | Fuga muy probable | Investigación inmediata |
| **0.6-0.8** | Fuga probable | Verificar con histórico |
| **0.4-0.6** | Sospechoso | Monitorear evolución |
| **0.0-0.4** | Normal | Sin indicios |

---

## 🆕 Migración desde Bash/XML

### Comparación de Workflows

| Aspecto | Antiguo (Bash+XML) | Nuevo (Snapista) |
|---------|-------------------|------------------|
| **Lenguaje** | Bash + XML | Python puro |
| **Ejecución** | `./scripts/process_insar.sh` | `python scripts/process_insar.py` |
| **Workflows** | Archivos XML complejos | Código Python legible |
| **Debugging** | Logs crípticos de GPT | Exceptions Python claras |
| **Extensible** | Difícil (editar XML) | Fácil (añadir código Python) |
| **Mantenible** | Bajo | Alto |

### Ejemplo de Migración

**Antes (XML):**
```xml
<node id="Calibration">
  <operator>Calibration</operator>
  <sources>
    <sourceProduct refid="Read"/>
  </sources>
  <parameters class="com.bc.ceres.binding.dom.XppDomElement">
    <outputSigmaBand>true</outputSigmaBand>
    <selectedPolarisations>VV,VH</selectedPolarisations>
  </parameters>
</node>
```

**Ahora (Python con snapista):**
```python
calib = Operator('Calibration')
calib.outputSigmaBand = 'true'
calib.selectedPolarisations = 'VV,VH'
g.add_node(calib, node_id='calibration', source='read')
```

**¡Mucho más claro y fácil de mantener!** ✨

---

## 📚 Referencias

- **Snapista Documentación:** https://snap-contrib.github.io/snapista/
- **SNAP Documentation:** https://step.esa.int/main/doc/
- **Sentinel-1 User Guide:** https://sentinels.copernicus.eu/web/sentinel/user-guides/sentinel-1-sar

---

## 💡 Tips

1. **Logs detallados:** Los scripts Python muestran progreso en tiempo real
2. **Interruptible:** Puedes interrumpir con Ctrl+C, los archivos ya procesados se mantienen
3. **Reanudable:** Ejecuta de nuevo y salta archivos existentes automáticamente
4. **Paralelizable:** Modifica los scripts para procesar múltiples pares en paralelo
5. **Extensible:** Añade nuevos operadores de SNAP fácilmente con snapista

---

**¡Bienvenido al nuevo workflow con snapista!** 🛰️✨
