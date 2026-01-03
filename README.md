# 🦅 Goshawk ETL Pipeline

Pipeline ETL automatizado para procesamiento de datos satelitales Sentinel-1 con enfoque en detección de cambios y análisis InSAR.

## 📋 Descripción

Goshawk ETL es un pipeline robusto para descarga, procesamiento y análisis de datos Sentinel-1 (SAR) usando ESA SNAP. El sistema implementa un flujo completo desde la descarga de productos satelitales hasta la generación de productos InSAR y polarimétricos, con gestión inteligente de descargas y repositorio compartido de productos procesados.

### ✨ Características Principales

- **🎯 Descarga Inteligente**: Modo por defecto que analiza el repositorio y descarga solo los SLCs necesarios para completar procesamiento (2 pares cortos + 2 largos por fecha)
- **🗄️ Repositorio Compartido**: Sistema de gestión de productos InSAR y polarimétricos procesados organizado por órbita, subswath y track
- **🔄 Procesamiento por Lotes**: Workflows automatizados para múltiples AOIs
- **📊 Análisis Multi-temporal**: Generación de pares interferométricos cortos (6 días) y largos (12 días)
- **🎨 Productos Polarimétricos**: Descomposición H-Alpha Dual Pol
- **🧹 Gestión Automática de Espacio**: Cleanup inteligente de SLCs tras procesamiento
- **📈 Series Temporales**: Análisis de coherencia, backscatter y entropía

## 🚀 Inicio Rápido

### Requisitos

- **Sistema Operativo**: Linux (probado en Ubuntu 20.04+)
- **Python**: 3.8+
- **ESA SNAP**: 9.0+ con GPT configurado
- **Memoria**: Mínimo 16GB RAM (recomendado 32GB)
- **Almacenamiento**: 
  - SSD para procesamiento temporal (~50GB)
  - HDD/NAS para datos crudos y repositorio (>500GB)

### Instalación

1. **Clonar repositorio**:
```bash
git clone https://github.com/Kimedes-Artificial-Intelligence/goshawk_ETL.git
cd goshawk_ETL
```

2. **Configurar entorno conda**:
```bash
conda env create -f environment.yml
conda activate goshawk
```

3. **Configurar credenciales**:
```bash
cp .env.example .env
# Editar .env con tus credenciales de Copernicus Dataspace
```

4. **Configurar rutas** (opcional):
```bash
# Si usas un NAS o disco externo para datos
ln -s /mnt/satelit_data data
```

### Uso Básico

#### 1. Descargar productos Sentinel-1

```bash
# Modo inteligente (default) - descarga solo lo necesario
python3 scripts/download_copernicus.py \
  --collection SENTINEL-1 \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --orbit-direction DESCENDING \
  --start-date 2025-07-01 \
  --end-date 2025-12-31 \
  --yes
```

#### 2. Seleccionar serie de productos

```bash
python3 scripts/select_multiswath_series.py \
  --data-dir data/sentinel1_slc \
  --aoi-geojson aoi/arenys_de_munt.geojson \
  --output-dir processing/arenys \
  --orbit-direction DESCENDING
```

#### 3. Procesar serie InSAR

```bash
python3 scripts/process_insar_series.py \
  --input-file processing/arenys/selected_products_desc_iw1.json \
  --workspace processing/arenys/insar_desc_iw1 \
  --max-workers 4
```

#### 4. Workflow completo

```bash
./run_complete_workflow.py \
  --aoi-name arenys_de_munt \
  --orbit-direction DESCENDING \
  --start-date 2025-07-01 \
  --end-date 2025-12-31 \
  --auto-confirm
```

## 📂 Estructura del Proyecto

