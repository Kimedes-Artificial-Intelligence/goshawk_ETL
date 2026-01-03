#!/usr/bin/env python3
"""
Script: calculate_statistics.py
Descripción: Calcula estadísticas temporales a partir de productos procesados
Uso: python scripts/calculate_statistics.py
"""

import os
import sys
import glob
import numpy as np
import rasterio

# Importar módulos locales
sys.path.append(os.path.dirname(__file__))
from processing_utils import load_config
from logging_utils import LoggerConfig

# Logger se configurará según el directorio de trabajo
logger = None


def read_band_from_dim(dim_path, band_pattern, downsample_factor=1):
    """
    Lee una banda específica de un producto BEAM-DIMAP

    MODIFICADO PARA DETECCIÓN DE HUMEDAD:
    El downsample_factor ahora es 1 por defecto (sin reducción).
    Un factor de 4 creaba píxeles efectivos de ~40m, haciendo invisible
    cualquier fuga de agua puntual.

    Args:
        dim_path: Ruta al archivo .dim
        band_pattern: Patrón de la banda a buscar
        downsample_factor: Factor de reducción (1 = sin reducción, máxima resolución)
    """
    # BEAM-DIMAP almacena datos en directorio .data/
    # Necesitamos buscar el archivo .img correspondiente

    data_dir = dim_path.replace('.dim', '.data')

    if not os.path.isdir(data_dir):
        logger.error(f"No existe directorio .data: {data_dir}")
        return None, None

    try:
        # Buscar archivos .img que contengan el patrón de banda
        img_files = glob.glob(os.path.join(data_dir, '*.img'))

        # Filtrar por patrón de banda
        matching_files = [f for f in img_files if band_pattern.lower() in os.path.basename(f).lower()]

        if not matching_files:
            logger.warning(f"No se encontró banda '{band_pattern}' en {data_dir}")
            logger.warning(f"Archivos disponibles: {[os.path.basename(f) for f in img_files]}")
            return None, None

        # Usar el primer archivo que coincida
        img_file = matching_files[0]

        with rasterio.open(img_file) as src:
            # Leer con subsampling para ahorrar memoria
            if downsample_factor > 1:
                # Leer cada N píxeles (downsampling)
                data = src.read(1, 
                               out_shape=(
                                   src.height // downsample_factor,
                                   src.width // downsample_factor
                               ),
                               resampling=rasterio.enums.Resampling.average)
                # Actualizar profile con nuevas dimensiones
                profile = src.profile.copy()
                profile.update({
                    'height': data.shape[0],
                    'width': data.shape[1],
                    'transform': src.transform * src.transform.scale(
                        (src.width / data.shape[1]),
                        (src.height / data.shape[0])
                    )
                })
            else:
                data = src.read(1)
                profile = src.profile
                
            return data, profile

    except Exception as e:
        logger.error(f"Error leyendo {dim_path}: {e}")
        return None, None


