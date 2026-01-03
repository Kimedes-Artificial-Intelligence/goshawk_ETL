#!/usr/bin/env python3
"""
Script: calculate_pair_statistics.py
Descripción: Calcula estadísticas por cada par temporal InSAR + SAR
             Para cada par InSAR (master-slave):
             - Coherencia del interferograma
             - Entropía y VV del GRD master
             - Entropía y VV del GRD slave

Uso: python scripts/calculate_pair_statistics.py
"""

import os
import sys
import glob
import numpy as np
import rasterio
from pathlib import Path
from scipy.ndimage import generic_filter

# Importar módulos locales
sys.path.append(os.path.dirname(__file__))
from processing_utils import load_config, extract_date_from_filename
from logging_utils import LoggerConfig

# Logger se configurará según el directorio de trabajo
logger = None


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


def calculate_entropy_cv(vv_data, window_size=7):
    """
    Calcula entropía aproximada usando Coeficiente de Variación local (fallback)

    Args:
        vv_data: Array numpy con datos de backscatter VV
        window_size: Tamaño de ventana para cálculo local

    Returns:
        Array numpy con valores de entropía aproximada (CV)
    """

    def local_entropy(values):
        values = values[~np.isnan(values)]
        if len(values) < 2:
            return np.nan
        # Coeficiente de variación como proxy de entropía
        return np.std(values) / (np.abs(np.mean(values)) + 1e-10)

    with np.errstate(invalid='ignore'):
        entropy = generic_filter(vv_data, local_entropy, size=window_size,
                                mode='constant', cval=np.nan)
        entropy = entropy.astype(np.float32)

    return entropy


def get_glcm_bands_from_grd(grd_dim_path):
    """
    Extrae bandas GLCM (Entropy y Contrast) de un producto GRD procesado con SNAP.

    Args:
        grd_dim_path: Ruta al archivo .dim del producto GRD

    Returns:
        tuple: (entropy_data, contrast_data, profile) - entropy y contrast pueden ser None
    """
    entropy_data = None
    contrast_data = None
    profile = None

    # Intentar leer banda GLCMEntropy
    entropy_data, profile = read_band_from_dim(grd_dim_path, 'Entropy')
    if entropy_data is not None:
        # Filtrar valores no válidos
        entropy_data = np.where(np.isfinite(entropy_data) & (entropy_data >= 0),
                                entropy_data, np.nan).astype(np.float32)
        logger.info(f"      ✓ Banda GLCMEntropy encontrada")

    # Intentar leer banda GLCMContrast
    contrast_data, _ = read_band_from_dim(grd_dim_path, 'Contrast')
    if contrast_data is not None:
        contrast_data = np.where(np.isfinite(contrast_data) & (contrast_data >= 0),
                                 contrast_data, np.nan).astype(np.float32)
        logger.info(f"      ✓ Banda GLCMContrast encontrada")

    return entropy_data, contrast_data, profile


def get_entropy_for_image(grd_dim_path, vv_data=None, vv_profile=None):
    """
    Obtiene la entropía para una imagen GRD, priorizando GLCM real sobre CV local.

    Args:
        grd_dim_path: Ruta al archivo .dim del producto GRD
        vv_data: Datos VV (para fallback a CV local si no hay GLCM)
        vv_profile: Profile rasterio (para fallback)

    Returns:
        tuple: (entropy_data, contrast_data, profile, is_glcm)
               is_glcm indica si es GLCM real (True) o CV local (False)
    """
    # Intentar obtener GLCM real primero
    entropy_data, contrast_data, profile = get_glcm_bands_from_grd(grd_dim_path)

    if entropy_data is not None:
        return entropy_data, contrast_data, profile, True

    # Fallback: calcular CV local si hay datos VV
    if vv_data is not None:
        logger.warning(f"      ⚠ No hay bandas GLCM - usando CV local como fallback")
        entropy_cv = calculate_entropy_cv(vv_data)
        return entropy_cv, None, vv_profile, False

    return None, None, None, False


def find_grd_for_date(sar_dir, date_str):
    """
    Busca el producto GRD procesado para una fecha específica

    Args:
        sar_dir: Directorio con productos SAR procesados
        date_str: Fecha en formato YYYYMMDD

    Returns:
        Path al archivo .dim del GRD o None
    """
    # Buscar GRD_YYYYMMDD.dim
    grd_file = os.path.join(sar_dir, f'GRD_{date_str}.dim')

    if os.path.exists(grd_file):
        return grd_file

    # Buscar con patrón más flexible
    pattern = os.path.join(sar_dir, f'GRD_{date_str}*.dim')
    matches = glob.glob(pattern)

    if matches:
        return matches[0]

    return None


