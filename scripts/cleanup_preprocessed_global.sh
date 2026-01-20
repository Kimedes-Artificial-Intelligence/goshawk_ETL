#!/bin/bash
#
# Script: cleanup_preprocessed_global.sh
# Descripción: Limpia productos preprocessados del workspace Y del caché global
#
# ⚠️  ADVERTENCIA: Este script elimina preprocessados de FORMA PERMANENTE
#     tanto del workspace local como del caché global en data/preprocessed_slc/
#
# REQUISITOS OBLIGATORIOS para permitir eliminación:
# 1. Procesamiento InSAR completo (productos en fusion/insar/)
# 2. Procesamiento polarimétrico completo (productos en polarimetry/)
# 3. Productos guardados en repositorio (data/processed_products/)
# 4. Mínimo de productos verificados
#
# Uso:
#     bash scripts/cleanup_preprocessed_global.sh processing/arenys_de_munt/insar_desc_iw1
#     bash scripts/cleanup_preprocessed_global.sh processing/arenys_de_munt/insar_desc_iw1 --execute
#

set -e

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Umbrales de seguridad
MIN_INSAR_PRODUCTS=50        # Mínimo de interferogramas
MIN_POL_PRODUCTS=50          # Mínimo de productos polarimétricos
MIN_REPO_INSAR=100           # Mínimo en repositorio (short + long)

