#!/usr/bin/env python3
"""
Script: crop_insar_to_aoi.py
Descripción: Recorta productos InSAR al AOI manteniendo resolución nativa
             Optimizado para detección de fugas en tuberías de distribución de agua
Uso: python scripts/crop_insar_to_aoi.py [workspace_dir]
"""

import os
import sys
import glob
import logging
import rasterio
from rasterio.mask import mask
from shapely import wkt
from shapely.geometry import mapping
from pathlib import Path

# Agregar directorio scripts al path si es necesario
sys.path.insert(0, str(Path(__file__).parent))
from logging_utils import LoggerConfig

# Logger se configurará en main() después de conocer el workspace
logger = None

def load_config(config_file="config.txt"):
    """Cargar configuración desde config.txt"""
    config = {}
    
    if not os.path.exists(config_file):
        logger.warning(f"No existe {config_file}")
        return config
    
    with open(config_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip().strip('"')
    
    return config


def crop_insar_product(dim_file, aoi_wkt, output_dir):
    """
    Recorta un producto InSAR .dim al AOI
    
    Args:
        dim_file: Ruta al archivo .dim
        aoi_wkt: WKT string del AOI
        output_dir: Directorio de salida
        
    Returns:
        str: Ruta al producto recortado o None si falla
    """
    try:
        basename = os.path.basename(dim_file).replace('.dim', '')
        output_file = os.path.join(output_dir, f"{basename}_cropped.tif")
        
        # Verificar si ya está procesado
        if os.path.exists(output_file):
            logger.info(f"  ✓ Ya recortado: {basename}")
            return output_file
        
        data_dir = dim_file.replace('.dim', '.data')
        
        # Buscar banda de coherencia
        coh_file = None
        for root, dirs, files in os.walk(data_dir):
            for f in files:
                if 'coh' in f.lower() and f.endswith('.img'):
                    coh_file = os.path.join(root, f)
                    break
            if coh_file:
                break
        
        if not coh_file:
            logger.warning(f"  No se encontró banda de coherencia en {basename}")
            return None
        
        # Cargar AOI como geometría
        aoi_geom = wkt.loads(aoi_wkt)
        geoms = [mapping(aoi_geom)]
        
        # Abrir el raster y hacer crop
        with rasterio.open(coh_file) as src:
            if src.crs is None:
                logger.warning(f"  Producto sin CRS: {basename}")
                return None
            
            # Hacer el crop
            out_image, out_transform = mask(src, geoms, crop=True, all_touched=True)
            out_meta = src.meta.copy()
            
            # Actualizar metadata (mantener resolución nativa para detección de fugas)
            out_meta.update({
                "driver": "GTiff",
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "compress": "lzw",
                "tiled": True,
                "blockxsize": 512,
                "blockysize": 512
            })
            
            # Guardar el producto recortado
            output_file = os.path.join(output_dir, f"{basename}_cropped.tif")
            with rasterio.open(output_file, "w", **out_meta) as dest:
                dest.write(out_image)
            
            logger.info(f"  ✓ Recortado: {basename} ({src.shape[0]}x{src.shape[1]} → {out_image.shape[1]}x{out_image.shape[2]})")
            return output_file
            
    except Exception as e:
        logger.error(f"  ✗ Error recortando {basename}: {e}")
        return None


def main():
    # Determinar directorio de trabajo
    if len(sys.argv) > 1:
        workspace_dir = sys.argv[1]
    else:
        workspace_dir = os.getcwd()

    # Configurar logger con workspace
    global logger
    logger = LoggerConfig.setup_aoi_logger(
        aoi_project_dir=workspace_dir,
        log_name='crop_insar_to_aoi',
        level=logging.INFO,
        console_level=logging.WARNING
    )

    LoggerConfig.log_section(logger, "RECORTE DE PRODUCTOS InSAR AL AOI")
    logger.info(f"Workspace: {workspace_dir}")
    
    # Cargar configuración
    config_file = os.path.join(workspace_dir, "config.txt")
    config = load_config(config_file)
    aoi_wkt = config.get('AOI')
    
    if not aoi_wkt:
        logger.error("No se encontró AOI en config.txt")
        return 1
    
    logger.info(f"AOI: {aoi_wkt[:60]}...")

    # Buscar productos InSAR en insar/short/ y insar/long/
    insar_base_dir = os.path.join(workspace_dir, 'insar')
    if not os.path.isdir(insar_base_dir):
        logger.error(f"No existe directorio: {insar_base_dir}")
        return 1

    # Buscar en ambos subdirectorios
    insar_products = []
    for subdir in ['short', 'long']:
        subdir_path = os.path.join(insar_base_dir, subdir)
        if os.path.isdir(subdir_path):
            products = glob.glob(os.path.join(subdir_path, 'Ifg_*.dim'))
            insar_products.extend(products)

    insar_products = sorted(insar_products)

    if not insar_products:
        logger.warning(f"No se encontraron productos InSAR en {insar_base_dir}/{{short,long}}/")
        return 1

    logger.info(f"\nEncontrados {len(insar_products)} productos InSAR")

    # Crear directorio de salida
    output_dir = os.path.join(workspace_dir, 'insar/cropped')
    os.makedirs(output_dir, exist_ok=True)
    
    # Procesar cada producto
    cropped = 0
    failed = 0
    
    for i, dim_file in enumerate(insar_products, 1):
        logger.info(f"\n[{i}/{len(insar_products)}] {os.path.basename(dim_file)}")
        result = crop_insar_product(dim_file, aoi_wkt, output_dir)
        if result:
            cropped += 1
        else:
            failed += 1
    
    # Resumen
    logger.info("")
    logger.info("=" * 80)
    logger.info("RESUMEN")
    logger.info("=" * 80)
    logger.info(f"Productos recortados: {cropped}/{len(insar_products)}")
    logger.info(f"Fallidos: {failed}")
    logger.info(f"Salida: {output_dir}")
    logger.info("")
    
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
