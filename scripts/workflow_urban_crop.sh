#!/bin/bash
# Script: workflow_urban_crop.sh
# Descripción: Workflow completo para recortar productos InSAR a suelo urbano
# Uso: ./scripts/workflow_urban_crop.sh <workspace_dir> <mcc_file>

set -e

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Funciones de ayuda
info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
}

warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

error() {
    echo -e "${RED}✗${NC} $1"
}

# Verificar argumentos
if [ "$#" -lt 2 ]; then
    echo "Uso: $0 <workspace_dir> <mcc_file>"
    echo ""
    echo "Ejemplo:"
    echo "  $0 processing/figueres data/mcc/mcc_v5s_cobertes.shp"
    echo ""
    echo "El MCC debe descargarse manualmente desde:"
    echo "  https://www.icgc.cat/ca/Descarregues/Cobertes-del-sol"
    exit 1
fi

WORKSPACE=$1
MCC_FILE=$2

# Verificar que existen
if [ ! -d "$WORKSPACE" ]; then
    error "No existe workspace: $WORKSPACE"
    exit 1
fi

if [ ! -f "$MCC_FILE" ]; then
    error "No existe archivo MCC: $MCC_FILE"
    exit 1
fi

echo "=========================================================================="
echo "WORKFLOW: Recorte a Suelo Urbano"
echo "=========================================================================="
info "Workspace: $WORKSPACE"
info "MCC: $MCC_FILE"
echo ""

# Buscar AOI
info "Buscando archivo AOI..."
AOI_FILE=$(find "$WORKSPACE" -name "*.geojson" -o -name "aoi.json" | head -1)

if [ -z "$AOI_FILE" ]; then
    # Buscar en config.txt
    CONFIG="$WORKSPACE/config.txt"
    if [ -f "$CONFIG" ]; then
        info "Extrayendo AOI de config.txt..."
        AOI_WKT=$(grep "^AOI=" "$CONFIG" | cut -d'=' -f2- | tr -d '"')
        
        if [ -n "$AOI_WKT" ]; then
            # Crear AOI temporal desde WKT
            AOI_FILE="$WORKSPACE/aoi_temp.geojson"
            python3 -c "
from shapely import wkt
from shapely.geometry import mapping
import json

aoi_wkt = '$AOI_WKT'
geom = wkt.loads(aoi_wkt)
geojson = {
    'type': 'FeatureCollection',
    'features': [{
        'type': 'Feature',
        'geometry': mapping(geom),
        'properties': {}
    }]
}
with open('$AOI_FILE', 'w') as f:
    json.dump(geojson, f)
"
            success "AOI extraído de config.txt"
        fi
    fi
fi

if [ -z "$AOI_FILE" ] || [ ! -f "$AOI_FILE" ]; then
    error "No se pudo encontrar AOI en $WORKSPACE"
    exit 1
fi

success "AOI encontrado: $AOI_FILE"

# Extraer suelo urbano
URBAN_FILE="$WORKSPACE/urban_mask.geojson"

info "Extrayendo áreas urbanas..."
python3 scripts/extract_urban_from_mcc.py "$MCC_FILE" "$AOI_FILE" "$URBAN_FILE"

if [ ! -f "$URBAN_FILE" ]; then
    error "Falló la extracción de áreas urbanas"
    exit 1
fi

success "Máscara urbana creada: $URBAN_FILE"
echo ""

# Recortar productos InSAR
info "Recortando productos InSAR a suelo urbano..."
python3 scripts/crop_to_urban_soil.py "$WORKSPACE" --mcc-file "$URBAN_FILE"

if [ $? -eq 0 ]; then
    success "Recorte completado"
    echo ""
    info "Productos urbanos en: $WORKSPACE/urban_products/"
    ls -lh "$WORKSPACE/urban_products/" 2>/dev/null || true
else
    error "Falló el recorte"
    exit 1
fi

# Limpiar AOI temporal si se creó
if [[ "$AOI_FILE" == *"aoi_temp.geojson" ]]; then
    rm -f "$AOI_FILE"
fi

echo ""
echo "=========================================================================="
success "WORKFLOW COMPLETADO"
echo "=========================================================================="
