#!/usr/bin/env python3
"""
Script: calculate_pair_statistics.py
Descripción: Calcula estadísticas por cada par temporal InSAR
             Para cada par InSAR (master-slave):
             - Coherencia del interferograma

Uso: python scripts/calculate_pair_statistics.py
"""

import os
import sys
import glob
import numpy as np
import rasterio
from rasterio.merge import merge as rasterio_merge
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Importar módulos locales
sys.path.append(os.path.dirname(__file__))
from processing_utils import load_config, extract_date_from_filename
from logging_utils import LoggerConfig

# Logger se configurará según el directorio de trabajo
logger = None

# ==================================================================================
# DEPRECATED: Bandas GRD ya no se usan (solo coherencia InSAR)
# ==================================================================================
# GLCM_BANDS_VV = [...]
# GLCM_BANDS_VH = [...]
# BACKSCATTER_VV = 'Sigma0_VV'
# BACKSCATTER_VH = 'Sigma0_VH'
# DEM_BAND = 'elevation'
# LIA_BAND = 'localIncidenceAngle'
# ==================================================================================


def read_band_from_dim(dim_path, band_pattern):
    """
    Lee una banda específica de un producto BEAM-DIMAP

    Args:
        dim_path: Ruta al archivo .dim
        band_pattern: Patrón de la banda a buscar (ej: 'coh', 'Sigma0_VV')

    Returns:
        tuple: (data, profile) o (None, None) si falla
    """
    data_dir = dim_path.replace('.dim', '.data')

    if not os.path.isdir(data_dir):
        logger.error(f"No existe directorio .data: {data_dir}")
        return None, None

    try:
        # Buscar archivos .img que contengan el patrón de banda
        img_files = glob.glob(os.path.join(data_dir, '*.img'))
        matching_files = [f for f in img_files if band_pattern.lower() in os.path.basename(f).lower()]

        if not matching_files:
            logger.warning(f"No se encontró banda '{band_pattern}' en {data_dir}")
            return None, None

        # Usar el primer archivo que coincida
        img_file = matching_files[0]

        with rasterio.open(img_file) as src:
            data = src.read(1)
            profile = src.profile.copy()
            
            # Limpiar opciones no compatibles con GeoTIFF
            # Usar driver GTiff en lugar de ENVI para evitar warnings
            profile.update({
                'driver': 'GTiff',
                'compress': 'lzw',
                'tiled': True,
                'blockxsize': 256,
                'blockysize': 256
            })
            
            # Eliminar opciones específicas de ENVI
            for key in ['interleave', 'INTERLEAVE']:
                profile.pop(key, None)
            
            return data, profile

    except Exception as e:
        logger.error(f"Error leyendo {dim_path}: {e}")
        return None, None


# ==================================================================================
# FUNCIONES GRD ELIMINADAS
# ==================================================================================
# Las siguientes funciones fueron deprecadas al eliminar el procesamiento GRD:
# - extract_glcm_band()
# - find_grd_for_date()
# - merge_grd_parts()
# - extract_and_save_band()
# - process_grd_date()
# ==================================================================================


def find_msavi_for_date(project_dir, date_str, date_window=2):
    """
    Busca archivo MSAVI para una fecha específica con ventana temporal
    
    Args:
        project_dir: Directorio del proyecto
        date_str: Fecha en formato YYYYMMDD
        date_window: Ventana de días para buscar (±days, default: 2)
    
    Returns:
        Path al archivo MSAVI .tif o None
    """
    from datetime import datetime, timedelta
    
    # Directorio de MSAVI en el proyecto
    msavi_dir = os.path.join(project_dir, 'sentinel2_msavi')
    
    if not os.path.exists(msavi_dir):
        return None
    
    # Convertir fecha objetivo
    try:
        target_date = datetime.strptime(date_str, '%Y%m%d')
    except ValueError:
        return None
    
    # Buscar archivo exacto primero
    exact_file = os.path.join(msavi_dir, f'MSAVI_{date_str}.tif')
    if os.path.exists(exact_file):
        return exact_file
    
    # Buscar dentro de la ventana temporal
    for delta in range(-date_window, date_window + 1):
        check_date = target_date + timedelta(days=delta)
        check_date_str = check_date.strftime('%Y%m%d')
        check_file = os.path.join(msavi_dir, f'MSAVI_{check_date_str}.tif')
        
        if os.path.exists(check_file):
            logger.info(f"      Usando MSAVI de {check_date_str} (±{abs(delta)} días)")
            return check_file
    
    return None


