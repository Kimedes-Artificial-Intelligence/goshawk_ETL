#!/usr/bin/env python3
"""
Script: process_sentinel2_msavi.py
Descripción: Procesa imágenes Sentinel-2 L2A y calcula el índice MSAVI
            (Modified Soil Adjusted Vegetation Index) para inversión de 
            humedad del suelo.

MSAVI = (2 * NIR + 1 - sqrt((2 * NIR + 1)² - 8 * (NIR - RED))) / 2

Bandas Sentinel-2 requeridas:
  - B4 (RED): 665 nm, 10m
  - B8 (NIR): 842 nm, 10m
  - SCL: Scene Classification (máscara de nubes)

Uso:
  python scripts/process_sentinel2_msavi.py --date 20240315 --aoi-geojson aoi/arenys_munt.geojson
  python scripts/process_sentinel2_msavi.py --s2-product S2A_MSIL2A_20240315T105311_...
"""

import os
import sys
import argparse
import glob
import numpy as np
import rasterio
from rasterio.warp import reproject
from rasterio.enums import Resampling
from rasterio.mask import mask
from datetime import datetime, timedelta
import json

# Agregar directorio de scripts al path
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from logging_utils import LoggerConfig

logger = None


def find_sentinel2_products(data_dir, target_date=None, date_window=2):
    """
    Busca productos Sentinel-2 L2A descargados
    
    Args:
        data_dir: Directorio base de datos
        target_date: Fecha objetivo (datetime o string YYYYMMDD)
        date_window: Ventana de días para buscar (±days)
    
    Returns:
        list: Lista de rutas a productos .SAFE encontrados
    """
    s2_dir = os.path.join(data_dir, 'sentinel2_l2a')
    
    if not os.path.exists(s2_dir):
        logger.warning(f"No existe directorio Sentinel-2: {s2_dir}")
        return []
    
    # Buscar todos los productos .SAFE
    pattern = os.path.join(s2_dir, 'S2*_MSIL2A_*.SAFE')
    all_products = glob.glob(pattern)
    
    if not all_products:
        logger.warning(f"No se encontraron productos Sentinel-2 en {s2_dir}")
        return []
    
    # Si no hay fecha objetivo, devolver todos
    if target_date is None:
        return sorted(all_products)
    
    # Convertir target_date a datetime si es string
    if isinstance(target_date, str):
        target_date = datetime.strptime(target_date, '%Y%m%d')
    
    # Filtrar por ventana temporal
    filtered_products = []
    for product in all_products:
        # Extraer fecha del nombre: S2A_MSIL2A_20240315T105311_...
        basename = os.path.basename(product)
        try:
            date_str = basename.split('_')[2][:8]  # 20240315
            product_date = datetime.strptime(date_str, '%Y%m%d')
            
            # Verificar si está dentro de la ventana
            delta = abs((product_date - target_date).days)
            if delta <= date_window:
                filtered_products.append(product)
        except Exception as e:
            logger.warning(f"Error parseando fecha de {basename}: {e}")
            continue
    
    return sorted(filtered_products)


def find_band_file(product_path, band_name):
    """
    Busca un archivo de banda específico dentro de un producto Sentinel-2
    
    Args:
        product_path: Ruta al producto .SAFE
        band_name: Nombre de la banda (ej: 'B04', 'B08', 'SCL')
    
    Returns:
        str: Ruta al archivo JP2 de la banda o None
    """
    # Buscar en estructura Sentinel-2:
    # S2X_MSIL2A_*.SAFE/GRANULE/L2A_*/IMG_DATA/R10m/*_B0X_10m.jp2
    
    img_data_dirs = glob.glob(os.path.join(product_path, 'GRANULE', '*', 'IMG_DATA'))
    
    for img_dir in img_data_dirs:
        # Buscar en resolución 10m para B04 y B08
        if band_name in ['B04', 'B08']:
            r10m_dir = os.path.join(img_dir, 'R10m')
            if os.path.exists(r10m_dir):
                pattern = os.path.join(r10m_dir, f'*_{band_name}_10m.jp2')
                matches = glob.glob(pattern)
                if matches:
                    return matches[0]
        
        # SCL está en R20m
        elif band_name == 'SCL':
            r20m_dir = os.path.join(img_dir, 'R20m')
            if os.path.exists(r20m_dir):
                pattern = os.path.join(r20m_dir, f'*_{band_name}_20m.jp2')
                matches = glob.glob(pattern)
                if matches:
                    return matches[0]
    
    return None


