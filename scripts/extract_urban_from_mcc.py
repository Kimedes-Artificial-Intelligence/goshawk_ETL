#!/usr/bin/env python3
"""
Script: extract_urban_from_mcc.py
Descripción: Extrae las categorías urbanas del MCC y crea una máscara
             para un AOI específico
Uso: python scripts/extract_urban_from_mcc.py <mcc_file> <aoi_file> <output_file>
"""

import os
import sys
import logging
import geopandas as gpd
from pathlib import Path

# Agregar directorio scripts al path si es necesario
sys.path.insert(0, str(Path(__file__).parent))
from logging_utils import LoggerConfig

# Configurar logger
logger = LoggerConfig.setup_script_logger(
    script_name='extract_urban_from_mcc',
    level=logging.INFO,
    console_level=logging.INFO
)

# Códigos de suelo urbano del MCC v1r0-2023
# Según clasificación oficial ICGC del archivo proporcionado
# CÓDIGOS URBANOS: 34x (superficies artificiales/urbanas)
URBAN_CODES = [
    '34',    # Todas las zonas urbanas
    '341',   # Casc urbà
    '342',   # Eixample
    '343',   # Zones urbanes laxes
    '344',   # Edificacions aïllades en l'espai rural
    '345',   # Àrees residencials aïllades
    '346',   # Zones verdes
    '347',   # Zones industrials, comercials i/o de serveis
    '348',   # Zones esportives i de lleure
    '349',   # Zones d'extracció minera i/o abocadors
    '350',   # Zones en transformació
    '351',   # Xarxa viària
    '352',   # Sòl nu urbà
    '353',   # Zones aeroportuàries
    '354',   # Xarxa ferroviària
    '355',   # Zones portuàries
]


def extract_urban(mcc_file, aoi_file, output_file):
    """
    Extrae áreas urbanas del MCC para un AOI
    """
    LoggerConfig.log_section(logger, "EXTRACCIÓN DE SUELO URBANO DEL MCC")
    
    # Leer MCC
    logger.info(f"\n📁 Leyendo MCC: {mcc_file}")
    
    # Si es GeoPackage, especificar la capa correcta
    if mcc_file.endswith('.gpkg'):
        # Intentar leer la capa 'cobertes_sol' primero
        try:
            mcc = gpd.read_file(mcc_file, layer='cobertes_sol')
            logger.info(f"   Leyendo capa: cobertes_sol")
        except:
            # Si falla, intentar leer sin especificar capa
            mcc = gpd.read_file(mcc_file)
    else:
        mcc = gpd.read_file(mcc_file)
    
    logger.info(f"   Total polígonos: {len(mcc)}")
    logger.info(f"   CRS: {mcc.crs}")
    logger.info(f"   Columnas: {', '.join(mcc.columns)}")
    
    # Buscar columna de código de cobertura
    # Puede ser 'codi', 'CODI', 'codigo', etc.
    code_col = None
    possible_cols = ['nivell_2', 'nivell_4',  # Para MCC v1r0-2023
                     'codi', 'CODI', 'codigo', 'CODIGO', 'code', 'CODE', 
                     'Codi_Subt', 'CODI_SUBT', 'codi_subt', 
                     'codi_nivell4', 'CODI_NIVELL4',
                     'codi_cobert', 'CODI_COBERT']
    
    for col in possible_cols:
        if col in mcc.columns:
            code_col = col
            break
    
    if not code_col:
        logger.error(f"\n❌ No se encontró columna de código")
        logger.info("Columnas disponibles:")
        for col in mcc.columns:
            sample = str(mcc[col].iloc[0])[:50] if len(mcc) > 0 else "N/A"
            logger.info(f"  - {col}: {sample}")
        return False
    
    logger.info(f"\n✓ Columna de código: {code_col}")
    
    # Mostrar distribución de códigos
    logger.info("\n📊 Distribución de códigos:")
    code_counts = mcc[code_col].astype(str).str[:2].value_counts()
    for code, count in code_counts.head(10).items():
        logger.info(f"   {code}**: {count} polígonos")
    
    # Filtrar urbano
    logger.info("\n🏙️  Filtrando áreas urbanas...")
    
    # Intentar diferentes longitudes de código
    urban_mask = False
    for code in URBAN_CODES:
        mask = mcc[code_col].astype(str).str.startswith(code)
        urban_mask = urban_mask | mask
    
    urban = mcc[urban_mask].copy()
    logger.info(f"   Polígonos urbanos: {len(urban)} ({len(urban)/len(mcc)*100:.1f}%)")
    
    if len(urban) == 0:
        logger.error("\n❌ No se encontraron áreas urbanas")
        return False
    
    # Leer AOI
    logger.info(f"\n📍 Leyendo AOI: {aoi_file}")
    aoi = gpd.read_file(aoi_file)
    logger.info(f"   CRS: {aoi.crs}")
    
    # Reproyectar si es necesario
    if urban.crs != aoi.crs:
        logger.info(f"   Reproyectando AOI de {aoi.crs} a {urban.crs}")
        aoi = aoi.to_crs(urban.crs)
    
    # Intersección
    logger.info("\n✂️  Intersectando con AOI...")
    urban_aoi = gpd.overlay(urban, aoi, how='intersection')
    logger.info(f"   Polígonos resultantes: {len(urban_aoi)}")
    
    if len(urban_aoi) == 0:
        logger.warning("\n⚠️  No hay áreas urbanas en el AOI")
        return False
    
    # Calcular área
    if urban_aoi.crs and urban_aoi.crs.is_projected:
        total_area = urban_aoi.geometry.area.sum() / 1e6  # km²
        logger.info(f"   Área urbana total: {total_area:.2f} km²")
    
    # Disolver en una geometría
    logger.info("\n🔗 Disolviendo geometrías...")
    urban_dissolved = urban_aoi.dissolve()
    
    # Guardar
    logger.info(f"\n💾 Guardando: {output_file}")
    os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
    urban_dissolved.to_file(output_file, driver='GeoJSON')
    
    logger.info("\n✅ Extracción completada con éxito")
    logger.info(f"   Archivo: {output_file}")
    
    return True


def main():
    if len(sys.argv) < 4:
        print("Uso: python extract_urban_from_mcc.py <mcc_file> <aoi_file> <output_file>")
        print("\nEjemplo:")
        print("  python scripts/extract_urban_from_mcc.py \\")
        print("    data/mcc_v5s_cobertes.shp \\")
        print("    aoi/figueres.geojson \\")
        print("    data/urban_figueres.geojson")
        print("\nFormatos soportados: .shp, .geojson, .gpkg")
        return 1
    
    mcc_file = sys.argv[1]
    aoi_file = sys.argv[2]
    output_file = sys.argv[3]
    
    # Verificar archivos
    if not os.path.exists(mcc_file):
        logger.error(f"❌ No existe: {mcc_file}")
        return 1
    
    if not os.path.exists(aoi_file):
        logger.error(f"❌ No existe: {aoi_file}")
        return 1
    
    # Extraer
    success = extract_urban(mcc_file, aoi_file, output_file)
    
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
