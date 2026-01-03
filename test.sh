#!/bin/bash
# Quick test para verificar instalación

set -e

echo "=========================================="
echo "GOSHAWK ETL - Test Rápido"
echo "=========================================="
echo ""

# Activar environment
eval "$(conda shell.bash hook)"
conda activate goshawk_etl 2>/dev/null || {
    echo "❌ Environment no activado"
    echo "Ejecuta: bash setup.sh"
    exit 1
}

echo "✓ Environment activado: goshawk_etl"
echo ""

# Test imports
echo "Verificando paquetes Python..."
python3 -c "
import sys
import geopandas
import rasterio
import shapely
import numpy
import pandas
import matplotlib

print('✓ geopandas:', geopandas.__version__)
print('✓ rasterio:', rasterio.__version__)
print('✓ numpy:', numpy.__version__)
print('✓ pandas:', pandas.__version__)
"

echo ""

# Test SNAP
echo "Verificando SNAP GPT..."
if command -v gpt &> /dev/null; then
    gpt -h | head -n 1
    echo "✓ SNAP GPT disponible"
else
    echo "⚠️  SNAP GPT no encontrado (se instala con snapista)"
fi

echo ""

# Test estructura
echo "Verificando estructura..."
for dir in aoi data processing logs; do
    if [ -d "$dir" ]; then
        echo "✓ $dir/"
    else
        echo "⚠️  $dir/ falta (se creará automáticamente)"
    fi
done

echo ""

# Test credenciales
if [ -f .env ]; then
    echo "✓ Archivo .env existe"
    
    if grep -q "tu_usuario" .env 2>/dev/null; then
        echo "⚠️  Credenciales aún no configuradas"
    else
        echo "✓ Credenciales configuradas"
    fi
else
    echo "⚠️  Archivo .env falta"
fi

echo ""
echo "=========================================="
echo "✅ Test completado"
echo "=========================================="
echo ""
echo "Siguiente paso:"
echo "  python run_complete_workflow.py"