def read_and_resample_band(band_file, target_transform=None, target_shape=None, 
                           target_crs=None, resampling_method=Resampling.bilinear):
    """
    Lee una banda y opcionalmente la remuestrea a una resolución/proyección objetivo
    
    Args:
        band_file: Ruta al archivo de banda
        target_transform: Transformación objetivo (para reproyección)
        target_shape: Shape objetivo (height, width)
        target_crs: CRS objetivo
        resampling_method: Método de remuestreo
    
    Returns:
        tuple: (data, profile)
    """
    with rasterio.open(band_file) as src:
        if target_transform is None or target_shape is None or target_crs is None:
            # Sin reproyección, leer directamente
            data = src.read(1)
            profile = src.profile.copy()
        else:
            # Reproyectar
            data = np.empty(target_shape, dtype=src.dtypes[0])
            reproject(
                source=rasterio.band(src, 1),
                destination=data,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=target_transform,
                dst_crs=target_crs,
                resampling=resampling_method
            )
            
            profile = src.profile.copy()
            profile.update({
                'transform': target_transform,
                'width': target_shape[1],
                'height': target_shape[0],
                'crs': target_crs
            })
    
    return data, profile


def calculate_msavi(red, nir):
    """
    Calcula el índice MSAVI (Modified Soil Adjusted Vegetation Index)
    
    MSAVI = (2 * NIR + 1 - sqrt((2 * NIR + 1)² - 8 * (NIR - RED))) / 2
    
    Args:
        red: Array numpy con valores de banda roja (B4)
        nir: Array numpy con valores de banda NIR (B8)
    
    Returns:
        Array numpy con valores MSAVI
    """
    # Convertir a float para evitar problemas con operaciones
    red = red.astype(np.float32)
    nir = nir.astype(np.float32)
    
    # Normalizar valores (Sentinel-2 L2A está en reflectancia * 10000)
    # Dividir por 10000 para tener reflectancia [0, 1]
    red = red / 10000.0
    nir = nir / 10000.0
    
    # Aplicar fórmula MSAVI
    with np.errstate(invalid='ignore', divide='ignore'):
        term1 = 2 * nir + 1
        term2 = np.sqrt(term1**2 - 8 * (nir - red))
        msavi = (term1 - term2) / 2.0
    
    # Manejar valores no válidos
    msavi = np.where(np.isfinite(msavi), msavi, np.nan)
    
    # Clip a rango válido [0, 1] (aunque MSAVI teóricamente puede ser negativo)
    msavi = np.clip(msavi, -1, 1)
    
    return msavi.astype(np.float32)


def apply_cloud_mask(data, scl_data, scl_transform, data_transform, data_shape):
    """
    Aplica máscara de nubes usando la banda SCL de Sentinel-2
    
    SCL valores:
      0: NO_DATA
      1: SATURATED_OR_DEFECTIVE
      2: DARK_AREA_PIXELS (sombra)
      3: CLOUD_SHADOWS
      4: VEGETATION
      5: NOT_VEGETATED
      6: WATER
      7: UNCLASSIFIED
      8: CLOUD_MEDIUM_PROBABILITY
      9: CLOUD_HIGH_PROBABILITY
     10: THIN_CIRRUS
     11: SNOW
    
    Args:
        data: Array de datos a enmascarar
        scl_data: Array de Scene Classification Layer
        scl_transform: Transform de SCL (20m)
        data_transform: Transform de datos (10m)
        data_shape: Shape de datos objetivo
    
    Returns:
        Array enmascarado
    """
    # Remuestrear SCL de 20m a 10m para coincidir con data
    scl_resampled = np.empty(data_shape, dtype=scl_data.dtype)
    
    reproject(
        source=scl_data,
        destination=scl_resampled,
        src_transform=scl_transform,
        dst_transform=data_transform,
        resampling=Resampling.nearest  # Usar nearest para clasificación
    )
    
    # Crear máscara: True para píxeles válidos
    # Rechazar: nubes (8, 9), cirrus (10), sombras (3), saturados (1), no data (0)
    invalid_classes = [0, 1, 3, 8, 9, 10]
    valid_mask = ~np.isin(scl_resampled, invalid_classes)
    
    # Aplicar máscara
    masked_data = np.where(valid_mask, data, np.nan)
    
    return masked_data