```
goshawk_ETL/
├── scripts/                      # Scripts principales
│   ├── download_copernicus.py    # Descarga inteligente de Copernicus
│   ├── download_orbits.py        # Descarga de archivos de órbita
│   ├── select_multiswath_series.py  # Selección de series por subswath
│   ├── process_insar_series.py   # Procesamiento InSAR
│   ├── insar_repository.py       # Gestión del repositorio
│   └── ...
├── aoi/                          # Archivos GeoJSON de áreas de interés
├── data/                         # Datos satelitales (enlace simbólico)
│   ├── sentinel1_slc/           # Productos SLC descargados
│   ├── sentinel1_grd/           # Productos GRD
│   └── processed_products/      # Repositorio de productos procesados
│       ├── desc_iw1/           # DESCENDING IW1
│       │   ├── t110/           # Track 110
│       │   │   ├── metadata.json
│       │   │   ├── insar/      # Productos InSAR
│       │   │   └── polarimetry/ # Productos polarimétricos
│       │   └── ...
│       └── ...
├── processing/                   # Workspaces de procesamiento
├── logs/                        # Logs de ejecución
├── docs/                        # Documentación
├── environment.yml              # Entorno conda
├── run_complete_workflow.py    # Workflow completo
├── run_batch_aoi_workflow.py   # Procesamiento por lotes
└── README.md
```

## 🎯 Modo Inteligente (Default)

El script de descarga implementa un **modo inteligente por defecto** que:

1. **Consulta Copernicus**: Obtiene lista de productos disponibles
2. **Analiza Repositorio**: Verifica qué productos InSAR ya existen
3. **Detecta Gaps**:
   - Fechas sin procesar
   - Fechas con pares incompletos (<2 cortos o <2 largos)
4. **Calcula Necesidades**: Determina qué SLCs descargar para completar procesamiento
5. **Descarga Mínimo**: Solo descarga lo estrictamente necesario

### Ventajas

✅ **Resuelve SLCs borrados**: Detecta necesidad aunque el SLC no exista localmente  
✅ **Optimiza descargas**: Solo descarga lo estrictamente necesario  
✅ **Garantiza completitud**: 2 pares cortos + 2 largos por fecha  
✅ **Multi-track**: Analiza todos los tracks del repositorio  
✅ **Transparente**: Muestra exactamente qué falta y por qué  
✅ **Por defecto**: No requiere flags especiales  

## 🗄️ Repositorio de Productos

Los productos procesados se organizan en un repositorio compartido:

```
data/processed_products/
├── desc_iw1/t110/          # DESCENDING IW1 Track 110
│   ├── metadata.json       # Metadata del track
│   ├── insar/
│   │   ├── short/         # Pares contiguos (6 días)
│   │   └── long/          # Pares saltados (12 días)
│   └── polarimetry/       # Por fecha SLC
│       ├── 20251102/
│       └── ...
```

### Comandos del Repositorio

```bash
# Listar contenido del repositorio
python scripts/insar_repository.py --list

# Verificar cobertura de AOI
python scripts/insar_repository.py \
  --check-coverage "POLYGON(...)" \
  --orbit DESCENDING \
  --subswath IW1

# Añadir productos al repositorio
python scripts/insar_repository.py \
  --add-products processing/arenys/insar_desc_iw1 \
  --orbit DESCENDING \
  --subswath IW1 \
  --track 110
```

## 🔧 Scripts Principales

### download_copernicus.py

Descarga productos de Copernicus Dataspace con modo inteligente.

**Opciones clave**:
- `--orbit-direction`: ASCENDING o DESCENDING (requerido para modo inteligente)
- `--no-smart`: Desactiva modo inteligente
- `--skip-processed`: Omite productos ya procesados
- `--min-coverage`: % mínimo de cobertura del AOI (default: 10%)
- `--satellites`: Filtrar por satélite (S1A, S1B, S1C)

### select_multiswath_series.py

Selecciona productos óptimos para cada subswath.

**Características**:
- Análisis de cobertura por subswath
- Selección del mejor producto por fecha
- Generación de manifest para procesamiento
- Soporte para múltiples series simultáneas

### process_insar_series.py

Procesa serie InSAR con GPT de SNAP.

**Pipeline**:
1. Apply Orbit File
2. Back-Geocoding
3. Enhanced Spectral Diversity (ESD)
4. Interferogram generation
5. TOPSAR Deburst
6. Terrain Correction
7. Band math y export

### insar_repository.py

Gestiona repositorio de productos procesados.

**Funciones**:
- Organización por órbita/subswath/track
- Metadata automático con estadísticas
- Verificación de cobertura espacial
- Gestión de productos InSAR y polarimétricos