def align_msavi_to_sar(msavi_file, reference_profile):
    """
    Alinea un raster MSAVI a la grilla de un producto SAR de referencia
    
    Args:
        msavi_file: Ruta al archivo MSAVI
        reference_profile: Profile de rasterio del producto SAR de referencia
    
    Returns:
        tuple: (msavi_aligned, profile) o (None, None) si falla
    """
    from rasterio.warp import reproject, Resampling
    
    try:
        with rasterio.open(msavi_file) as src:
            # Crear array de destino con la misma forma que la referencia
            msavi_aligned = np.empty(
                (reference_profile['height'], reference_profile['width']),
                dtype=np.float32
            )
            
            # Reproyectar MSAVI a la grilla SAR
            reproject(
                source=rasterio.band(src, 1),
                destination=msavi_aligned,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=reference_profile['transform'],
                dst_crs=reference_profile['crs'],
                resampling=Resampling.bilinear
            )
            
            # Actualizar profile
            aligned_profile = reference_profile.copy()
            aligned_profile.update({
                'dtype': 'float32',
                'nodata': np.nan
            })
            
            return msavi_aligned, aligned_profile
            
    except Exception as e:
        logger.error(f"      Error alineando MSAVI: {e}")
        return None, None


def process_insar_pair(ifg_file, sar_dir, output_base_dir, insar_dir):
    """
    Procesa un par InSAR y extrae estadísticas de ambos días

    Extrae:
    - Coherencia del interferograma
    - Backscatter Gamma0_VV y Gamma0_VH (master y slave)
    - 8 texturas GLCM de VV (master y slave)
    - 8 texturas GLCM de VH (master y slave)

    Args:
        ifg_file: Path al interferograma .dim
        sar_dir: Directorio con productos SAR procesados
        output_base_dir: Directorio base para guardar resultados
        insar_dir: Directorio con productos InSAR

    Returns:
        bool: True si exitoso
    """
    basename = os.path.basename(ifg_file)

    # Extraer fechas del nombre: Ifg_YYYYMMDD_YYYYMMDD.dim o Ifg_YYYYMMDD_YYYYMMDD_SUFFIX.dim
    parts = basename.replace('Ifg_', '').replace('.dim', '').split('_')
    if len(parts) < 2:
        logger.error(f"Formato de nombre inválido: {basename}")
        return False

    # Tomar solo las dos primeras partes (fechas), ignorar sufijos adicionales
    master_date = parts[0]
    slave_date = parts[1]

    pair_name = f"pair_{master_date}_{slave_date}"
    pair_dir = os.path.join(output_base_dir, 'pairs', pair_name)
    
    # Verificar si el par ya está completamente procesado
    # Solo verificamos coherencia (productos GRD deprecados)
    required_files = [
        os.path.join(pair_dir, 'coherence.tif')
    ]

    # # Añadir archivos GLCM VV a la verificación
    # for band in GLCM_BANDS_VV:
    #     band_short = band.replace('Gamma0_VV_', '').lower()
    #     required_files.append(os.path.join(pair_dir, f'vv_{band_short}_master.tif'))
    #     required_files.append(os.path.join(pair_dir, f'vv_{band_short}_slave.tif'))
    #
    # # Añadir archivos GLCM VH a la verificación
    # for band in GLCM_BANDS_VH:
    #     band_short = band.replace('Gamma0_VH_', '').lower()
    #     required_files.append(os.path.join(pair_dir, f'vh_{band_short}_master.tif'))
    #     required_files.append(os.path.join(pair_dir, f'vh_{band_short}_slave.tif'))
    
    if all(os.path.exists(f) for f in required_files):
        logger.info(f"\n✓ Par ya procesado: {master_date} → {slave_date}")
        return True
    
    os.makedirs(pair_dir, exist_ok=True)

    logger.info(f"\n{'='*60}")
    logger.info(f"Par: {master_date} → {slave_date}")
    logger.info(f"{'='*60}")

    success = True

    # 1. Extraer coherencia del interferograma
    logger.info("1. Extrayendo coherencia...")

    # Buscar primero en cropped/ (archivos TIF ya recortados)
    cropped_file = os.path.join(insar_dir, 'cropped', f'Ifg_{master_date}_{slave_date}_cropped.tif')

    if os.path.exists(cropped_file):
        logger.info(f"   Usando archivo recortado: {os.path.basename(cropped_file)}")
        try:
            with rasterio.open(cropped_file) as src:
                coh_data = src.read(1)
                coh_profile = src.profile.copy()
                
                # Limpiar opciones no compatibles y usar GTiff
                coh_profile.update({
                    'driver': 'GTiff',
                    'compress': 'lzw',
                    'tiled': True,
                    'blockxsize': 256,
                    'blockysize': 256
                })
                
                # Eliminar opciones específicas de ENVI
                for key in ['interleave', 'INTERLEAVE']:
                    coh_profile.pop(key, None)
                
                # Filtrar valores válidos [0, 1]
                coh_data = np.where((coh_data >= 0) & (coh_data <= 1), coh_data, np.nan)
                
                # VALIDACIÓN: Verificar que hay datos válidos
                valid_mask = np.isfinite(coh_data) & (coh_data != 0)
                valid_pixels = np.sum(valid_mask)
                total_pixels = coh_data.size
                valid_ratio = valid_pixels / total_pixels if total_pixels > 0 else 0
                
                if valid_ratio < 0.10:
                    logger.error(f"   ✗ ADVERTENCIA: Coherencia con datos insuficientes")
                    logger.error(f"      Solo {valid_pixels}/{total_pixels} píxeles válidos ({valid_ratio*100:.1f}%)")
                    logger.error(f"      El subswath seleccionado probablemente NO cubre el AOI")
                    logger.error(f"      RECOMENDACIÓN: Reprocesar con otro subswath (IW2 o IW3)")
                
        except Exception as e:
            logger.error(f"   ✗ Error leyendo archivo recortado: {e}")
            coh_data, coh_profile = None, None
    else:
        logger.info(f"   No encontrado recortado, buscando en .dim...")
        coh_data, coh_profile = read_band_from_dim(ifg_file, 'coh')

    if coh_data is not None:
        output_coh = os.path.join(pair_dir, 'coherence.tif')
        with rasterio.open(output_coh, 'w', **coh_profile) as dst:
            dst.write(coh_data, 1)
        logger.info(f"   ✓ Guardado: {output_coh}")
    else:
        logger.error(f"   ✗ No se pudo extraer coherencia")
        success = False

    # 2. Procesar MSAVI master (para inversión de humedad)
    logger.info(f"2. Procesando MSAVI master ({master_date})...")
    
    # Determinar directorio del proyecto (subir niveles desde pairs/pair_X)
    # Estructura: processing/<proyecto>/insar_*/fusion/pairs/pair_X/
    # Necesitamos: processing/<proyecto>/
    project_dir = Path(output_base_dir).parent.parent.parent
    
    msavi_master_file = find_msavi_for_date(str(project_dir), master_date, date_window=2)
    
    if msavi_master_file:
        logger.info(f"   Encontrado: {os.path.basename(msavi_master_file)}")
        
        # Necesitamos un perfil de referencia - usar coherencia
        reference_profile = None
        coherence_file = os.path.join(pair_dir, 'coherence.tif')
        if os.path.exists(coherence_file):
            with rasterio.open(coherence_file) as src:
                reference_profile = src.profile.copy()
        
        if reference_profile:
            msavi_master_aligned, msavi_profile = align_msavi_to_sar(msavi_master_file, reference_profile)
            
            if msavi_master_aligned is not None:
                output_msavi_master = os.path.join(pair_dir, 'msavi_master.tif')
                with rasterio.open(output_msavi_master, 'w', **msavi_profile) as dst:
                    dst.write(msavi_master_aligned, 1)
                    dst.set_band_description(1, 'MSAVI')
                logger.info(f"   ✓ MSAVI master guardado: {os.path.basename(output_msavi_master)}")
                
                # Mostrar estadísticas
                valid_data = msavi_master_aligned[np.isfinite(msavi_master_aligned)]
                if len(valid_data) > 0:
                    logger.info(f"      Rango: [{np.min(valid_data):.3f}, {np.max(valid_data):.3f}], Media: {np.mean(valid_data):.3f}")
            else:
                logger.warning(f"   ⚠️  No se pudo alinear MSAVI master")
        else:
            logger.warning(f"   ⚠️  No se encontró producto de referencia para alineamiento")
    else:
        logger.debug(f"   ℹ️  No se encontró MSAVI master para fecha {master_date}")
    
    # 3. Procesar MSAVI slave (para inversión de humedad)
    logger.info(f"3. Procesando MSAVI slave ({slave_date})...")
    msavi_slave_file = find_msavi_for_date(str(project_dir), slave_date, date_window=2)
    
    if msavi_slave_file:
        logger.info(f"   Encontrado: {os.path.basename(msavi_slave_file)}")
        
        # Usar coherencia como perfil de referencia
        reference_profile = None
        coherence_file = os.path.join(pair_dir, 'coherence.tif')
        if os.path.exists(coherence_file):
            with rasterio.open(coherence_file) as src:
                reference_profile = src.profile.copy()
        
        if reference_profile:
            msavi_slave_aligned, msavi_profile = align_msavi_to_sar(msavi_slave_file, reference_profile)
            
            if msavi_slave_aligned is not None:
                output_msavi_slave = os.path.join(pair_dir, 'msavi_slave.tif')
                with rasterio.open(output_msavi_slave, 'w', **msavi_profile) as dst:
                    dst.write(msavi_slave_aligned, 1)
                    dst.set_band_description(1, 'MSAVI')
                logger.info(f"   ✓ MSAVI slave guardado: {os.path.basename(output_msavi_slave)}")
                
                # Mostrar estadísticas
                valid_data = msavi_slave_aligned[np.isfinite(msavi_slave_aligned)]
                if len(valid_data) > 0:
                    logger.info(f"      Rango: [{np.min(valid_data):.3f}, {np.max(valid_data):.3f}], Media: {np.mean(valid_data):.3f}")
            else:
                logger.warning(f"   ⚠️  No se pudo alinear MSAVI slave")
        else:
            logger.warning(f"   ⚠️  No se encontró producto de referencia para alineamiento")
    else:
        logger.debug(f"   ℹ️  No se encontró MSAVI slave para fecha {slave_date}")

    # Retornar éxito si tenemos coherencia
    if success:
        logger.info(f"✅ Par {pair_name} completado")
        return True
    else:
        logger.warning(f"⚠️  Par {pair_name} incompleto")
        return False