def calculate_insar_statistics(config):
    """
    Calcula estadísticas de coherencia InSAR

    MÉTRICAS PARA DETECCIÓN DE HUMEDAD:
    - coherence_mean.tif: Media temporal de coherencia interferométrica
                          CRÍTICO: Zonas con fugas de agua tendrán BAJA
                          coherencia media (la humedad destruye la fase)
    - coherence_std.tif: Desviación estándar temporal de coherencia
                         IMPORTANTE: Zonas con fugas intermitentes tendrán
                         ALTA variabilidad de coherencia

    Filosofía: "Preservar el ruido para detectar humedad local"
    La decorrelación NO es ruido a eliminar, es la SEÑAL que buscamos.
    """
    logger.info("\n" + "=" * 60)
    logger.info("CALCULANDO ESTADÍSTICAS InSAR")
    logger.info("=" * 60)

    statistics_dir = config.get('STATISTICS_DIR', config.get('OUTPUT_DIR', 'fusion'))
    insar_dir = os.path.join(statistics_dir, 'insar')
    # Buscar en fusion/insar/cropped (nueva ubicación) y en processed/insar/cropped (legacy)
    cropped_dir_new = os.path.join(insar_dir, 'cropped')
    cropped_dir_legacy = os.path.join(config.get('OUTPUT_DIR', 'fusion'), 'insar', 'cropped')
    output_dir = statistics_dir

    if not os.path.isdir(insar_dir):
        logger.warning(f"No existe directorio InSAR: {insar_dir}")
        return False

    # Priorizar productos recortados si existen (buscar en ambas ubicaciones)
    cropped_files = []
    if os.path.isdir(cropped_dir_new):
        cropped_files = glob.glob(os.path.join(cropped_dir_new, '*_cropped.tif'))
    if not cropped_files and os.path.isdir(cropped_dir_legacy):
        cropped_files = glob.glob(os.path.join(cropped_dir_legacy, '*_cropped.tif'))
    
    if cropped_files:
        logger.info(f"Encontrados {len(cropped_files)} productos InSAR recortados al AOI")
        logger.info(f"  → Usando resolución completa (sin downsample)")
        use_cropped = True
        downsample_factor = 1
    else:
        # Buscar productos interferométricos originales
        dim_files = glob.glob(os.path.join(insar_dir, 'Ifg_*.dim'))

        if not dim_files:
            logger.warning(f"No se encontraron productos InSAR en {insar_dir}")
            return False

        logger.info(f"Encontrados {len(dim_files)} productos InSAR (sin recortar)")
        # MODIFICADO PARA DETECCIÓN DE HUMEDAD:
        # Eliminado downsample para mantener resolución máxima
        # El downsampling creaba píxeles efectivos de ~40m, haciendo invisible
        # cualquier fuga de agua puntual
        logger.info(f"  → Usando resolución completa (sin downsample) para detectar anomalías puntuales")
        logger.info(f"  💡 Para reducir uso de memoria: ejecuta 'python scripts/crop_insar_to_aoi.py' primero")
        use_cropped = False
        downsample_factor = 1  # MODIFICADO: Sin reducción para máxima resolución

    # Leer todas las bandas de coherencia
    coherence_arrays = []
    profile = None

    if use_cropped:
        # Leer archivos TIF recortados directamente
        for tif_file in sorted(cropped_files):
            logger.info(f"  Leyendo: {os.path.basename(tif_file)}")
            try:
                with rasterio.open(tif_file) as src:
                    coh_data = src.read(1)
                    # Filtrar valores no válidos
                    coh_data = np.where((coh_data >= 0) & (coh_data <= 1), coh_data, np.nan)
                    coherence_arrays.append(coh_data)
                    if profile is None:
                        profile = src.profile
            except Exception as e:
                logger.error(f"  Error leyendo {os.path.basename(tif_file)}: {e}")
    else:
        # Leer productos .dim originales con downsampling
        for dim_file in sorted(dim_files):
            logger.info(f"  Leyendo: {os.path.basename(dim_file)}")
            coh_data, coh_profile = read_band_from_dim(dim_file, 'coh', downsample_factor=downsample_factor)

            if coh_data is not None:
                # Filtrar valores no válidos
                coh_data = np.where((coh_data >= 0) & (coh_data <= 1), coh_data, np.nan)
                coherence_arrays.append(coh_data)
                if profile is None:
                    profile = coh_profile

    if not coherence_arrays:
        logger.error("No se pudieron leer bandas de coherencia")
        return False

    # Verificar que todas las imágenes tengan el mismo tamaño
    shapes = [arr.shape for arr in coherence_arrays]
    if len(set(shapes)) > 1:
        logger.warning(f"Imágenes con tamaños diferentes: {shapes}")
        logger.info("Usando solo imágenes del tamaño más común...")

        # Encontrar el tamaño más común
        from collections import Counter
        most_common_shape = Counter(shapes).most_common(1)[0][0]
        logger.info(f"Tamaño seleccionado: {most_common_shape}")

        # Filtrar solo las imágenes con el tamaño más común (evita remuestreo costoso)
        coherence_arrays = [arr for arr in coherence_arrays if arr.shape == most_common_shape]
        logger.info(f"Usando {len(coherence_arrays)} imágenes con tamaño consistente")

    # Calcular estadísticas (optimizado para memoria)
    logger.info("\nCalculando estadísticas temporales...")

    # Usar el primer array para obtener las dimensiones
    height, width = coherence_arrays[0].shape

    # Calcular estadísticas incrementalmente para ahorrar memoria
    logger.info("Calculando media...")
    coh_sum = np.zeros((height, width), dtype=np.float64)
    coh_count = np.zeros((height, width), dtype=np.float32)

    for arr in coherence_arrays:
        mask = ~np.isnan(arr)
        coh_sum[mask] += arr[mask]
        coh_count[mask] += 1

    with np.errstate(invalid='ignore', divide='ignore'):
        coh_mean = (coh_sum / coh_count).astype(np.float32)
        coh_mean[coh_count == 0] = np.nan

    # Calcular desviación estándar
    logger.info("Calculando desviación estándar...")
    coh_var = np.zeros((height, width), dtype=np.float64)

    for arr in coherence_arrays:
        mask = ~np.isnan(arr)
        diff = arr.copy()
        diff[mask] = (arr[mask] - coh_mean[mask]) ** 2
        coh_var[mask] += diff[mask]

    with np.errstate(invalid='ignore', divide='ignore'):
        coh_std = np.sqrt(coh_var / coh_count).astype(np.float32)
        coh_std[coh_count == 0] = np.nan

    # Guardar GeoTIFFs
    profile.update(dtype=rasterio.float32, count=1, compress='lzw', driver='GTiff')

    output_mean = os.path.join(output_dir, 'coherence_mean.tif')
    with rasterio.open(output_mean, 'w', **profile) as dst:
        dst.write(coh_mean, 1)
    logger.info(f"✓ Guardado: {output_mean}")

    output_std = os.path.join(output_dir, 'coherence_std.tif')
    with rasterio.open(output_std, 'w', **profile) as dst:
        dst.write(coh_std, 1)
    logger.info(f"✓ Guardado: {output_std}")

    return True


