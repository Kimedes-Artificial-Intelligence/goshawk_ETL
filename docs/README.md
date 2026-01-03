# 🦅 Goshawk ETL

Pipeline automatizado para procesamiento InSAR multi-temporal con Sentinel-1

## 🚀 Quick Start (3 comandos)

```bash
# 1. Setup automático (5-10 min)
bash setup.sh

# 2. Activar environment
conda activate goshawk_etl

# 3. Ejecutar workflow
python run_complete_workflow.py
```

## 📋 Requisitos Previos

- **Sistema Operativo**: Linux o macOS
- **Conda/Mamba**: [Miniconda](https://docs.conda.io/en/latest/miniconda.html) o [Miniforge](https://github.com/conda-forge/miniforge)
- **Espacio en disco**: Mínimo 50GB (recomendado 200GB+)
- **RAM**: Mínimo 8GB (recomendado 16GB+)
- **Cuenta Copernicus**: Gratis en [dataspace.copernicus.eu](https://dataspace.copernicus.eu/)

## 📦 Instalación

### Opción A: Setup Automático (Recomendado)

```bash
# Clona el repositorio
git clone https://github.com/tu-usuario/goshawk_ETL.git
cd goshawk_ETL

# Ejecuta setup
bash setup.sh

# Sigue las instrucciones en pantalla
```

El script automáticamente:
- ✅ Detecta tu sistema operativo
- ✅ Crea el environment conda
- ✅ Instala todas las dependencias
- ✅ **Instala ESA SNAP 13.0.0** (si no está instalado)
- ✅ Configura la interfaz Python (esa_snappy)
- ✅ Crea estructura de directorios
- ✅ Configura credenciales (interactivo)

### Opción B: Setup Manual

```bash
# 1. Crear environment
conda env create -f environment.yml

# 2. Activar
conda activate goshawk_etl

# 3. Crear directorios
mkdir -p data/{sentinel1_slc,sentinel1_grd,sentinel2_l2a,orbits}
mkdir -p processing logs aoi

# 4. Configurar credenciales
cp .env.example .env
nano .env  # Editar con tus credenciales
```

## 🎯 Uso

### Modo Interactivo (Recomendado)

```bash
python run_complete_workflow.py
```

El workflow te guiará paso a paso:
1. Selecciona un AOI (área de interés)
2. Define rango de fechas
3. Configura parámetros
4. Procesamiento automático

### Modo Batch (Múltiples AOIs)

```bash
# Procesar todos los AOIs del archivo
python run_batch_aoi_workflow.py
```

### Usando Makefile

```bash
# Ver comandos disponibles
make help

# Setup completo
make setup

# Ejecutar workflow
make workflow

# Ver estado del proyecto
make status

# Limpiar temporales
make clean
```

## 📂 Estructura del Proyecto

```
goshawk_ETL/
├── setup.sh              # Setup automático
├── Makefile              # Comandos útiles
├── environment.yml       # Dependencias conda
├── .env.example          # Plantilla configuración
│
├── aoi/                  # Áreas de interés (GeoJSON)
│   └── mi_aoi.geojson
│
├── data/                 # Datos descargados
│   ├── sentinel1_slc/
│   ├── sentinel1_grd/
│   ├── sentinel2_l2a/
│   └── orbits/
│
├── processing/           # Resultados por proyecto
│   └── mi_proyecto/
│       ├── insar_desc_iw1/
│       ├── insar_asce_iw1/
│       └── urban_products/
│
├── scripts/              # Scripts de procesamiento
├── docs/                 # Documentación detallada
└── logs/                 # Logs de ejecución
```

## 📖 Documentación

- **[QUICK_START.md](docs/QUICK_START.md)** - Inicio rápido paso a paso
- **[INSTALLATION.md](docs/INSTALLATION.md)** - Instalación detallada
- **[SNAP_INSTALLATION.md](SNAP_INSTALLATION.md)** - Guía de instalación SNAP 13.0.0
- **[WORKFLOW.md](docs/WORKFLOW.md)** - Descripción del pipeline
- **[PREPROCESSING_GUIDE.md](docs/PREPROCESSING_GUIDE.md)** - Guía de preprocesamiento

## 🔧 Configuración

### Credenciales Copernicus

Edita `.env`:

```bash
CDSE_USERNAME=tu_usuario
CDSE_PASSWORD=tu_password
```

### Parámetros Avanzados

Ver `.env.example` para opciones adicionales:
- Número de workers paralelos
- Memoria para SNAP
- Directorios personalizados

## 🎛️ Comandos Make Útiles

```bash
make help              # Muestra ayuda
make setup             # Setup completo
make workflow          # Ejecuta workflow
make status            # Estado del proyecto
make clean             # Limpia temporales
make check-deps        # Verifica dependencias
make docs              # Lista documentación
```

## 🐛 Troubleshooting

### Error: conda no encontrado
```bash
# Instala Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
```

### Error: SNAP GPT no encontrado
```bash
# Ejecuta el instalador de SNAP
conda activate goshawk_etl
bash scripts/install_snap.sh

# O agrega SNAP al PATH
export PATH="/opt/esa-snap/bin:$PATH"
```

### Error de memoria
```bash
# Edita .env
SNAP_MAX_MEMORY=16  # Aumentar según RAM disponible
```

### Más ayuda
Ver [docs/INSTALLATION.md](docs/INSTALLATION.md)

## 🤝 Contribuir

1. Fork el proyecto
2. Crea una rama (`git checkout -b feature/nueva-funcionalidad`)
3. Commit cambios (`git commit -am 'Añade nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Abre un Pull Request

## 📄 Licencia

Ver [LICENSE](../LICENSE)

## 🙏 Agradecimientos

- **ESA Copernicus**: Datos Sentinel-1/2
- **ESA SNAP**: Software de procesamiento SAR (versión 13.0.0)
- **esa_snappy**: Interfaz Python oficial para SNAP

---

**Versión basada en**: satelit_download (limpieza y mejoras)
