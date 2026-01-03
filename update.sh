#!/bin/bash
# Script para actualizar el repositorio y environment
# Uso: bash update.sh

set -e

echo "=========================================="
echo "GOSHAWK ETL - Actualización"
echo "=========================================="
echo ""

# Obtener cambios del repositorio
echo "📥 Descargando últimos cambios..."
git fetch origin

# Mostrar cambios
CHANGES=$(git log HEAD..origin/main --oneline 2>/dev/null || git log HEAD..origin/master --oneline 2>/dev/null || echo "Sin cambios")

if [ "$CHANGES" = "Sin cambios" ]; then
    echo "✓ Ya estás en la última versión"
    echo ""
else
    echo ""
    echo "📋 Cambios disponibles:"
    echo "$CHANGES"
    echo ""
    
    read -p "¿Aplicar cambios? (Y/n): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "❌ Actualización cancelada"
        exit 0
    fi
    
    echo "📦 Aplicando cambios..."
    git pull
    echo "✓ Código actualizado"
fi

echo ""

# Actualizar environment si hay cambios en environment.yml
if git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -q "environment.yml"; then
    echo "⚠️  Detectados cambios en environment.yml"
    read -p "¿Actualizar environment conda? (Y/n): " -n 1 -r
    echo
    
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo "🔄 Actualizando environment..."
        
        if command -v mamba &> /dev/null; then
            mamba env update -f environment.yml --prune
        else
            conda env update -f environment.yml --prune
        fi
        
        echo "✓ Environment actualizado"
    fi
fi

echo ""
echo "=========================================="
echo "✅ ACTUALIZACIÓN COMPLETADA"
echo "=========================================="
echo ""
echo "Próximos pasos:"
echo "  1. conda activate goshawk_etl"
echo "  2. python check_system.py  # Verificar"
echo "  3. python run_complete_workflow.py"
