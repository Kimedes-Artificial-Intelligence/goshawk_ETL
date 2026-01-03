#!/usr/bin/env python3
"""
Script: crop_polarimetry_to_aoi.py
Descripción: Recorta productos polarimétricos (H/A/Alpha) al AOI manteniendo resolución nativa
Uso: python scripts/crop_polarimetry_to_aoi.py [workspace_dir]
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


def crop_polarimetric_product(dim_file, aoi_wkt, output_dir):
    """
    Recorta un producto polarimétrico .dim al AOI

    Extrae las bandas principales de H-Alpha decomposition:
    - Entropy
    - Alpha
    - Anisotropy

    Args:
        dim_file: Ruta al archivo .dim
        aoi_wkt: WKT string del AOI
        output_dir: Directorio de salida

    Returns:
        str: Ruta al producto recortado o None si falla
    """
    try:
        basename = os.path.basename(dim_file).replace('.dim', '')

        data_dir = dim_file.replace('.dim', '.data')

        if not os.path.exists(data_dir):
            logger.warning(f"  No existe directorio .data: {basename}")
            return None

        # Buscar bandas principales de H-Alpha
        bands_to_crop = {
            'entropy': None,
            'alpha': None,
            'anisotropy': None
        }

        for root, dirs, files in os.walk(data_dir):
            for f in files:
                if not f.endswith('.img'):
                    continue

                f_lower = f.lower()
                if 'entropy' in f_lower and bands_to_crop['entropy'] is None:
                    bands_to_crop['entropy'] = os.path.join(root, f)
                elif 'alpha' in f_lower and bands_to_crop['alpha'] is None:
                    bands_to_crop['alpha'] = os.path.join(root, f)
                elif 'anisotropy' in f_lower and bands_to_crop['anisotropy'] is None:
                    bands_to_crop['anisotropy'] = os.path.join(root, f)

        # Verificar que se encontró al menos una banda
        found_bands = [k for k, v in bands_to_crop.items() if v is not None]

        if not found_bands:
            logger.warning(f"  No se encontraron bandas polariméricas en {basename}")
            return None

        logger.info(f"  Bandas encontradas: {', '.join(found_bands)}")

        # Cargar AOI como geometría
        aoi_geom = wkt.loads(aoi_wkt)
        geoms = [mapping(aoi_geom)]

        # Recortar cada banda encontrada
        cropped_files = []

        for band_name, band_file in bands_to_crop.items():
            if band_file is None:
                continue

            output_file = os.path.join(output_dir, f"{basename}_{band_name}_cropped.tif")

            # Verificar si ya está procesado
            if os.path.exists(output_file):
                logger.debug(f"    ✓ Ya recortado: {band_name}")
                cropped_files.append(output_file)
                continue

            # Abrir el raster y hacer crop
            with rasterio.open(band_file) as src:
                if src.crs is None:
                    logger.warning(f"    Banda sin CRS: {band_name}")
                    continue

                # Hacer el crop
                out_image, out_transform = mask(src, geoms, crop=True, all_touched=True)
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

                logger.debug(f"    ✓ Recortado: {band_name}")
                cropped_files.append(output_file)

        if cropped_files:
            logger.info(f"  ✓ {len(cropped_files)} bandas recortadas: {basename}")
            return cropped_files[0]  # Retornar primera banda como referencia
        else:
            return None

    except Exception as e:
        logger.error(f"  ✗ Error recortando {os.path.basename(dim_file)}: {e}")
        return None


def main():
    global logger

    # Obtener directorio de workspace
    if len(sys.argv) > 1:
        workspace_dir = sys.argv[1]
    else:
        workspace_dir = os.getcwd()

    workspace_dir = os.path.abspath(workspace_dir)

    # Configurar logger
    logger = LoggerConfig.setup_series_logger(
        series_dir=workspace_dir,
        log_name="polarimetry_crop"
    )

    logger.info("=" * 80)
    logger.info("RECORTE DE PRODUCTOS POLARIMÉTRICOS AL AOI")
    logger.info("=" * 80)
    logger.info(f"Workspace: {workspace_dir}")
    logger.info("")

    # Cargar configuración
    config_file = os.path.join(workspace_dir, "config.txt")
    config = load_config(config_file)
    aoi_wkt = config.get('AOI')

    if not aoi_wkt:
        logger.error("No se encontró AOI en config.txt")
        return 1

    logger.info(f"AOI: {aoi_wkt[:60]}...")

    # Buscar productos polarimétricos en polarimetry/
    polarimetry_dir = os.path.join(workspace_dir, 'polarimetry')
    if not os.path.isdir(polarimetry_dir):
        logger.error(f"No existe directorio: {polarimetry_dir}")
        return 1

    # Buscar productos HAAlpha
    polarimetry_products = sorted(glob.glob(os.path.join(polarimetry_dir, '*_HAAlpha.dim')))

    if not polarimetry_products:
        logger.warning(f"No se encontraron productos polarimétricos en {polarimetry_dir}")
        return 1

    logger.info(f"\nEncontrados {len(polarimetry_products)} productos polarimétricos")

    # Crear directorio de salida
    output_dir = os.path.join(workspace_dir, 'polarimetry/cropped')
    os.makedirs(output_dir, exist_ok=True)

    # Procesar cada producto
    cropped = 0
    failed = 0

    for i, dim_file in enumerate(polarimetry_products, 1):
        logger.info(f"\n[{i}/{len(polarimetry_products)}] {os.path.basename(dim_file)}")
        result = crop_polarimetric_product(dim_file, aoi_wkt, output_dir)
        if result:
            cropped += 1
        else:
            failed += 1

    # Resumen
    logger.info("")
    logger.info("=" * 80)
    logger.info("RESUMEN")
    logger.info("=" * 80)
    logger.info(f"Productos recortados: {cropped}/{len(polarimetry_products)}")
    logger.info(f"Fallidos: {failed}")
    logger.info(f"Salida: {output_dir}")
    logger.info("")

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
