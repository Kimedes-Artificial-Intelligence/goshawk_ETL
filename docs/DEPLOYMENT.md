# 🚀 Guía de Deployment Rápido

## Para un nuevo servidor (5 minutos)

### 1. Clonar repositorio
```bash
git clone https://github.com/tu-usuario/goshawk_ETL.git
cd goshawk_ETL
```

### 2. Setup automático
```bash
bash setup.sh
```

El script:
- ✅ Detecta OS (Linux/macOS)
- ✅ Verifica conda/mamba
- ✅ Crea environment `goshawk_etl`
- ✅ Instala dependencias (Python 3.9 + SNAP + librerías)
- ✅ Crea estructura de directorios
- ✅ Configura credenciales (interactivo)

### 3. Verificar instalación
```bash
# Opción A: Verificación completa
python check_system.py

# Opción B: Test rápido
bash test.sh
```

### 4. Ejecutar workflow
```bash
conda activate goshawk_etl
python run_complete_workflow.py
```

---

## Comandos útiles (Makefile)

```bash
make help              # Ver todos los comandos
make setup             # Setup completo
make status            # Ver estado del proyecto
make workflow          # Ejecutar workflow interactivo
make clean             # Limpiar temporales
make check-deps        # Verificar dependencias
```

---

## Troubleshooting rápido

### ❌ Error: "conda: command not found"
```bash
# Instalar Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
source ~/.bashrc
```

### ❌ Error: "Environment already exists"
```bash
# Opción 1: Usar existente
conda activate goshawk_etl

# Opción 2: Recrear
conda env remove -n goshawk_etl -y
bash setup.sh
```

### ❌ Error: "SNAP GPT not found"
```bash
conda activate goshawk_etl
pip install --upgrade snapista
# SNAP se instala automáticamente con snapista
```

### ❌ Error: Out of memory
```bash
# Editar .env
nano .env
# Aumentar: SNAP_MAX_MEMORY=16
```

### ❌ Error: "No space left on device"
```bash
# Verificar espacio
df -h .

# Limpiar datos antiguos
make clean-data  # ⚠️ Elimina descargas
make clean-processing  # ⚠️ Elimina procesamientos
```

---

## Deployment en diferentes entornos

### 🖥️ Servidor Linux (recomendado)
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install wget git -y

# Instalar Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b
~/miniconda3/bin/conda init
source ~/.bashrc

# Clonar y setup
git clone <repo>
cd goshawk_ETL
bash setup.sh
```

### 🍎 macOS
```bash
# Instalar Homebrew si no existe
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Instalar dependencias base
brew install wget git

# Instalar Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh
bash Miniconda3-latest-MacOSX-x86_64.sh -b
~/miniconda3/bin/conda init
source ~/.zshrc

# Clonar y setup
git clone <repo>
cd goshawk_ETL
bash setup.sh
```

### 🐳 Docker (futuro)
```bash
# TODO: Crear Dockerfile
# docker build -t goshawk_etl .
# docker run -v $(pwd)/data:/data goshawk_etl
```

---

## Configuración avanzada

### Múltiples usuarios en mismo servidor
```bash
# Cada usuario:
git clone <repo>
cd goshawk_ETL

# Usar environment compartido (opcional)
conda activate /shared/envs/goshawk_etl

# O crear propio
bash setup.sh
```

### Cluster HPC (Slurm)
```bash
# Módulos típicos
module load anaconda3
module load gdal

# Setup
git clone <repo>
cd goshawk_ETL
bash setup.sh

# Job script
cat > job.slurm << 'EOF'
#!/bin/bash
#SBATCH --job-name=goshawk
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=48:00:00

source ~/.bashrc
conda activate goshawk_etl

cd $SLURM_SUBMIT_DIR
python run_complete_workflow.py --aoi aoi/mi_aoi.geojson
EOF

sbatch job.slurm
```

---

## Checklist de deployment exitoso

- [ ] Sistema operativo compatible (Linux/macOS)
- [ ] Conda/Mamba instalado
- [ ] Git instalado
- [ ] Espacio en disco ≥50GB (recomendado 200GB+)
- [ ] RAM ≥8GB (recomendado 16GB+)
- [ ] Environment `goshawk_etl` creado
- [ ] Paquetes Python instalados
- [ ] SNAP GPT funcional
- [ ] Credenciales Copernicus configuradas (.env)
- [ ] Test pasado (`python check_system.py`)
- [ ] Workflow ejecutado exitosamente

---

## Contacto y soporte

- **Documentación**: `docs/`
- **Issues**: GitHub Issues
- **Quick Start**: `docs/QUICK_START.md`
- **Installation**: `docs/INSTALLATION.md`
