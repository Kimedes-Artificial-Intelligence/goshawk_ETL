#!/usr/bin/env python3
"""
Script: crop_to_urban_soil.py
Descripción: Recorta productos InSAR finales solo al suelo urbano usando
             el Mapa de Cobertes del Sòl de Catalunya (ICGC)
Uso: python scripts/crop_to_urban_soil.py <workspace_dir> [--mcc-file <path>]
"""

import os
import sys
import glob
import logging
import argparse
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from shapely.geometry import mapping
from pathlib import Path

# Agregar directorio scripts al path si es necesario
sys.path.insert(0, str(Path(__file__).parent))
from logging_utils import LoggerConfig

# Logger se configurará en main() después de conocer el workspace
logger = None

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


def download_mcc_data(aoi_bounds, output_file):
    """
    Intenta descargar el MCC del ICGC para el área de interés
    
    Args:
        aoi_bounds: Bounding box (minx, miny, maxx, maxy) en EPSG:25831
        output_file: Ruta donde guardar el GeoJSON
        
    Returns:
        str: Ruta al archivo descargado o None si falla
    """
    logger.info("Intentando descargar Mapa de Cobertes del Sòl (MCC) del ICGC...")
    
    minx, miny, maxx, maxy = aoi_bounds
    
    # URLs posibles del servicio WFS (el ICGC cambia frecuentemente)
    wfs_urls = [
        "https://geoserveis.icgc.cat/icgc_mcc5/wfs/service",
        "https://geoserveis.icgc.cat/servei/catalunya/mcc-v5s/wfs",
        "https://geoserveis.icgc.cat/servei/catalunya/cobertes-sol/wfs"
    ]
    
    import requests
    from urllib.parse import urlencode
    
    for wfs_url in wfs_urls:
        try:
            # Parámetros WFS GetFeature
            params = {
                'service': 'WFS',
                'version': '2.0.0',
                'request': 'GetFeature',
                'typeName': 'cobertes:MCC5',
                'bbox': f"{minx},{miny},{maxx},{maxy}",
                'srsName': 'EPSG:25831',
                'outputFormat': 'application/json'
            }
            
            url = f"{wfs_url}?{urlencode(params)}"
            logger.info(f"  Probando: {wfs_url}")
            
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            
            # Guardar y validar
            with open(output_file, 'w') as f:
                f.write(response.text)
            
            gdf = gpd.read_file(output_file)
            if len(gdf) > 0:
                logger.info(f"  ✓ MCC descargado: {len(gdf)} polígonos")
                return output_file
            
        except Exception as e:
            logger.debug(f"  ✗ Fallo: {e}")
            continue
    
    logger.warning("  ⚠ No se pudo descargar MCC automáticamente")
    logger.info("\n  📥 DESCARGA MANUAL:")
    logger.info("  1. Ir a: https://www.icgc.cat/ca/Descarregues/Cobertes-del-sol")
    logger.info("  2. Descargar 'Mapa de cobertes del sòl de Catalunya' (versión más reciente)")
    logger.info("  3. Descomprimir y convertir a GeoJSON si es necesario")
    logger.info("  4. Ejecutar de nuevo con: --mcc-file <ruta_al_archivo>")
    return None


