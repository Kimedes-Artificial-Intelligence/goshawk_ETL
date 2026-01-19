#!/bin/bash
#
# Script: rename_legacy_preprocessed.sh
# Descripción: Renombra productos preprocessed con naming incorrecto
#              De: SLC_YYYYMMDD_S1A_IW_SLC__...
#              A:  S1A_IW_SLC__...
#
# Características:
# - Modo dry-run por defecto (muestra cambios sin aplicarlos)
# - Renombra tanto .dim como .data correspondientes
# - Verifica que destino no existe antes de renombrar
# - Crea backup log de cambios realizados
# - Maneja archivos derivados (HAAlpha, cropped, etc.)
#
# Uso:
#     bash scripts/rename_legacy_preprocessed.sh           # Dry-run (solo muestra)
#     bash scripts/rename_legacy_preprocessed.sh --execute # Ejecuta renombrado
#

set -e  # Exit on error

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Variables
DRY_RUN=1
LOG_FILE="logs/rename_preprocessed_$(date +%Y%m%d_%H%M%S).log"
RENAMED_COUNT=0
SKIPPED_COUNT=0
ERROR_COUNT=0

# Verificar argumentos
if [ "$1" == "--execute" ] || [ "$1" == "-e" ]; then
    DRY_RUN=0
    echo -e "${YELLOW}⚠️  MODO EJECUCIÓN ACTIVADO${NC}"
    echo "Los archivos serán renombrados permanentemente"
    echo ""
    read -p "¿Continuar? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelado por el usuario"
        exit 0
    fi
else
    echo -e "${BLUE}ℹ️  MODO DRY-RUN (solo muestra cambios, no los aplica)${NC}"
    echo "Usa: $0 --execute  para aplicar cambios"
    echo ""
fi

# Crear directorio de logs
mkdir -p logs

# Función para renombrar archivo
rename_file() {
    local old_path="$1"
    local new_path="$2"
    local file_type="$3"  # "dim" o "data"
    
    # Verificar que origen existe
    if [ ! -e "$old_path" ]; then
        echo -e "${RED}✗ No existe: $old_path${NC}" | tee -a "$LOG_FILE"
        ((ERROR_COUNT++))
        return 1
    fi
    
    # Verificar que destino NO existe
    if [ -e "$new_path" ]; then
        echo -e "${YELLOW}⚠ Destino ya existe, saltando: $(basename "$new_path")${NC}" | tee -a "$LOG_FILE"
        ((SKIPPED_COUNT++))
        return 1
    fi
    
    if [ $DRY_RUN -eq 1 ]; then
        echo -e "${GREEN}[DRY-RUN]${NC} $file_type: $(basename "$old_path") → $(basename "$new_path")"
    else
        mv "$old_path" "$new_path"
        echo -e "${GREEN}✓${NC} $file_type: $(basename "$old_path") → $(basename "$new_path")" | tee -a "$LOG_FILE"
    fi
    
    return 0
}

# Función para procesar un archivo .dim y su .data correspondiente
process_pair() {
    local dim_file="$1"
    local basename_old=$(basename "$dim_file")
    local dirname=$(dirname "$dim_file")
    
    # Extraer nuevo nombre (eliminar prefijo SLC_YYYYMMDD_)
    # Patrón: SLC_20230604_S1A_IW_SLC__... → S1A_IW_SLC__...
    local basename_new=$(echo "$basename_old" | sed 's/SLC_[0-9]\{8\}_//')
    
    # Si no cambió, saltar (ya tiene formato correcto)
    if [ "$basename_old" == "$basename_new" ]; then
        return 0
    fi
    
    local new_dim="$dirname/$basename_new"
    local old_data="${dim_file%.dim}.data"
    local new_data="${new_dim%.dim}.data"
    
    # Renombrar .dim
    if rename_file "$dim_file" "$new_dim" "dim"; then
        ((RENAMED_COUNT++))
        
        # Renombrar .data si existe
        if [ -d "$old_data" ]; then
            if rename_file "$old_data" "$new_data" "data"; then
                ((RENAMED_COUNT++))
            fi
        fi
    fi
}

# Banner
echo "================================================================"
echo "RENOMBRADO DE PRODUCTOS PREPROCESSADOS LEGACY"
echo "================================================================"
echo ""
echo "Buscando archivos con formato incorrecto..."
echo ""

# Buscar todos los archivos .dim con prefijo incorrecto
mapfile -t DIM_FILES < <(find . -name "SLC_[0-9]*_S1*.dim" 2>/dev/null | sort)

TOTAL_FILES=${#DIM_FILES[@]}

if [ $TOTAL_FILES -eq 0 ]; then
    echo -e "${GREEN}✓ No se encontraron archivos con naming incorrecto${NC}"
    exit 0
fi

echo "Archivos encontrados: $TOTAL_FILES"
echo ""

# Categorizar archivos por tipo
PREPROCESSED_COUNT=0
POLARIMETRY_COUNT=0
CROPPED_COUNT=0

for dim in "${DIM_FILES[@]}"; do
    if [[ "$dim" == *"preprocessed_slc"* ]]; then
        ((PREPROCESSED_COUNT++))
    elif [[ "$dim" == *"polarimetry/cropped"* ]]; then
        ((CROPPED_COUNT++))
    elif [[ "$dim" == *"polarimetry"* ]]; then
        ((POLARIMETRY_COUNT++))
    fi
done

echo "Desglose por tipo:"
echo "  - Preprocessed SLC: $PREPROCESSED_COUNT"
echo "  - Polarimetry: $POLARIMETRY_COUNT"
echo "  - Cropped: $CROPPED_COUNT"
echo ""

if [ $DRY_RUN -eq 1 ]; then
    echo -e "${BLUE}Mostrando primeros 10 cambios propuestos:${NC}"
    echo ""
fi

# Procesar archivos
COUNT=0
for dim in "${DIM_FILES[@]}"; do
    ((COUNT++))
    
    # En dry-run, solo mostrar primeros 10 para no saturar
    if [ $DRY_RUN -eq 1 ] && [ $COUNT -gt 10 ]; then
        echo "  ... y $((TOTAL_FILES - 10)) más"
        break
    fi
    
    process_pair "$dim"
done

# Resumen
echo ""
echo "================================================================"
echo "RESUMEN"
echo "================================================================"

if [ $DRY_RUN -eq 1 ]; then
    echo -e "${BLUE}Modo: DRY-RUN (no se aplicaron cambios)${NC}"
    echo ""
    echo "Archivos que se renombrarían: $TOTAL_FILES"
    echo ""
    echo "Para aplicar cambios, ejecuta:"
    echo "  bash $0 --execute"
else
    echo -e "${GREEN}Modo: EJECUCIÓN (cambios aplicados)${NC}"
    echo ""
    echo "Archivos renombrados: $RENAMED_COUNT"
    echo "Archivos saltados: $SKIPPED_COUNT"
    echo "Errores: $ERROR_COUNT"
    echo ""
    echo "Log guardado en: $LOG_FILE"
    
    # Verificar que no quedan archivos con formato incorrecto
    REMAINING=$(find . -name "SLC_[0-9]*_S1*.dim" 2>/dev/null | wc -l)
    if [ $REMAINING -eq 0 ]; then
        echo -e "${GREEN}✓ Todos los archivos renombrados exitosamente${NC}"
    else
        echo -e "${YELLOW}⚠ Aún quedan $REMAINING archivos con formato incorrecto${NC}"
        echo "  (Probablemente porque ya existía el destino)"
    fi
fi

echo "================================================================"