def find_msavi_for_date(data_dir, date_str, date_window=2):
    """
    Busca archivo MSAVI para una fecha específica con ventana temporal
    
    Args:
        data_dir: Directorio base de datos
        date_str: Fecha en formato YYYYMMDD
        date_window: Ventana de días para buscar (±days, default: 2)
    
    Returns:
        Path al archivo MSAVI .tif o None
    """
    from datetime import datetime, timedelta
    
    # Directorio de MSAVI
    msavi_dir = os.path.join(data_dir, 'sentinel2_msavi')
    
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

    Args:
        ifg_file: Path al interferograma .dim
        sar_dir: Directorio con productos SAR procesados
        output_base_dir: Directorio base para guardar resultados
        insar_dir: Directorio con productos InSAR

    Returns:
        bool: True si exitoso
    """
    basename = os.path.basename(ifg_file)

    # Extraer fechas del nombre: Ifg_YYYYMMDD_YYYYMMDD.dim
    parts = basename.replace('Ifg_', '').replace('.dim', '').split('_')
    if len(parts) != 2:
        logger.error(f"Formato de nombre inválido: {basename}")
        return False

    master_date = parts[0]
    slave_date = parts[1]

    pair_name = f"pair_{master_date}_{slave_date}"
    pair_dir = os.path.join(output_base_dir, 'pairs', pair_name)
    
    # Verificar si el par ya está completamente procesado
    required_files = [
        os.path.join(pair_dir, 'coherence.tif'),
        os.path.join(pair_dir, 'vv_master.tif'),
        os.path.join(pair_dir, 'vv_slave.tif')
    ]
    
    if all(os.path.exists(f) for f in required_files):
        logger.info(f"✓ Par ya procesado: {master_date} → {slave_date}")
        return True
    
    os.makedirs(pair_dir, exist_ok=True)

    logger.info(f"{'='*60}")
    logger.info(f"Par: {master_date} → {slave_date}")
    logger.info(f"{'='*60}")

    success = True
    
    # Inicializar variables que se usarán más tarde
    vv_master = None
    vv_slave = None
    vv_profile = None

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
                
                if valid_ratio < 0.10:  # Menos de 10% de datos válidos
                    logger.error(f"   ✗ ADVERTENCIA: Coherencia con datos insuficientes")
                    logger.error(f"      Solo {valid_pixels}/{total_pixels} píxeles válidos ({valid_ratio*100:.1f}%)")
                    logger.error(f"      El subswath seleccionado probablemente NO cubre el AOI")
                    logger.error(f"      RECOMENDACIÓN: Reprocesar con otro subswath (IW2 o IW3)")
                    # No fallar, pero advertir claramente
                
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

    # 2. Procesar GRD master
    logger.info(f"2. Procesando GRD master ({master_date})...")
    master_grd = find_grd_for_date(sar_dir, master_date)

    if master_grd:
        logger.info(f"   Encontrado: {os.path.basename(master_grd)}")

        # Extraer VV
        vv_master, vv_master_profile = read_band_from_dim(master_grd, 'Sigma0_VV')

        if vv_master is not None:
            vv_profile = vv_master_profile  # Guardar para uso posterior
            # Guardar VV master
            output_vv_master = os.path.join(pair_dir, 'vv_master.tif')
            with rasterio.open(output_vv_master, 'w', **vv_master_profile) as dst:
                dst.write(vv_master, 1)
            logger.info(f"   ✓ VV guardado: {output_vv_master}")

            # Obtener entropía (GLCM real o CV local como fallback)
            logger.info(f"   Extrayendo entropía master...")
            entropy_master, contrast_master, ent_profile, is_glcm = get_entropy_for_image(
                master_grd, vv_data=vv_master, vv_profile=vv_master_profile
            )

            if entropy_master is not None:
                output_entropy_master = os.path.join(pair_dir, 'entropy_master.tif')
                profile_to_use = ent_profile if ent_profile else vv_master_profile
                with rasterio.open(output_entropy_master, 'w', **profile_to_use) as dst:
                    dst.write(entropy_master, 1)
                entropy_type = "GLCM" if is_glcm else "CV local"
                logger.info(f"   ✓ Entropía guardada: {output_entropy_master} ({entropy_type})")

                # Guardar Contrast si existe
                if contrast_master is not None:
                    output_contrast_master = os.path.join(pair_dir, 'contrast_master.tif')
                    with rasterio.open(output_contrast_master, 'w', **profile_to_use) as dst:
                        dst.write(contrast_master, 1)
                    logger.info(f"   ✓ Contrast guardado: {output_contrast_master}")
        else:
            logger.error(f"   ✗ No se pudo extraer VV del master")
            success = False
    else:
        logger.error(f"   ✗ No se encontró GRD master para fecha {master_date}")
        success = False

    # 3. Procesar GRD slave
    logger.info(f"3. Procesando GRD slave ({slave_date})...")
    slave_grd = find_grd_for_date(sar_dir, slave_date)

    if slave_grd:
        logger.info(f"   Encontrado: {os.path.basename(slave_grd)}")

        # Extraer VV
        vv_slave, vv_slave_profile = read_band_from_dim(slave_grd, 'Sigma0_VV')

        if vv_slave is not None:
            # Si vv_profile no está definido (master falló), usar slave profile
            if vv_profile is None:
                vv_profile = vv_slave_profile
            
            # Guardar VV slave
            output_vv_slave = os.path.join(pair_dir, 'vv_slave.tif')
            with rasterio.open(output_vv_slave, 'w', **vv_slave_profile) as dst:
                dst.write(vv_slave, 1)
            logger.info(f"   ✓ VV guardado: {output_vv_slave}")

            # Obtener entropía (GLCM real o CV local como fallback)
            logger.info(f"   Extrayendo entropía slave...")
            entropy_slave, contrast_slave, ent_profile, is_glcm = get_entropy_for_image(
                slave_grd, vv_data=vv_slave, vv_profile=vv_slave_profile
            )

            if entropy_slave is not None:
                output_entropy_slave = os.path.join(pair_dir, 'entropy_slave.tif')
                profile_to_use = ent_profile if ent_profile else vv_profile
                with rasterio.open(output_entropy_slave, 'w', **profile_to_use) as dst:
                    dst.write(entropy_slave, 1)
                entropy_type = "GLCM" if is_glcm else "CV local"
                logger.info(f"   ✓ Entropía guardada: {output_entropy_slave} ({entropy_type})")

                # Guardar Contrast si existe
                if contrast_slave is not None:
                    output_contrast_slave = os.path.join(pair_dir, 'contrast_slave.tif')
                    with rasterio.open(output_contrast_slave, 'w', **profile_to_use) as dst:
                        dst.write(contrast_slave, 1)
                    logger.info(f"   ✓ Contrast guardado: {output_contrast_slave}")
        else:
            logger.error(f"   ✗ No se pudo extraer VV del slave")
            success = False
    else:
        logger.error(f"   ✗ No se encontró GRD slave para fecha {slave_date}")
        success = False

    # 4. Procesar MSAVI master (NUEVO)
    logger.info(f"4. Procesando MSAVI master ({master_date})...")
    data_dir = os.path.dirname(output_base_dir)  # Subir un nivel desde fusion/
    msavi_master_file = find_msavi_for_date(data_dir, master_date, date_window=2)
    
    if msavi_master_file:
        logger.info(f"   Encontrado: {os.path.basename(msavi_master_file)}")
        
        # Alinear MSAVI a la grilla del SAR (usar VV como referencia)
        if vv_master is not None and vv_profile is not None:
            msavi_master_aligned, msavi_profile = align_msavi_to_sar(msavi_master_file, vv_profile)
            
            if msavi_master_aligned is not None:
                output_msavi_master = os.path.join(pair_dir, 'msavi_master.tif')
                with rasterio.open(output_msavi_master, 'w', **msavi_profile) as dst:
                    dst.write(msavi_master_aligned, 1)
                    dst.set_band_description(1, 'MSAVI')
                logger.info(f"   ✓ MSAVI master guardado: {output_msavi_master}")
                
                # Mostrar estadísticas
                valid_data = msavi_master_aligned[np.isfinite(msavi_master_aligned)]
                if len(valid_data) > 0:
                    logger.info(f"      Rango: [{np.min(valid_data):.3f}, {np.max(valid_data):.3f}], Media: {np.mean(valid_data):.3f}")
            else:
                logger.warning(f"   ⚠ No se pudo alinear MSAVI master")
    else:
        logger.warning(f"   ⚠ No se encontró MSAVI master para fecha {master_date}")
        logger.info(f"      Ejecuta: python scripts/process_sentinel2_msavi.py --date {master_date}")
    
    # 5. Procesar MSAVI slave (NUEVO)
    logger.info(f"5. Procesando MSAVI slave ({slave_date})...")
    msavi_slave_file = find_msavi_for_date(data_dir, slave_date, date_window=2)
    
    if msavi_slave_file:
        logger.info(f"   Encontrado: {os.path.basename(msavi_slave_file)}")
        
        # Alinear MSAVI a la grilla del SAR (usar VV como referencia)
        if vv_slave is not None and vv_profile is not None:
            msavi_slave_aligned, msavi_profile = align_msavi_to_sar(msavi_slave_file, vv_profile)
            
            if msavi_slave_aligned is not None:
                output_msavi_slave = os.path.join(pair_dir, 'msavi_slave.tif')
                with rasterio.open(output_msavi_slave, 'w', **msavi_profile) as dst:
                    dst.write(msavi_slave_aligned, 1)
                    dst.set_band_description(1, 'MSAVI')
                logger.info(f"   ✓ MSAVI slave guardado: {output_msavi_slave}")
                
                # Mostrar estadísticas
                valid_data = msavi_slave_aligned[np.isfinite(msavi_slave_aligned)]
                if len(valid_data) > 0:
                    logger.info(f"      Rango: [{np.min(valid_data):.3f}, {np.max(valid_data):.3f}], Media: {np.mean(valid_data):.3f}")
            else:
                logger.warning(f"   ⚠ No se pudo alinear MSAVI slave")
    else:
        logger.warning(f"   ⚠ No se encontró MSAVI slave para fecha {slave_date}")
        logger.info(f"      Ejecuta: python scripts/process_sentinel2_msavi.py --date {slave_date}")

    if success:
        logger.info(f"✅ Par {pair_name} completado")
    else:
        logger.warning(f"⚠️  Par {pair_name} completado con errores")

    return success


def main():
    global logger

    # Cargar configuración primero
    config = load_config()

    # Determinar si estamos en un directorio de sèrie o en la raíz
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
    sar_dir = os.path.join(output_dir, 'sar')

    logger.info(f"Directorios:")
    logger.info(f"  InSAR: {insar_dir}")
    logger.info(f"  SAR:   {sar_dir}")
    logger.info(f"  Output: {output_dir}")

    # Verificar que existen los directorios
    if not os.path.isdir(insar_dir):
        logger.error(f"No existe directorio InSAR: {insar_dir}")
        return 1

    if not os.path.isdir(sar_dir):
        logger.error(f"No existe directorio SAR: {sar_dir}")
        return 1

    # Buscar interferogramas
    ifg_files = sorted(glob.glob(os.path.join(insar_dir, 'Ifg_*.dim')))

    if not ifg_files:
        logger.error(f"No se encontraron interferogramas en {insar_dir}")
        return 1

    logger.info(f"Encontrados {len(ifg_files)} pares InSAR")

    # Procesar cada par
    processed = 0
    failed = 0

    for ifg_file in ifg_files:
        try:
            if process_insar_pair(ifg_file, sar_dir, output_dir, insar_dir):
                processed += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Error procesando {os.path.basename(ifg_file)}: {e}")
            failed += 1

    # Resumen
    logger.info(f"{'='*80}")
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
        logger.info("      ├── coherence.tif")
        logger.info("      ├── vv_master.tif")
        logger.info("      ├── entropy_master.tif")
        logger.info("      ├── vv_slave.tif")
        logger.info("      ├── entropy_slave.tif")
        logger.info("      ├── msavi_master.tif      ← NUEVO (si Sentinel-2 disponible)")
        logger.info("      └── msavi_slave.tif       ← NUEVO (si Sentinel-2 disponible)")

    # Retornar 0 si al menos algunos pares fueron procesados exitosamente
    # Solo retornar error si TODOS fallaron o si la mayoría falló
    if processed == 0:
        logger.error("NINGÚN par fue procesado exitosamente")
        return 1
    elif failed > processed:
        logger.warning(f"Mayoría de pares fallaron ({failed}/{len(ifg_files)})")
        return 1
    else:
        # Al menos la mitad fue exitoso, considerarlo éxito
        if failed > 0:
            logger.warning(f"Procesamiento completado con {failed} errores parciales")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        if logger:
            logger.warning("Interrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        if logger:
            logger.error(f"ERROR: {e}", exc_info=True)
        else:
            print(f"ERROR: {e}")
        sys.exit(1)