def crop_to_aoi(data, profile, aoi_geojson):
    """
    Recorta un raster al área de interés definida por un GeoJSON
    
    Args:
        data: Array numpy de datos
        profile: Profile de rasterio
        aoi_geojson: Ruta al archivo GeoJSON con el AOI
    
    Returns:
        tuple: (cropped_data, cropped_profile)
    """
    from shapely.geometry import shape
    
    # Leer geometría del AOI
    with open(aoi_geojson, 'r') as f:
        geojson = json.load(f)
    
    # Extraer geometría
    if 'features' in geojson:
        geometries = [shape(feature['geometry']) for feature in geojson['features']]
    else:
        geometries = [shape(geojson['geometry'])]
    
    # Crear un dataset temporal en memoria
    from rasterio.io import MemoryFile
    
    with MemoryFile() as memfile:
        with memfile.open(**profile) as dataset:
            dataset.write(data, 1)
            
            # Aplicar máscara
            out_image, out_transform = mask(dataset, geometries, crop=True, nodata=np.nan)
            
            # Actualizar profile
            out_profile = profile.copy()
            out_profile.update({
                'height': out_image.shape[1],
                'width': out_image.shape[2],
                'transform': out_transform
            })
    
    return out_image[0], out_profile


def process_sentinel2_to_msavi(product_path, output_path, aoi_geojson=None,
                                apply_cloud_masking=True):
    """
    Procesa un producto Sentinel-2 L2A y calcula MSAVI
    
    Args:
        product_path: Ruta al producto .SAFE
        output_path: Ruta donde guardar el MSAVI GeoTIFF
        aoi_geojson: Ruta al GeoJSON del AOI (opcional, para recorte)
        apply_cloud_masking: Si aplicar máscara de nubes (default: True)
    
    Returns:
        bool: True si exitoso
    """
    logger.info(f"Procesando: {os.path.basename(product_path)}")
    
    # 1. Buscar bandas B04, B08 y SCL
    logger.info("  Buscando bandas...")
    b04_file = find_band_file(product_path, 'B04')
    b08_file = find_band_file(product_path, 'B08')
    scl_file = find_band_file(product_path, 'SCL')
    
    if not b04_file:
        logger.error(f"  ✗ No se encontró banda B04 (RED)")
        return False
    
    if not b08_file:
        logger.error(f"  ✗ No se encontró banda B08 (NIR)")
        return False
    
    logger.info(f"  ✓ B04 (RED): {os.path.basename(b04_file)}")
    logger.info(f"  ✓ B08 (NIR): {os.path.basename(b08_file)}")
    
    if scl_file and apply_cloud_masking:
        logger.info(f"  ✓ SCL (Cloud mask): {os.path.basename(scl_file)}")
    elif apply_cloud_masking:
        logger.warning(f"  ⚠ No se encontró banda SCL - sin máscara de nubes")
    
    # 2. Leer bandas
    logger.info("  Leyendo bandas...")
    red_data, red_profile = read_and_resample_band(b04_file)
    nir_data, nir_profile = read_and_resample_band(b08_file)
    
    # Verificar que ambas bandas tienen la misma forma
    if red_data.shape != nir_data.shape:
        logger.error(f"  ✗ Las bandas tienen diferentes dimensiones: RED {red_data.shape} vs NIR {nir_data.shape}")
        return False
    
    # 3. Calcular MSAVI
    logger.info("  Calculando MSAVI...")
    msavi = calculate_msavi(red_data, nir_data)
    
    # 4. Aplicar máscara de nubes si está disponible
    if scl_file and apply_cloud_masking:
        logger.info("  Aplicando máscara de nubes...")
        with rasterio.open(scl_file) as scl_src:
            scl_data = scl_src.read(1)
            scl_transform = scl_src.transform
        
        msavi = apply_cloud_mask(msavi, scl_data, scl_transform, 
                                red_profile['transform'], red_data.shape)
        
        # Reportar cobertura de nubes
        valid_pixels = np.sum(np.isfinite(msavi))
        total_pixels = msavi.size
        cloud_coverage = (1 - valid_pixels / total_pixels) * 100
        logger.info(f"  Cobertura de nubes: {cloud_coverage:.1f}%")
    
    # 5. Recortar al AOI si se especifica
    if aoi_geojson and os.path.exists(aoi_geojson):
        logger.info("  Recortando al AOI...")
        msavi, red_profile = crop_to_aoi(msavi, red_profile, aoi_geojson)
    
    # 6. Guardar resultado
    logger.info(f"  Guardando MSAVI: {output_path}")
    
    # Preparar profile para salida
    output_profile = red_profile.copy()
    output_profile.update({
        'dtype': 'float32',
        'driver': 'GTiff',
        'compress': 'lzw',
        'tiled': True,
        'blockxsize': 256,
        'blockysize': 256,
        'nodata': np.nan
    })
    
    # Crear directorio si no existe
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    
    # Guardar
    with rasterio.open(output_path, 'w', **output_profile) as dst:
        dst.write(msavi, 1)
        dst.set_band_description(1, 'MSAVI')
        
        # Agregar metadatos
        dst.update_tags(
            MSAVI_FORMULA='(2*NIR+1-sqrt((2*NIR+1)^2-8*(NIR-RED)))/2',
            SOURCE_PRODUCT=os.path.basename(product_path),
            PROCESSING_DATE=datetime.now().isoformat()
        )
    
    # Validar resultado
    valid_pixels = np.sum(np.isfinite(msavi))
    if valid_pixels == 0:
        logger.error(f"  ✗ MSAVI no contiene datos válidos")
        return False
    
    logger.info(f"  ✓ MSAVI guardado exitosamente ({valid_pixels} píxeles válidos)")
    
    # Mostrar estadísticas
    msavi_valid = msavi[np.isfinite(msavi)]
    if len(msavi_valid) > 0:
        logger.info(f"  Estadísticas MSAVI:")
        logger.info(f"    Min:    {np.min(msavi_valid):.3f}")
        logger.info(f"    Max:    {np.max(msavi_valid):.3f}")
        logger.info(f"    Media:  {np.mean(msavi_valid):.3f}")
        logger.info(f"    Mediana:{np.median(msavi_valid):.3f}")
    
    return True


