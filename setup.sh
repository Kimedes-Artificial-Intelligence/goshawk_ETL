#!/bin/bash
# Setup automático para goshawk_ETL
# Uso: bash setup.sh

set -e  # Exit on error

echo "=========================================="
echo "GOSHAWK ETL - Setup Automático"
echo "=========================================="
echo ""

# Detectar sistema operativo
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="Linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
else
    echo "❌ Sistema operativo no soportado: $OSTYPE"
    exit 1
fi

echo "✓ Sistema detectado: $OS"
echo ""

# 1. Verificar conda/mamba
echo "📦 Verificando gestor de paquetes conda..."
if command -v mamba &> /dev/null; then
    CONDA_CMD="mamba"
    echo "✓ Encontrado: mamba (rápido)"
elif command -v conda &> /dev/null; then
    CONDA_CMD="conda"
    echo "✓ Encontrado: conda"
else
    echo "❌ ERROR: conda/mamba no instalado"
    echo ""
    echo "Instala Miniconda/Miniforge desde:"
    echo "  https://docs.conda.io/en/latest/miniconda.html"
    echo "  https://github.com/conda-forge/miniforge"
    exit 1
fi
echo ""

# 2. Crear environment
ENV_NAME="goshawk_etl"
echo "🔧 Creando environment: $ENV_NAME"

if conda env list | grep -q "^${ENV_NAME} "; then
    echo "⚠️  Environment ya existe"
    read -p "¿Recrear desde cero? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "🗑️  Eliminando environment existente..."
        conda env remove -n $ENV_NAME -y
    else
        echo "✓ Usando environment existente"
        conda activate $ENV_NAME
        echo ""
        echo "=========================================="
        echo "✅ SETUP COMPLETADO"
        echo "=========================================="
        echo ""
        echo "Activa el environment con:"
        echo "  conda activate $ENV_NAME"
        echo ""
        echo "Siguiente paso:"
        echo "  python run_complete_workflow.py"
        exit 0
    fi
fi

echo "⏳ Instalando paquetes (puede tardar 5-10 min)..."
$CONDA_CMD env create -f environment.yml

echo ""
echo "✓ Environment creado exitosamente"
echo ""

# 3. Crear directorios necesarios
echo "📁 Creando estructura de directorios..."
mkdir -p data/{sentinel1_slc,sentinel2_l2a,orbits}
mkdir -p processing
mkdir -p logs
mkdir -p aoi

echo "✓ Directorios creados"
echo ""

# 4. Verificar credentials (opcional)
if [ ! -f .env ]; then
    echo "🔐 Configurando credenciales Copernicus..."
    echo ""
    echo "Necesitas crear una cuenta en:"
    echo "  https://dataspace.copernicus.eu/"
    echo ""
    read -p "¿Ya tienes cuenta? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        read -p "Usuario: " USER
        read -sp "Password: " PASS
        echo ""
        cat > .env << EOF
# Credenciales Copernicus Dataspace
CDSE_USERNAME=$USER
CDSE_PASSWORD=$PASS
EOF
        chmod 600 .env
        echo "✓ Credenciales guardadas en .env"
    else
        echo "⚠️  Necesitarás configurar .env manualmente después"
        cat > .env << 'EOF'
# Credenciales Copernicus Dataspace
# Crear cuenta en: https://dataspace.copernicus.eu/
CDSE_USERNAME=tu_usuario
CDSE_PASSWORD=tu_password
EOF
    fi
fi
echo ""

# 5. Instalar y configurar SNAP 13.0.0
echo "=========================================="
echo "📡 SNAP 13.0.0 - Instalación y Configuración"
echo "=========================================="
echo ""
echo "ESA SNAP es necesario para procesar imágenes SAR"
echo ""

eval "$(conda shell.bash hook)"
conda activate $ENV_NAME

# Ejecutar script de instalación de SNAP
if [ -f "scripts/system/install_snap.sh" ]; then
    bash scripts/install_snap.sh
else
    echo "❌ Error: scripts/install_snap.sh no encontrado"
    exit 1
fi
echo ""

# 6. Test rápido
echo "🧪 Verificando instalación completa..."

python -c "import geopandas; import rasterio; print('✓ Paquetes geoespaciales OK')" || {
    echo "❌ Error importando paquetes"
    exit 1
}

python -c "from esa_snappy import ProductIO; print('✓ SNAP Python interface OK')" || {
    echo "❌ Error importando esa_snappy"
    exit 1
}

if command -v gpt &> /dev/null; then
    echo "✓ SNAP GPT disponible en PATH"
else
    echo "⚠️  SNAP GPT no en PATH. Agrega esto a tu shell:"
    echo "   export PATH=\"/opt/esa-snap/bin:\$PATH\""
fi
echo ""

# 7. Instrucciones finales
echo "=========================================="
echo "✅ SETUP COMPLETADO EXITOSAMENTE"
echo "=========================================="
echo ""
echo "📝 Próximos pasos:"
echo ""
echo "1. Activa el environment:"
echo "   conda activate $ENV_NAME"
echo ""
echo "2. Configura credenciales (si no lo hiciste):"
echo "   nano .env"
echo ""
echo "3. Añade tu área de interés (GeoJSON):"
echo "   cp tu_aoi.geojson aoi/"
echo ""
echo "4. Ejecuta el workflow:"
echo "   python run_complete_workflow.py"
echo ""
echo "📚 Documentación:"
echo "   cat docs/QUICK_START.md"
echo "   cat docs/INSTALLATION.md"
echo ""