def calculate_sar_statistics(config):
    """
    Calcula estadísticas de backscatter SAR

    MÉTRICAS PARA DETECCIÓN DE HUMEDAD:
    - vv_mean.tif: Media temporal de amplitud VV (dB)
    - vv_std.tif: Desviación estándar temporal de amplitud VV (dB)
                  CRÍTICO: Una calle seca es estable. Una calle con fugas
                  intermitentes tendrá alta varianza en amplitud.
    - entropy_mean.tif: Media temporal de textura GLCM
    - contrast_mean.tif: Media temporal de contraste GLCM

    La variabilidad temporal (vv_std) es clave para detectar zonas
    con cambios intermitentes de humedad (fugas, riego, etc.)
    """
    logger.info("\n" + "=" * 60)
    logger.info("CALCULANDO ESTADÍSTICAS SAR")
    logger.info("=" * 60)

    statistics_dir = config.get('STATISTICS_DIR', config.get('OUTPUT_DIR', 'fusion'))
    sar_dir = os.path.join(statistics_dir, 'sar')
    output_dir = statistics_dir

    if not os.path.isdir(sar_dir):
        logger.warning(f"No existe directorio SAR: {sar_dir}")
        return False

    # Buscar productos GRD procesados
    dim_files = glob.glob(os.path.join(sar_dir, 'GRD_*.dim'))

    if not dim_files:
        logger.warning(f"No se encontraron productos SAR en {sar_dir}")
        return False

    logger.info(f"Encontrados {len(dim_files)} productos SAR")

    # Leer todas las bandas Sigma0_VV (SIN downsample para máxima resolución)
    vv_arrays = []
    profile = None

    for dim_file in sorted(dim_files):
        logger.info(f"  Leyendo: {os.path.basename(dim_file)}")
        vv_data, vv_profile = read_band_from_dim(dim_file, 'Sigma0_VV', downsample_factor=1)

        if vv_data is not None:
            # Convertir a dB y filtrar valores no válidos
            with np.errstate(divide='ignore', invalid='ignore'):
                vv_db = 10 * np.log10(vv_data)
                vv_db = np.where(np.isfinite(vv_db) & (vv_db > -30) & (vv_db < 10), vv_db, np.nan)
            vv_arrays.append(vv_db)
            if profile is None:
                profile = vv_profile

    if not vv_arrays:
        logger.error("No se pudieron leer bandas Sigma0_VV")
        return False

    # Verificar que todas las imágenes tengan el mismo tamaño
    shapes = [arr.shape for arr in vv_arrays]
    if len(set(shapes)) > 1:
        logger.warning(f"Imágenes con tamaños diferentes: {shapes}")
        logger.info("Remuestreando a tamaño común...")

        # Encontrar el tamaño más común
        from collections import Counter
        most_common_shape = Counter(shapes).most_common(1)[0][0]
        logger.info(f"Usando tamaño: {most_common_shape}")

        # Remuestrear todas las imágenes al tamaño más común
        from scipy.ndimage import zoom
        resampled_arrays = []

        for i, arr in enumerate(vv_arrays):
            if arr.shape != most_common_shape:
                zoom_factors = (most_common_shape[0] / arr.shape[0], most_common_shape[1] / arr.shape[1])
                arr_resampled = zoom(arr, zoom_factors, order=1)
                resampled_arrays.append(arr_resampled)
                logger.info(f"  Remuestreado {i+1}: {arr.shape} → {arr_resampled.shape}")
            else:
                resampled_arrays.append(arr)

        vv_arrays = resampled_arrays

    # Calcular estadísticas (optimizado para memoria)
    logger.info("\nCalculando estadísticas temporales...")

    # Usar el primer array para obtener las dimensiones
    height, width = vv_arrays[0].shape

    # Calcular estadísticas incrementalmente para ahorrar memoria
    logger.info("Calculando media...")
    vv_sum = np.zeros((height, width), dtype=np.float64)
    vv_count = np.zeros((height, width), dtype=np.float32)

    for arr in vv_arrays:
        mask = ~np.isnan(arr)
        vv_sum[mask] += arr[mask]
        vv_count[mask] += 1

    with np.errstate(invalid='ignore', divide='ignore'):
        vv_mean = (vv_sum / vv_count).astype(np.float32)
        vv_mean[vv_count == 0] = np.nan

    # Calcular desviación estándar
    logger.info("Calculando desviación estándar...")
    vv_var = np.zeros((height, width), dtype=np.float64)

    for arr in vv_arrays:
        mask = ~np.isnan(arr)
        diff = arr.copy()
        diff[mask] = (arr[mask] - vv_mean[mask]) ** 2
        vv_var[mask] += diff[mask]

    with np.errstate(invalid='ignore', divide='ignore'):
        vv_std = np.sqrt(vv_var / vv_count).astype(np.float32)
        vv_std[vv_count == 0] = np.nan

    # Guardar GeoTIFFs
    profile.update(dtype=rasterio.float32, count=1, compress='lzw', driver='GTiff')

    output_mean = os.path.join(output_dir, 'vv_mean.tif')
    with rasterio.open(output_mean, 'w', **profile) as dst:
        dst.write(vv_mean, 1)
    logger.info(f"✓ Guardado: {output_mean}")

    output_std = os.path.join(output_dir, 'vv_std.tif')
    with rasterio.open(output_std, 'w', **profile) as dst:
        dst.write(vv_std, 1)
    logger.info(f"✓ Guardado: {output_std}")

    # Calcular entropía GLCM real (desde bandas generadas por SNAP)
    logger.info("\nCalculando estadísticas de Entropía GLCM...")

    # Leer bandas GLCMEntropy de los productos (generadas por SNAP GLCM operator)
    entropy_arrays = []
    contrast_arrays = []

    for dim_file in sorted(dim_files):
        # Intentar leer GLCMEntropy (puede ser GLCMEntropy_Gamma0_VV o similar)
        entropy_data, _ = read_band_from_dim(dim_file, 'Entropy', downsample_factor=1)
        if entropy_data is not None:
            # Filtrar valores no válidos
            entropy_data = np.where(np.isfinite(entropy_data) & (entropy_data >= 0), entropy_data, np.nan)
            entropy_arrays.append(entropy_data)

        # También leer GLCMContrast si existe
        contrast_data, _ = read_band_from_dim(dim_file, 'Contrast', downsample_factor=1)
        if contrast_data is not None:
            contrast_data = np.where(np.isfinite(contrast_data) & (contrast_data >= 0), contrast_data, np.nan)
            contrast_arrays.append(contrast_data)

    if entropy_arrays:
        logger.info(f"  Encontradas {len(entropy_arrays)} bandas GLCMEntropy")

        # Verificar tamaños y remuestrear si es necesario
        from collections import Counter
        from scipy.ndimage import zoom

        shapes = [arr.shape for arr in entropy_arrays]
        if len(set(shapes)) > 1:
            most_common_shape = Counter(shapes).most_common(1)[0][0]
            entropy_arrays = [
                zoom(arr, (most_common_shape[0]/arr.shape[0], most_common_shape[1]/arr.shape[1]), order=1)
                if arr.shape != most_common_shape else arr
                for arr in entropy_arrays
            ]

        # Calcular media temporal de entropía GLCM
        entropy_stack = np.stack(entropy_arrays, axis=0)
        with np.errstate(invalid='ignore'):
            entropy_mean = np.nanmean(entropy_stack, axis=0).astype(np.float32)

        output_entropy = os.path.join(output_dir, 'entropy_mean.tif')
        with rasterio.open(output_entropy, 'w', **profile) as dst:
            dst.write(entropy_mean, 1)
        logger.info(f"✓ Guardado: {output_entropy} (GLCM Entropy real)")
    else:
        # Fallback: calcular entropía aproximada si no hay GLCM
        logger.warning("  ⚠ No se encontraron bandas GLCMEntropy - usando aproximación CV local")
        from scipy.ndimage import generic_filter

        def local_entropy(values):
            values = values[~np.isnan(values)]
            if len(values) < 2:
                return np.nan
            return np.std(values) / (np.abs(np.mean(values)) + 1e-10)

        with np.errstate(invalid='ignore'):
            entropy = generic_filter(vv_mean, local_entropy, size=7, mode='constant', cval=np.nan)
            entropy = entropy.astype(np.float32)

        output_entropy = os.path.join(output_dir, 'entropy_mean.tif')
        with rasterio.open(output_entropy, 'w', **profile) as dst:
            dst.write(entropy, 1)
        logger.info(f"✓ Guardado: {output_entropy} (CV local - fallback)")

    # Guardar Contrast GLCM si existe
    if contrast_arrays:
        logger.info(f"  Encontradas {len(contrast_arrays)} bandas GLCMContrast")

        shapes = [arr.shape for arr in contrast_arrays]
        if len(set(shapes)) > 1:
            most_common_shape = Counter(shapes).most_common(1)[0][0]
            contrast_arrays = [
                zoom(arr, (most_common_shape[0]/arr.shape[0], most_common_shape[1]/arr.shape[1]), order=1)
                if arr.shape != most_common_shape else arr
                for arr in contrast_arrays
            ]

        contrast_stack = np.stack(contrast_arrays, axis=0)
        with np.errstate(invalid='ignore'):
            contrast_mean = np.nanmean(contrast_stack, axis=0).astype(np.float32)

        output_contrast = os.path.join(output_dir, 'contrast_mean.tif')
        with rasterio.open(output_contrast, 'w', **profile) as dst:
            dst.write(contrast_mean, 1)
        logger.info(f"✓ Guardado: {output_contrast} (GLCM Contrast)")

    return True