def main():
    global logger
    
    parser = argparse.ArgumentParser(
        description='Procesa Sentinel-2 L2A y calcula MSAVI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--date', type=str,
                       help='Fecha objetivo (YYYYMMDD) para buscar productos Sentinel-2')
    parser.add_argument('--date-window', type=int, default=2,
                       help='Ventana de días para buscar productos (±days, default: 2)')
    parser.add_argument('--s2-product', type=str,
                       help='Ruta directa a producto Sentinel-2 .SAFE')
    parser.add_argument('--data-dir', type=str,
                       help='Directorio base de datos (default: data/)')
    parser.add_argument('--output', type=str,
                       help='Ruta de salida para MSAVI GeoTIFF')
    parser.add_argument('--aoi-geojson', type=str,
                       help='GeoJSON del AOI para recorte')
    parser.add_argument('--no-cloud-mask', action='store_true',
                       help='No aplicar máscara de nubes')
    
    args = parser.parse_args()
    
    # Configurar logger
    logger = LoggerConfig.setup_script_logger('process_sentinel2_msavi')
    
    logger.info("="*80)
    logger.info("PROCESAMIENTO SENTINEL-2 → MSAVI")
    logger.info("="*80)
    
    # Determinar directorio de datos
    data_dir = args.data_dir or os.path.join(os.path.dirname(script_dir), 'data')
    
    # Buscar productos Sentinel-2
    products = []
    
    if args.s2_product:
        # Producto especificado directamente
        if os.path.exists(args.s2_product):
            products = [args.s2_product]
        else:
            logger.error(f"Producto no encontrado: {args.s2_product}")
            return 1
    elif args.date:
        # Buscar por fecha
        products = find_sentinel2_products(data_dir, args.date, args.date_window)
    else:
        # Buscar todos los productos disponibles
        products = find_sentinel2_products(data_dir)
    
    if not products:
        logger.error("No se encontraron productos Sentinel-2")
        logger.info("\nAsegúrate de haber descargado productos Sentinel-2 L2A con:")
        logger.info("  python scripts/download_copernicus.py --collection SENTINEL-2 --product-type L2A")
        return 1
    
    logger.info(f"\nEncontrados {len(products)} producto(s) Sentinel-2:")
    for p in products:
        logger.info(f"  - {os.path.basename(p)}")
    
    # Procesar cada producto
    success_count = 0
    for product in products:
        # Determinar ruta de salida
        if args.output:
            output_path = args.output
        else:
            # Extraer fecha del producto para nombre de salida
            basename = os.path.basename(product)
            date_str = basename.split('_')[2][:8]  # YYYYMMDD
            output_dir = os.path.join(data_dir, 'sentinel2_msavi')
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f'MSAVI_{date_str}.tif')
        
        # Procesar
        if process_sentinel2_to_msavi(
            product, 
            output_path,
            aoi_geojson=args.aoi_geojson,
            apply_cloud_masking=not args.no_cloud_mask
        ):
            success_count += 1
        
        logger.info("")
    
    # Resumen
    logger.info("="*80)
    logger.info(f"Procesados exitosamente: {success_count}/{len(products)}")
    logger.info("="*80)
    
    return 0 if success_count > 0 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        if logger:
            logger.warning("\nInterrumpido por el usuario")
        else:
            print("\nInterrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        if logger:
            logger.error(f"ERROR: {e}", exc_info=True)
        else:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(1)
