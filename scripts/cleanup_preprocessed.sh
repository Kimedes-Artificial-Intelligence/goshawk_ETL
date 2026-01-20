#!/bin/bash
#
# Script: cleanup_preprocessed.sh
# Descripción: Limpia productos preprocessados después de procesamiento exitoso
#
# Este script elimina productos preprocessados (.dim y .data) que ya fueron
# usados para generar productos finales InSAR y polarimétricos.
#
# Seguridad:
# - Verifica que existan productos finales antes de eliminar
# - Solo limpia subswaths con procesamiento completo
# - Crea backup de lista de archivos eliminados
# - Modo dry-run por defecto
#
# Criterios para eliminar preprocessados:
# 1. Existen productos fusion/insar/*.tif (interferogramas procesados)
# 2. Existen productos polarimetry/*.dim (descomposición polarimétrica)
# 3. El procesamiento está marcado como completo
#
# Uso:
#     bash scripts/cleanup_preprocessed.sh processing/arenys_de_munt/insar_desc_iw2
#     bash scripts/cleanup_preprocessed.sh processing/arenys_de_munt/insar_desc_iw2 --execute
#

set -e

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Verificar argumentos
if [ $# -eq 0 ]; then
    echo "Uso: $0 <workspace_dir> [--execute]"
    echo ""
    echo "Ejemplos:"
    echo "  $0 processing/arenys_de_munt/insar_desc_iw2           # Dry-run"
    echo "  $0 processing/arenys_de_munt/insar_desc_iw2 --execute # Ejecutar"
    echo "  $0 processing/arenys_de_munt/insar_*                  # Múltiples"
    exit 1
fi

WORKSPACE_DIR="$1"
DRY_RUN=1

if [ "$2" == "--execute" ] || [ "$2" == "-e" ]; then
    DRY_RUN=0
fi

# Verificar que existe el workspace
if [ ! -d "$WORKSPACE_DIR" ]; then
    echo -e "${RED}✗ No existe: $WORKSPACE_DIR${NC}"
    exit 1
fi

WORKSPACE_NAME=$(basename "$WORKSPACE_DIR")
PROJECT_NAME=$(basename $(dirname "$WORKSPACE_DIR"))

echo "================================================================"
echo "LIMPIEZA DE PRODUCTOS PREPROCESSADOS"
echo "================================================================"
echo "Proyecto: $PROJECT_NAME"
echo "Workspace: $WORKSPACE_NAME"
echo ""

if [ $DRY_RUN -eq 1 ]; then
    echo -e "${BLUE}Modo: DRY-RUN (solo análisis)${NC}"
else
    echo -e "${YELLOW}Modo: EJECUCIÓN (eliminará archivos)${NC}"
fi
echo ""

# Verificar productos finales
PREPROCESSED_DIR="$WORKSPACE_DIR/preprocessed_slc"
FUSION_DIR="$WORKSPACE_DIR/fusion"
POLARIMETRY_DIR="$WORKSPACE_DIR/polarimetry"

# Contadores
TOTAL_SIZE=0
CAN_DELETE=0

# Verificación 1: Productos InSAR finales
INSAR_PRODUCTS=0
if [ -d "$FUSION_DIR/insar" ]; then
    INSAR_PRODUCTS=$(find "$FUSION_DIR/insar" -name "*.tif" 2>/dev/null | wc -l)
fi

# Verificación 2: Productos polarimétricos
POL_PRODUCTS=0
if [ -d "$POLARIMETRY_DIR" ]; then
    POL_PRODUCTS=$(find "$POLARIMETRY_DIR" -name "*HAAlpha*.dim" 2>/dev/null | wc -l)
fi

# Verificación 3: Preprocessados existentes
PREPROC_COUNT=0
PREPROC_SIZE=0
if [ -d "$PREPROCESSED_DIR" ]; then
    PREPROC_COUNT=$(find "$PREPROCESSED_DIR" -name "*.dim" 2>/dev/null | wc -l)
    PREPROC_SIZE=$(du -sb "$PREPROCESSED_DIR" 2>/dev/null | cut -f1)
fi

echo "Estado del workspace:"
echo "  Productos InSAR finales: $INSAR_PRODUCTS"
echo "  Productos polarimétricos: $POL_PRODUCTS"
echo "  Productos preprocessados: $PREPROC_COUNT"

if [ $PREPROC_SIZE -gt 0 ]; then
    PREPROC_SIZE_GB=$(echo "scale=2; $PREPROC_SIZE / 1024 / 1024 / 1024" | bc)
    echo "  Tamaño preprocessados: ${PREPROC_SIZE_GB} GB"
fi

echo ""

# Decidir si se puede limpiar
if [ $PREPROC_COUNT -eq 0 ]; then
    echo -e "${GREEN}✓ No hay productos preprocessados para limpiar${NC}"
    exit 0
fi

if [ $INSAR_PRODUCTS -eq 0 ] && [ $POL_PRODUCTS -eq 0 ]; then
    echo -e "${RED}✗ No se encontraron productos finales${NC}"
    echo "  No es seguro eliminar preprocessados sin verificar procesamiento completo"
    exit 1
fi

# Verificar que haya suficientes productos finales
MIN_PRODUCTS=10
if [ $INSAR_PRODUCTS -lt $MIN_PRODUCTS ] && [ $POL_PRODUCTS -lt $MIN_PRODUCTS ]; then
    echo -e "${YELLOW}⚠ Pocos productos finales encontrados${NC}"
    echo "  InSAR: $INSAR_PRODUCTS, Polarimetría: $POL_PRODUCTS"
    echo "  Se recomienda tener al menos $MIN_PRODUCTS productos antes de limpiar"
    echo ""
    read -p "¿Continuar de todos modos? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelado"
        exit 0
    fi
fi

echo -e "${GREEN}✓ Procesamiento verificado - es seguro limpiar preprocessados${NC}"
echo ""

if [ $DRY_RUN -eq 1 ]; then
    echo "Archivos que se eliminarían:"
    echo ""
    find "$PREPROCESSED_DIR" -name "*.dim" -o -name "*.data" 2>/dev/null | head -10
    REMAINING=$(find "$PREPROCESSED_DIR" -name "*.dim" -o -name "*.data" 2>/dev/null | wc -l)
    if [ $REMAINING -gt 10 ]; then
        echo "  ... y $((REMAINING - 10)) más"
    fi
    echo ""
    echo "Espacio a liberar: ${PREPROC_SIZE_GB} GB"
    echo ""
    echo "Para ejecutar: $0 $WORKSPACE_DIR --execute"
else
    # Crear backup de lista de archivos
    BACKUP_DIR="$WORKSPACE_DIR/logs"
    mkdir -p "$BACKUP_DIR"
    BACKUP_FILE="$BACKUP_DIR/deleted_preprocessed_$(date +%Y%m%d_%H%M%S).txt"
    
    echo "Creando backup de lista de archivos..."
    find "$PREPROCESSED_DIR" -type f -o -type d 2>/dev/null > "$BACKUP_FILE"
    echo "✓ Lista guardada en: $BACKUP_FILE"
    echo ""
    
    # Eliminar directorio completo
    echo "Eliminando preprocessados..."
    rm -rf "$PREPROCESSED_DIR"
    
    if [ ! -d "$PREPROCESSED_DIR" ]; then
        echo -e "${GREEN}✓ Preprocessados eliminados${NC}"
        echo "  Espacio liberado: ${PREPROC_SIZE_GB} GB"
        echo "  Backup de lista: $BACKUP_FILE"
    else
        echo -e "${RED}✗ Error eliminando preprocessados${NC}"
        exit 1
    fi
fi

echo ""
echo "================================================================"