# Verificar argumentos
if [ $# -eq 0 ]; then
    echo "Uso: $0 <workspace_dir> [--execute]"
    echo ""
    echo "⚠️  ADVERTENCIA: Este script elimina preprocessados del caché global"
    echo ""
    echo "Requisitos obligatorios:"
    echo "  - InSAR procesado: mínimo $MIN_INSAR_PRODUCTS interferogramas"
    echo "  - Polarimetría procesada: mínimo $MIN_POL_PRODUCTS productos"
    echo "  - Repositorio: mínimo $MIN_REPO_INSAR productos InSAR guardados"
    echo ""
    echo "Ejemplos:"
    echo "  $0 processing/arenys_de_munt/insar_desc_iw1           # Dry-run"
    echo "  $0 processing/arenys_de_munt/insar_desc_iw1 --execute # Ejecutar"
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

# Extraer orbit y subswath del nombre del workspace
# Formato: insar_desc_iw1 o insar_asce_iw2
if [[ $WORKSPACE_NAME =~ insar_([a-z]+)_(iw[0-9]) ]]; then
    ORBIT_SHORT="${BASH_REMATCH[1]}"  # desc o asce
    SUBSWATH="${BASH_REMATCH[2]}"     # iw1, iw2, iw3
else
    echo -e "${RED}✗ No se pudo extraer orbit/subswath de: $WORKSPACE_NAME${NC}"
    exit 1
fi

GLOBAL_CACHE_DIR="data/preprocessed_slc/${ORBIT_SHORT}_${SUBSWATH}"

echo "================================================================"
echo "LIMPIEZA DE PRODUCTOS PREPROCESSADOS (LOCAL + GLOBAL)"
echo "================================================================"
echo "Proyecto: $PROJECT_NAME"
echo "Workspace: $WORKSPACE_NAME"
echo "Órbita/Subswath: ${ORBIT_SHORT}/${SUBSWATH}"
echo ""

if [ $DRY_RUN -eq 1 ]; then
    echo -e "${BLUE}Modo: DRY-RUN (solo análisis)${NC}"
else
    echo -e "${RED}⚠️  Modo: EJECUCIÓN (eliminará archivos PERMANENTEMENTE)${NC}"
fi
echo ""

# Verificar productos finales
PREPROCESSED_DIR="$WORKSPACE_DIR/preprocessed_slc"
FUSION_DIR="$WORKSPACE_DIR/fusion"
POLARIMETRY_DIR="$WORKSPACE_DIR/polarimetry"
POLARIMETRY_CROPPED_DIR="$WORKSPACE_DIR/polarimetry/cropped"

# ========================================
# VERIFICACIÓN 1: PROCESAMIENTO InSAR
# ========================================
INSAR_PRODUCTS=0
INSAR_SHORT=0
INSAR_LONG=0

if [ -d "$FUSION_DIR/insar" ]; then
    # Contar productos finales recortados al AOI
    INSAR_PRODUCTS=$(find "$FUSION_DIR/insar" -name "*.tif" 2>/dev/null | wc -l)
    
    # Discriminar entre short y long
    INSAR_SHORT=$(find "$FUSION_DIR/insar" -name "*.tif" ! -name "*LONG*" 2>/dev/null | wc -l)
    INSAR_LONG=$(find "$FUSION_DIR/insar" -name "*LONG*.tif" 2>/dev/null | wc -l)
fi

# ========================================
# VERIFICACIÓN 2: PROCESAMIENTO POLARIMÉTRICO
# ========================================
POL_PRODUCTS=0
POL_CROPPED=0

if [ -d "$POLARIMETRY_DIR" ]; then
    # Productos polarimétricos procesados
    POL_PRODUCTS=$(find "$POLARIMETRY_DIR" -name "*HAAlpha*.dim" 2>/dev/null | wc -l)
fi

if [ -d "$POLARIMETRY_CROPPED_DIR" ]; then
    # Productos recortados al AOI
    POL_CROPPED=$(find "$POLARIMETRY_CROPPED_DIR" -name "*_cropped.tif" 2>/dev/null | wc -l)
fi

# ========================================
# VERIFICACIÓN 3: REPOSITORIO InSAR
# ========================================
REPO_SHORT=0
REPO_LONG=0
REPO_TOTAL=0

REPO_DIR="data/processed_products/${ORBIT_SHORT}_${SUBSWATH}"
if [ -d "$REPO_DIR/insar" ]; then
    if [ -d "$REPO_DIR/insar/short" ]; then
        REPO_SHORT=$(find "$REPO_DIR/insar/short" -name "*.dim" 2>/dev/null | wc -l)
    fi
    if [ -d "$REPO_DIR/insar/long" ]; then
        REPO_LONG=$(find "$REPO_DIR/insar/long" -name "*.dim" 2>/dev/null | wc -l)
    fi
    REPO_TOTAL=$((REPO_SHORT + REPO_LONG))
fi

# ========================================
# TAMAÑOS
# ========================================
LOCAL_SIZE=0
GLOBAL_SIZE=0

if [ -d "$PREPROCESSED_DIR" ]; then
    LOCAL_SIZE=$(du -sb "$PREPROCESSED_DIR" 2>/dev/null | cut -f1 || echo 0)
fi

if [ -d "$GLOBAL_CACHE_DIR" ]; then
    GLOBAL_SIZE=$(du -sb "$GLOBAL_CACHE_DIR" 2>/dev/null | cut -f1 || echo 0)
fi

LOCAL_SIZE_GB=$(echo "scale=2; $LOCAL_SIZE / 1024 / 1024 / 1024" | bc)
GLOBAL_SIZE_GB=$(echo "scale=2; $GLOBAL_SIZE / 1024 / 1024 / 1024" | bc)
TOTAL_SIZE_GB=$(echo "scale=2; ($LOCAL_SIZE + $GLOBAL_SIZE) / 1024 / 1024 / 1024" | bc)

# ========================================
# MOSTRAR ESTADO
# ========================================
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║              VERIFICACIÓN DE PROCESAMIENTO                     ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

echo "1. PROCESAMIENTO InSAR:"
echo "   Productos finales (fusion/insar/):"
echo "     - Pares SHORT: $INSAR_SHORT"
echo "     - Pares LONG:  $INSAR_LONG"
echo "     - TOTAL:       $INSAR_PRODUCTS"

if [ $INSAR_PRODUCTS -ge $MIN_INSAR_PRODUCTS ]; then
    echo -e "   ${GREEN}✓ InSAR completo ($INSAR_PRODUCTS >= $MIN_INSAR_PRODUCTS)${NC}"
    INSAR_OK=1
else
    echo -e "   ${RED}✗ InSAR incompleto ($INSAR_PRODUCTS < $MIN_INSAR_PRODUCTS requeridos)${NC}"
    INSAR_OK=0
fi

echo ""
echo "2. PROCESAMIENTO POLARIMÉTRICO:"
echo "   Productos H/A/Alpha:"
echo "     - Procesados: $POL_PRODUCTS"
echo "     - Recortados: $POL_CROPPED"

if [ $POL_PRODUCTS -ge $MIN_POL_PRODUCTS ]; then
    echo -e "   ${GREEN}✓ Polarimetría completa ($POL_PRODUCTS >= $MIN_POL_PRODUCTS)${NC}"
    POL_OK=1
else
    echo -e "   ${RED}✗ Polarimetría incompleta ($POL_PRODUCTS < $MIN_POL_PRODUCTS requeridos)${NC}"
    POL_OK=0
fi

echo ""
echo "3. REPOSITORIO InSAR (guardado permanente):"
echo "   data/processed_products/${ORBIT_SHORT}_${SUBSWATH}/insar/:"
echo "     - SHORT: $REPO_SHORT pares"
echo "     - LONG:  $REPO_LONG pares"
echo "     - TOTAL: $REPO_TOTAL"

if [ $REPO_TOTAL -ge $MIN_REPO_INSAR ]; then
    echo -e "   ${GREEN}✓ Repositorio completo ($REPO_TOTAL >= $MIN_REPO_INSAR)${NC}"
    REPO_OK=1
else
    echo -e "   ${RED}✗ Repositorio incompleto ($REPO_TOTAL < $MIN_REPO_INSAR requeridos)${NC}"
    REPO_OK=0
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                 ESPACIO A LIBERAR                              ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Workspace local:  ${LOCAL_SIZE_GB} GB"
echo "  Caché global:     ${GLOBAL_SIZE_GB} GB"
echo "  ─────────────────────────────────"
echo "  TOTAL:           ${TOTAL_SIZE_GB} GB"
echo ""

# ========================================
# DECISIÓN: ¿SE PUEDE ELIMINAR?
# ========================================
CAN_DELETE=0
WARNINGS=0
ERRORS=0

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                   RESULTADO FINAL                              ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

if [ $INSAR_OK -eq 0 ]; then
    echo -e "${RED}✗ BLOQUEADO: Procesamiento InSAR incompleto${NC}"
    ((ERRORS++))
fi

if [ $POL_OK -eq 0 ]; then
    echo -e "${RED}✗ BLOQUEADO: Procesamiento polarimétrico incompleto${NC}"
    ((ERRORS++))
fi

if [ $REPO_OK -eq 0 ]; then
    echo -e "${RED}✗ BLOQUEADO: Repositorio InSAR incompleto${NC}"
    ((ERRORS++))
fi

if [ ! -d "$GLOBAL_CACHE_DIR" ]; then
    echo -e "${YELLOW}ℹ Caché global no existe: $GLOBAL_CACHE_DIR${NC}"
fi

echo ""

if [ $ERRORS -gt 0 ]; then
    echo -e "${RED}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║  NO ES SEGURO ELIMINAR PREPROCESSADOS                         ║${NC}"
    echo -e "${RED}║  Se encontraron $ERRORS verificaciones fallidas                      ║${NC}"
    echo -e "${RED}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Completa el procesamiento antes de eliminar:"
    [ $INSAR_OK -eq 0 ] && echo "  1. Ejecuta procesamiento InSAR completo"
    [ $POL_OK -eq 0 ] && echo "  2. Ejecuta procesamiento polarimétrico"
    [ $REPO_OK -eq 0 ] && echo "  3. Verifica que productos se guardaron al repositorio"
    exit 1
else
    CAN_DELETE=1
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║  ✓ TODOS LOS REQUISITOS CUMPLIDOS                             ║${NC}"
    echo -e "${GREEN}║  Es seguro eliminar los productos preprocessados             ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
fi

echo ""

# ========================================
# EJECUCIÓN O DRY-RUN
# ========================================
if [ $DRY_RUN -eq 1 ]; then
    echo "Vista previa de eliminación:"
    echo ""
    
    if [ -d "$PREPROCESSED_DIR" ]; then
        echo "1. Workspace local:"
        echo "   $PREPROCESSED_DIR/"
        PREPROC_COUNT=$(find "$PREPROCESSED_DIR" -name "*.dim" 2>/dev/null | wc -l)
        echo "   Productos: $PREPROC_COUNT"
        echo "   Tamaño: ${LOCAL_SIZE_GB} GB"
        echo ""
    fi
    
    if [ -d "$GLOBAL_CACHE_DIR" ]; then
        echo "2. Caché global:"
        echo "   $GLOBAL_CACHE_DIR/"
        GLOBAL_COUNT=$(find "$GLOBAL_CACHE_DIR" -name "*.dim" 2>/dev/null | wc -l)
        echo "   Productos: $GLOBAL_COUNT"
        echo "   Tamaño: ${GLOBAL_SIZE_GB} GB"
        echo ""
    fi
    
    echo "TOTAL a liberar: ${TOTAL_SIZE_GB} GB"
    echo ""
    echo -e "${GREEN}Para ejecutar: $0 $WORKSPACE_DIR --execute${NC}"
else
    # Confirmación final
    echo -e "${RED}⚠️  ADVERTENCIA FINAL${NC}"
    echo ""
    echo "Vas a eliminar PERMANENTEMENTE:"
    echo "  1. Workspace local: $PREPROCESSED_DIR/"
    echo "  2. Caché global: $GLOBAL_CACHE_DIR/"
    echo ""
    echo "Total: ${TOTAL_SIZE_GB} GB"
    echo ""
    echo "Esta acción es IRREVERSIBLE"
    echo "Otros proyectos NO podrán reutilizar estos preprocessados"
    echo ""
    read -p "Escribe 'DELETE' (mayúsculas) para confirmar: " -r
    echo
    if [ "$REPLY" != "DELETE" ]; then
        echo "Cancelado"
        exit 0
    fi
    
    # Crear backup de lista
    BACKUP_DIR="$WORKSPACE_DIR/logs"
    mkdir -p "$BACKUP_DIR"
    BACKUP_FILE="$BACKUP_DIR/deleted_preprocessed_$(date +%Y%m%d_%H%M%S).txt"
    
    echo ""
    echo "Creando backup de lista..."
    {
        echo "=== ELIMINACIÓN $(date) ==="
        echo ""
        echo "Workspace: $WORKSPACE_DIR"
        echo "Órbita: $ORBIT_SHORT"
        echo "Subswath: $SUBSWATH"
        echo ""
        echo "=== WORKSPACE LOCAL ==="
        [ -d "$PREPROCESSED_DIR" ] && find "$PREPROCESSED_DIR" 2>/dev/null || echo "(no existe)"
        echo ""
        echo "=== CACHE GLOBAL ==="
        [ -d "$GLOBAL_CACHE_DIR" ] && find "$GLOBAL_CACHE_DIR" 2>/dev/null || echo "(no existe)"
    } > "$BACKUP_FILE"
    echo "✓ Lista guardada en: $BACKUP_FILE"
    echo ""
    
    # Eliminar workspace local
    if [ -d "$PREPROCESSED_DIR" ]; then
        echo "Eliminando workspace local..."
        rm -rf "$PREPROCESSED_DIR"
        echo "✓ Workspace eliminado"
    fi
    
    # Eliminar caché global
    if [ -d "$GLOBAL_CACHE_DIR" ]; then
        echo "Eliminando caché global..."
        rm -rf "$GLOBAL_CACHE_DIR"
        echo "✓ Caché global eliminado"
    fi
    
    echo ""
    echo -e "${GREEN}✓ PREPROCESSADOS ELIMINADOS${NC}"
    echo "  Espacio liberado: ${TOTAL_SIZE_GB} GB"
    echo "  Backup: $BACKUP_FILE"
    echo ""
    echo -e "${YELLOW}NOTA: Futuros proyectos tendrán que preprocesar desde SLCs originales${NC}"
fi

echo ""
echo "================================================================"