def main():
    global logger

    # Cargar configuración primero
    config = load_config()

    # Determinar si estamos en un directorio de serie o en la raíz
    cwd = os.getcwd()
    if 'insar_' in cwd:
        # Estamos en un directorio de serie
        series_dir = cwd
        logger = LoggerConfig.setup_series_logger(
            series_dir=series_dir,
            log_name="pair_statistics"
        )
    else:
        # Buscar proyecto AOI más cercano
        output_dir = config.get('OUTPUT_DIR', 'fusion')
        if 'processing/' in output_dir:
            aoi_project = output_dir.split('processing/')[1].split('/')[0]
            aoi_project_dir = f"processing/{aoi_project}"
            logger = LoggerConfig.setup_aoi_logger(
                aoi_project_dir=aoi_project_dir,
                log_name="pair_statistics"
            )
        else:
            logger = LoggerConfig.setup_series_logger(
                series_dir=output_dir,
                log_name="pair_statistics"
            )

    logger.info("="*80)
    logger.info("CÁLCULO DE ESTADÍSTICAS POR PAR TEMPORAL")
    logger.info("="*80)

    output_dir = config.get('OUTPUT_DIR', 'fusion')
    insar_dir = os.path.join(output_dir, 'insar')
    # NOTA: sar_dir ya no se usa (productos GRD deprecados)
    # Solo se procesan coherencia InSAR y MSAVI

    logger.info(f"\nDirectorios:")
    logger.info(f"  InSAR: {insar_dir}")
    logger.info(f"  Output: {output_dir}")

    # Verificar que existe el directorio InSAR
    if not os.path.isdir(insar_dir):
        logger.error(f"No existe directorio InSAR: {insar_dir}")
        return 1

    # Buscar interferogramas
    ifg_files = sorted(glob.glob(os.path.join(insar_dir, 'Ifg_*.dim')))

    if not ifg_files:
        logger.error(f"No se encontraron interferogramas en {insar_dir}")
        return 1

    logger.info(f"\nEncontrados {len(ifg_files)} pares InSAR")

    # Procesar cada par
    processed = 0
    failed = 0

    for ifg_file in ifg_files:
        try:
            # sar_dir ya no se usa, se pasa None por compatibilidad
            if process_insar_pair(ifg_file, None, output_dir, insar_dir):
                processed += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Error procesando {os.path.basename(ifg_file)}: {e}")
            failed += 1

    # Resumen
    logger.info(f"\n{'='*80}")
    logger.info("RESUMEN")
    logger.info(f"{'='*80}")
    logger.info(f"Pares procesados exitosamente: {processed}")
    logger.info(f"Pares con errores: {failed}")
    logger.info(f"Total: {len(ifg_files)}")
    logger.info("")

    if processed > 0:
        logger.info(f"Resultados guardados en: {output_dir}/pairs/")
        logger.info("")
        logger.info("Estructura de salida:")
        logger.info("  pairs/")
        logger.info("    pair_YYYYMMDD_YYYYMMDD/")
        logger.info("      ├── coherence.tif              (coherencia InSAR)")
        logger.info("      ├── msavi_master.tif           (índice de vegetación master, opcional)")
        logger.info("      └── msavi_slave.tif            (índice de vegetación slave, opcional)")
        logger.info("")
        logger.info(f"Pipeline SLC-only: Solo coherencia InSAR (GRD deprecado)")

    # Retornar 0 si al menos algunos pares fueron procesados exitosamente
    if processed == 0:
        logger.error("\n✗ NINGÚN par fue procesado exitosamente")
        return 1
    else:
        if failed > 0:
            logger.warning(f"Procesamiento completado: {processed} pares exitosos, {failed} con advertencias")
        else:
            logger.info(f"Procesamiento completado: {processed} pares exitosos")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        if logger:
            logger.warning("\nInterrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        if logger:
            logger.error(f"ERROR: {e}", exc_info=True)
        else:
            print(f"ERROR: {e}")
        sys.exit(1)