def filter_urban_areas(mcc_file, aoi_wkt=None):
    """
    Filtra solo las áreas urbanas del MCC
    
    Args:
        mcc_file: Ruta al archivo GeoJSON/Shapefile del MCC
        aoi_wkt: WKT del área de interés para filtrar (opcional)
        
    Returns:
        GeoDataFrame: Polígonos de suelo urbano
    """
    logger.info("Cargando máscara urbana...")
    
    # Leer archivo
    mcc = gpd.read_file(mcc_file)
    logger.info(f"  Total polígonos: {len(mcc)}")
    
    # Buscar columna de código de cobertura
    code_col = None
    for col in ['nivell_2', 'codi', 'CODI', 'codigo', 'CODIGO', 'code', 'CODE']:
        if col in mcc.columns:
            code_col = col
            break
    
    # Si no hay columna de código, asumir que ya está filtrado
    if not code_col:
        logger.info("  ℹ Archivo sin columna de código - asumiendo máscara urbana ya filtrada")
        urban = mcc
    else:
        # Filtrar códigos urbanos usando startswith
        urban_mask = False
        for code in URBAN_CODES:
            mask = mcc[code_col].astype(str).str.startswith(code)
            urban_mask = urban_mask | mask
        urban = mcc[urban_mask].copy()
        logger.info(f"  Polígonos urbanos: {len(urban)} ({len(urban)/len(mcc)*100:.1f}%)")
    
    # Filtrar por AOI si se proporciona
    if aoi_wkt and len(urban) > 1:
        from shapely import wkt
        aoi_geom = wkt.loads(aoi_wkt)
        aoi_gdf = gpd.GeoDataFrame({'id': [1]}, geometry=[aoi_geom], crs=mcc.crs)
        urban = gpd.overlay(urban, aoi_gdf, how='intersection')
        logger.info(f"  Tras intersección con AOI: {len(urban)} polígonos")
    
    if len(urban) == 0:
        logger.warning("  ⚠ No se encontraron áreas urbanas")
        return None
    
    # Disolver en una sola geometría para el crop
    urban_dissolved = urban.dissolve()
    logger.info(f"  ✓ Geometría urbana creada")
    
    return urban_dissolved


def crop_raster_to_urban(raster_file, urban_geom, output_file):
    """
    Recorta un raster al suelo urbano
    
    Args:
        raster_file: Ruta al raster de entrada
        urban_geom: GeoDataFrame con geometría urbana
        output_file: Ruta al raster de salida
        
    Returns:
        bool: True si tuvo éxito
    """
    try:
        with rasterio.open(raster_file) as src:
            # Reproyectar geometría urbana al CRS del raster si es necesario
            if urban_geom.crs != src.crs:
                urban_geom = urban_geom.to_crs(src.crs)
            
            # Preparar geometrías para mask
            geoms = [mapping(geom) for geom in urban_geom.geometry]
            
            # Hacer el crop
            out_image, out_transform = mask(src, geoms, crop=True, all_touched=False)
            out_meta = src.meta.copy()
            
            # Actualizar metadata
            out_meta.update({
                "driver": "GTiff",
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "compress": "lzw",
                "tiled": True,
                "blockxsize": 256,
                "blockysize": 256
            })
            
            # Guardar
            with rasterio.open(output_file, "w", **out_meta) as dest:
                dest.write(out_image)
            
            logger.info(f"  ✓ Recortado: {os.path.basename(raster_file)} "
                       f"({src.shape[0]}x{src.shape[1]} → {out_image.shape[1]}x{out_image.shape[2]})")
            return True
            
    except Exception as e:
        logger.error(f"  ✗ Error recortando {os.path.basename(raster_file)}: {e}")
        return False


