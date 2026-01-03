#!/usr/bin/env python3
"""
Script: download_mcc_icgc.py
Descripción: Descarga el Mapa de Cobertes del Sòl de Catalunya (MCC) del ICGC
             para un área de interés específica
Uso: python scripts/download_mcc_icgc.py <aoi_geojson> <output_file>
"""

import os
import sys
import logging
import geopandas as gpd
import requests
from urllib.parse import urlencode
from pathlib import Path

# Agregar directorio scripts al path si es necesario
sys.path.insert(0, str(Path(__file__).parent))
from logging_utils import LoggerConfig

# Configurar logger
logger = LoggerConfig.setup_script_logger(
    script_name='download_mcc_icgc',
    level=logging.INFO,
    console_level=logging.INFO
)


def download_mcc_wfs(bbox, output_file, epsg=25831):
    """
    Descarga MCC usando el servicio WFS del ICGC
    
    Args:
        bbox: Tuple (minx, miny, maxx, maxy)
        output_file: Ruta de salida
        epsg: Sistema de referencia (default: 25831 - ETRS89 UTM 31N)
    """
    logger.info("Descargando MCC desde ICGC...")
    
    minx, miny, maxx, maxy = bbox
    
    # Lista de URLs a probar (el servicio del ICGC cambia frecuentemente)
    wfs_urls = [
        "https://geoserveis.icgc.cat/icgc_mcc5/wfs/service",
        "https://geoserveis.icgc.cat/servei/catalunya/mcc-v5s/wfs",
        "https://geoserveis.icgc.cat/servei/catalunya/cobertes-sol/wfs"
    ]
    
    for wfs_url in wfs_urls:
        logger.info(f"\n  Probando: {wfs_url}")
        
        # Parámetros WFS GetFeature
        params = {
            'service': 'WFS',
            'version': '2.0.0',
            'request': 'GetFeature',
            'typeName': 'cobertes:MCC5',  # También puede ser mcc:poligons
            'bbox': f"{minx},{miny},{maxx},{maxy}",
            'srsName': f'EPSG:{epsg}',
            'outputFormat': 'application/json'
        }
        
        url = f"{wfs_url}?{urlencode(params)}"
        
        try:
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            
            # Guardar GeoJSON
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(response.text)
            
            # Validar
            gdf = gpd.read_file(output_file)
            if len(gdf) > 0:
                logger.info(f"  ✓ Descargado: {len(gdf)} polígonos")
                logger.info(f"  ✓ Guardado: {output_file}")
                return True
            else:
                logger.warning(f"  ⚠ Respuesta vacía")
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"  ✗ Error: {e}")
            continue
        except Exception as e:
            logger.warning(f"  ✗ Error: {e}")
            continue
    
    logger.error("  ✗ No se pudo descargar desde ninguna URL WFS")
    return False


def main():
    if len(sys.argv) < 3:
        print("Uso: python download_mcc_icgc.py <aoi_geojson> <output_file>")
        print("\nEjemplo:")
        print("  python scripts/download_mcc_icgc.py aoi/figueres.geojson data/mcc_figueres.geojson")
        return 1
    
    aoi_file = sys.argv[1]
    output_file = sys.argv[2]
    
    LoggerConfig.log_section(logger, "DESCARGA MCC - Mapa de Cobertes del Sòl de Catalunya")
    
    # Verificar archivo AOI
    if not os.path.exists(aoi_file):
        logger.error(f"No existe: {aoi_file}")
        return 1
    
    # Leer AOI
    logger.info(f"\nLeyendo AOI: {aoi_file}")
    aoi = gpd.read_file(aoi_file)
    
    # Asegurar EPSG:25831
    if aoi.crs != 'EPSG:25831':
        logger.info(f"  Reproyectando de {aoi.crs} a EPSG:25831")
        aoi = aoi.to_crs('EPSG:25831')
    
    # Obtener bounds
    bounds = aoi.total_bounds  # minx, miny, maxx, maxy
    logger.info(f"  BBox: {bounds}")
    
    # Crear directorio de salida
    os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
    
    # Descargar
    success = download_mcc_wfs(bounds, output_file)
    
    if not success:
        logger.error("\nError en la descarga automática.")
        logger.info("\nAlternativas:")
        logger.info("1. Descargar manualmente desde:")
        logger.info("   https://www.icgc.cat/ca/Descarregues/Cobertes-del-sol/Mapa-de-cobertes-del-sol-de-Catalunya")
        logger.info("2. Usar el visor ICGC:")
        logger.info("   https://www.instamaps.cat/")
        return 1
    
    logger.info("\n✓ Descarga completada con éxito")
    return 0


if __name__ == '__main__':
    sys.exit(main())