def main():
    global logger
    
    config = load_config()
    
    # Determinar si estamos en un directorio de serie o en la raíz
    cwd = os.getcwd()
    if 'insar_' in cwd:
        # Estamos en un directorio de serie
        series_dir = cwd
        logger = LoggerConfig.setup_series_logger(
            series_dir=series_dir,
            log_name="statistics"
        )
    else:
        # Buscar proyecto AOI más cercano
        output_dir = config.get('OUTPUT_DIR', 'fusion')
        if 'processing/' in output_dir:
            aoi_project = output_dir.split('processing/')[1].split('/')[0]
            aoi_project_dir = f"processing/{aoi_project}"
            logger = LoggerConfig.setup_aoi_logger(
                aoi_project_dir=aoi_project_dir,
                log_name="statistics"
            )
        else:
            logger = LoggerConfig.setup_series_logger(
                series_dir=output_dir,
                log_name="statistics"
            )
    
    logger.info("=" * 80)
    logger.info("CÁLCULO DE ESTADÍSTICAS TEMPORALES")
    logger.info("=" * 80)

    # Calcular estadísticas InSAR
    insar_ok = calculate_insar_statistics(config)

    # Calcular estadísticas SAR
    sar_ok = calculate_sar_statistics(config)

    if insar_ok and sar_ok:
        logger.info("\n" + "=" * 80)
        logger.info("✓ ESTADÍSTICAS CALCULADAS CORRECTAMENTE")
        logger.info("=" * 80)
        return 0
    elif not insar_ok and not sar_ok:
        logger.error("\n" + "=" * 80)
        logger.error("✗ ERROR: No se pudieron calcular estadísticas")
        logger.error("=" * 80)
        return 1
    else:
        logger.warning("\n" + "=" * 80)
        logger.warning("⚠ ADVERTENCIA: Solo se calcularon estadísticas parciales")
        logger.warning("=" * 80)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning("\nInterrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        logger.error(f"ERROR: {e}", exc_info=True)
        sys.exit(1)