def load_config(config_file="config.txt"):
    """Cargar configuración desde config.txt"""
    config = {}
    
    if not os.path.exists(config_file):
        return config
    
    with open(config_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip().strip('"')
    
    return config


def main():
    parser = argparse.ArgumentParser(
        description='Recorta productos InSAR al suelo urbano usando MCC del ICGC'
    )
    parser.add_argument('workspace', help='Directorio de trabajo (municipio)')
    parser.add_argument('--mcc-file', help='Archivo MCC existente (GeoJSON/Shapefile)')
    parser.add_argument('--output-suffix', default='_urban', 
                       help='Sufijo para archivos de salida (default: _urban)')
    
    args = parser.parse_args()

    workspace_dir = args.workspace

    # Configurar logger con workspace
    global logger
    logger = LoggerConfig.setup_aoi_logger(
        aoi_project_dir=workspace_dir,
        log_name='crop_to_urban_soil',
        level=logging.INFO,
        console_level=logging.WARNING
    )

    LoggerConfig.log_section(logger, "RECORTE A SUELO URBANO - MCC ICGC")
    logger.info(f"Workspace: {workspace_dir}")
    
    # Cargar config
    config_file = os.path.join(workspace_dir, "config.txt")
    config = load_config(config_file)
    aoi_wkt = config.get('AOI')
    
    # Buscar productos InSAR finales
    # Pueden estar en diferentes ubicaciones según el workflow
    search_patterns = [
        'fusion/insar/*.tif',
        'fusion/insar/cropped/*.tif',
        'insar_*/fusion/pairs/*/*.tif',  # Todos los productos por par
        'insar_*/fusion/*.tif',          # Productos globales
        'insar_*/fusion/global_*.tif',
        'risk_maps/*.tif'
    ]
    
    insar_products = []
    for pattern in search_patterns:
        full_pattern = os.path.join(workspace_dir, pattern)
        insar_products.extend(glob.glob(full_pattern))
    
    insar_products = sorted(set(insar_products))
    
    if not insar_products:
        logger.error("No se encontraron productos InSAR en el workspace")
        return 1
    
    logger.info(f"\nEncontrados {len(insar_products)} productos InSAR")
    
    # Obtener o descargar MCC
    mcc_file = args.mcc_file
    
    if not mcc_file:
        # Intentar descargar automáticamente
        logger.info("\nNo se especificó archivo MCC, intentando descarga automática...")
        
        if not aoi_wkt:
            logger.error("Se necesita AOI en config.txt para descargar MCC automáticamente")
            return 1
        
        # Calcular bounds del AOI
        from shapely import wkt
        aoi_geom = wkt.loads(aoi_wkt)
        aoi_bounds = aoi_geom.bounds
        
        mcc_file = os.path.join(workspace_dir, 'mcc_data.geojson')
        mcc_file = download_mcc_data(aoi_bounds, mcc_file)
        
        if not mcc_file:
            logger.error("\nPor favor, descarga manualmente el MCC y especifica con --mcc-file")
            logger.info("Descargar desde: https://www.icgc.cat/ca/Descarregues/Cobertes-del-sol")
            return 1
    
    if not os.path.exists(mcc_file):
        logger.error(f"No existe archivo MCC: {mcc_file}")
        return 1
    
    logger.info(f"\nArchivo MCC: {mcc_file}")
    
    # Filtrar áreas urbanas
    urban_geom = filter_urban_areas(mcc_file, aoi_wkt)
    
    if urban_geom is None:
        return 1
    
    # Crear directorio de salida
    output_dir = os.path.join(workspace_dir, 'urban_products')
    os.makedirs(output_dir, exist_ok=True)
    
    # Procesar cada producto
    logger.info(f"\nRecortando {len(insar_products)} productos a suelo urbano...")
    
    success = 0
    failed = 0
    
    for i, raster_file in enumerate(insar_products, 1):
        logger.info(f"\n[{i}/{len(insar_products)}] {os.path.basename(raster_file)}")
        
        # Preservar estructura de directorios relativa al workspace
        rel_path = os.path.relpath(raster_file, workspace_dir)
        dirname = os.path.dirname(rel_path)
        basename = os.path.basename(raster_file)
        name, ext = os.path.splitext(basename)
        
        # Crear subdirectorio si es necesario
        output_subdir = os.path.join(output_dir, dirname)
        os.makedirs(output_subdir, exist_ok=True)
        
        # Generar nombre de salida preservando estructura
        output_file = os.path.join(output_subdir, f"{name}{args.output_suffix}{ext}")
        
        if crop_raster_to_urban(raster_file, urban_geom, output_file):
            success += 1
        else:
            failed += 1
    
    # Resumen
    logger.info("")
    logger.info("=" * 80)
    logger.info("RESUMEN")
    logger.info("=" * 80)
    logger.info(f"Productos recortados: {success}/{len(insar_products)}")
    logger.info(f"Fallidos: {failed}")
    logger.info(f"Salida: {output_dir}")
    logger.info("")
    
    # Guardar geometría urbana para referencia
    urban_output = os.path.join(output_dir, 'urban_mask.geojson')
    try:
        # Crear un GeoDataFrame limpio para guardar
        urban_save = gpd.GeoDataFrame(
            {'name': ['urban_area']},
            geometry=list(urban_geom.geometry),
            crs=urban_geom.crs
        )
        urban_save.to_file(urban_output, driver='GeoJSON')
        logger.info(f"Máscara urbana guardada: {urban_output}")
    except Exception as e:
        logger.warning(f"No se pudo guardar máscara urbana: {e}")
    
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