## 📊 Productos Generados

### Productos InSAR
- **Pares cortos** (6 días): Alta coherencia, cambios rápidos
- **Pares largos** (12 días): Menor coherencia, análisis temporal
- **Bandas**: Phase, Coherence, Intensity (VV, VH)

### Productos Polarimétricos
- **Descomposición H-Alpha Dual Pol**
- **Bandas**: Entropy, Anisotropy, Alpha angle

### Estadísticas Temporales
- Coherencia media/std
- Backscatter VV/VH media/std
- Entropía media/std
- Análisis por pares temporales

## 🛠️ Configuración Avanzada

### Variables de Entorno (.env)

```bash
# Credenciales Copernicus
COPERNICUS_USER=tu_usuario
COPERNICUS_PASSWORD=tu_password

# Rutas personalizadas (opcional)
DATA_DIR=/mnt/satelit_data
SNAP_GPT=/usr/local/snap/bin/gpt
```

### Archivos de Órbita

Los archivos de órbita precisos (POEORB) son necesarios para procesamiento InSAR:

```bash
python3 scripts/download_orbits.py \
  --start-date 2025-07-01 \
  --end-date 2025-12-31 \
  --satellites S1A S1C
```

## 📝 Casos de Uso

### 1. Procesamiento de Nueva Área

```bash
# 1. Descargar productos
python3 scripts/download_copernicus.py \
  --aoi-geojson aoi/nueva_area.geojson \
  --orbit-direction DESCENDING \
  --yes

# 2. Seleccionar series
python3 scripts/select_multiswath_series.py \
  --data-dir data/sentinel1_slc \
  --aoi-geojson aoi/nueva_area.geojson \
  --output-dir processing/nueva_area \
  --orbit-direction DESCENDING

# 3. Procesar
python3 scripts/process_insar_series.py \
  --input-file processing/nueva_area/selected_products_desc_iw1.json \
  --workspace processing/nueva_area/insar_desc_iw1
```

### 2. Actualizar Serie Existente

```bash
# El modo inteligente detecta automáticamente qué falta
python3 scripts/download_copernicus.py \
  --aoi-geojson aoi/area_existente.geojson \
  --orbit-direction DESCENDING \
  --yes
# Solo descarga lo necesario para completar pares
```

### 3. Procesamiento por Lotes

```bash
./run_batch_aoi_workflow.py \
  --aoi-list aoi_list.txt \
  --orbit-direction DESCENDING \
  --auto-confirm
```

## 🐛 Troubleshooting

### Problema: "No orbit files found"

**Solución**: Descargar archivos de órbita para el período
```bash
python3 scripts/download_orbits.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

### Problema: "Out of memory"

**Soluciones**:
1. Reducir `--max-workers`
2. Aumentar memoria disponible
3. Procesar subswaths por separado

### Problema: "No products match orbit direction"

**Solución**: Verificar que los SLCs descargados son de la órbita correcta
```bash
python3 scripts/select_multiswath_series.py --orbit-direction ASCENDING  # o DESCENDING
```

## 🤝 Contribuir

Las contribuciones son bienvenidas. Por favor:

1. Fork del repositorio
2. Crear rama feature (`git checkout -b feature/amazing-feature`)
3. Commit cambios (`git commit -m 'Add amazing feature'`)
4. Push a la rama (`git push origin feature/amazing-feature`)
5. Abrir Pull Request

## 📄 Licencia

Este proyecto está bajo licencia MIT. Ver archivo [LICENSE](LICENSE) para más detalles.

## 🙏 Agradecimientos

- **ESA SNAP**: Toolbox de procesamiento SAR
- **Copernicus Dataspace**: Acceso a datos Sentinel
- **Shapely**: Operaciones geométricas
- **Comunidad Open Source**: Por las herramientas y librerías utilizadas

## 📧 Contacto

- **Organización**: [Kimedes Artificial Intelligence](https://github.com/Kimedes-Artificial-Intelligence)
- **Issues**: [GitHub Issues](https://github.com/Kimedes-Artificial-Intelligence/goshawk_ETL/issues)

---

**Nota**: Este proyecto está en desarrollo activo. Las APIs y estructuras pueden cambiar entre versiones.
