#!/bin/bash
# Script: batch_urban_crop.sh
# Descripción: Procesa todos los municipios para recortar a suelo urbano

set -e

MCC_FILE="data/cobertes-sol-v1r0-2023.gpkg"

# Colores
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "================================================================================"
echo "BATCH: Recorte a Suelo Urbano - Todos los Municipios"
echo "================================================================================"
echo ""
echo "MCC: $MCC_FILE"
echo ""

# Verificar MCC
if [ ! -f "$MCC_FILE" ]; then
    echo "❌ Error: No existe $MCC_FILE"
    exit 1
fi

# Contar municipios
total=$(find processing -mindepth 1 -maxdepth 1 -type d | wc -l)
echo "📊 Total municipios a procesar: $total"
echo ""

# Confirmar
read -p "¿Continuar? (s/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[SsYy]$ ]]; then
    echo "Cancelado"
    exit 0
fi

echo ""

# Procesar cada municipio
count=0
success=0
failed=0
skipped=0

for workspace in processing/*/; do
    count=$((count + 1))
    municipio=$(basename "$workspace")
    
    echo "================================================================================"
    echo -e "${BLUE}[$count/$total]${NC} Procesando: ${GREEN}$municipio${NC}"
    echo "================================================================================"
    
    # Verificar si tiene AOI
    if [ ! -f "$workspace/aoi.geojson" ] && [ ! -f "$workspace/config.txt" ]; then
        echo -e "${YELLOW}⚠ Saltado: No tiene AOI${NC}"
        skipped=$((skipped + 1))
        echo ""
        continue
    fi
    
    # Verificar si ya tiene urban_products
    if [ -d "$workspace/urban_products" ]; then
        echo "ℹ Ya existe urban_products/, regenerando..."
        rm -rf "$workspace/urban_products"
    fi
    
    # Ejecutar workflow
    if ./scripts/workflow_urban_crop.sh "$workspace" "$MCC_FILE" > /tmp/urban_crop_${municipio}.log 2>&1; then
        echo -e "${GREEN}✓ Completado${NC}"
        success=$((success + 1))
        
        # Mostrar resumen
        if [ -d "$workspace/urban_products" ]; then
            n_products=$(find "$workspace/urban_products" -name "*.tif" | wc -l)
            size=$(du -sh "$workspace/urban_products" | cut -f1)
            echo "  Productos: $n_products, Tamaño: $size"
        fi
    else
        echo -e "${YELLOW}✗ Falló${NC}"
        failed=$((failed + 1))
        echo "  Ver log: /tmp/urban_crop_${municipio}.log"
    fi
    
    echo ""
done

# Resumen final
echo "================================================================================"
echo "RESUMEN FINAL"
echo "================================================================================"
echo "Total municipios: $total"
echo "  ✓ Exitosos: $success"
echo "  ✗ Fallidos:  $failed"
echo "  ⊘ Saltados:  $skipped"
echo ""

if [ $failed -gt 0 ]; then
    echo "Municipios fallidos - revisar logs en /tmp/urban_crop_*.log"
fi

echo "Productos urbanos generados en: processing/*/urban_products/"
echo ""
