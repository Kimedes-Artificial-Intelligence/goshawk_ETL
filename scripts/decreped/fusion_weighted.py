#!/usr/bin/env python3
"""
Script: fusion_weighted.py
Descripción: Fusión ponderada de características SAR + InSAR para detección de fugas
Método: Ponderación lineal de características normalizadas
Uso: python3 scripts/fusion_weighted.py
"""

import numpy as np
import rasterio
from rasterio.plot import show
import matplotlib
matplotlib.use('Agg')  # Usar backend no-interactivo
import matplotlib.pyplot as plt
from scipy.ndimage import uniform_filter, label
from pathlib import Path
import sys
import json
import os

# Importar sistema de logging
sys.path.append(os.path.dirname(__file__))
from logging_utils import LoggerConfig

logger = None  # Se configurará en main()

# Configuración
CONFIG_FILE = "../config.txt"  # Config en el directorio padre
OUTPUT_DIR = "fusion_weighted"  # Carpeta de salida para método ponderado
STATISTICS_DIR = ".."  # Estadísticas en el directorio actual (fusion/)

# Pesos de fusión (ajustar según preferencia)
WEIGHTS = {
    'coherence': 0.40,  # Indicador más fuerte
    'vv_std': 0.30,     # Variabilidad temporal
    'entropy': 0.30     # Textura
}

def load_config():
    """Cargar configuración desde config.txt"""
    config = {}
    try:
        with open(CONFIG_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # Remover comentarios inline
                    if '#' in value:
                        value = value.split('#')[0]
                    config[key.strip()] = value.strip().strip('"')
    except FileNotFoundError:
        print(f"ADVERTENCIA: No se encontró {CONFIG_FILE}, usando valores por defecto")

    return config

def normalize(data, low_percentile=5, high_percentile=95):
    """
    Normalización robusta usando percentiles

    Args:
        data: Array numpy con los datos
        low_percentile: Percentil inferior para recorte
        high_percentile: Percentil superior para recorte

    Returns:
        Array normalizado [0, 1]
    """
    valid = data[np.isfinite(data)]

    if len(valid) == 0:
        print("ADVERTENCIA: No hay datos válidos para normalizar")
        return data

    vmin = np.percentile(valid, low_percentile)
    vmax = np.percentile(valid, high_percentile)

    normed = (data - vmin) / (vmax - vmin + 1e-10)  # Evitar división por cero
    return np.clip(normed, 0, 1)

def load_feature(file_path, feature_name):
    """Cargar característica desde GeoTIFF"""
    try:
        with rasterio.open(file_path) as src:
            data = src.read(1)
            transform = src.transform
            crs = src.crs
            profile = src.profile
        print(f"  ✓ {feature_name}: {file_path}")
        return data, transform, crs, profile
    except Exception as e:
        print(f"  ✗ ERROR cargando {feature_name}: {e}")
        return None, None, None, None

def main():
    global logger
    
    print("="*80)
    print("FUSIÓN PONDERADA - Detección de Fugas SAR+InSAR")
    print("="*80)

    # Cargar configuración
    config = load_config()
    
    # OUTPUT_DIR y STATISTICS_DIR son fijos (definidos en el script), NO del config
    output_dir = OUTPUT_DIR
    statistics_dir = STATISTICS_DIR
    threshold_high = float(config.get('THRESHOLD_HIGH', 0.7))
    threshold_medium = float(config.get('THRESHOLD_MEDIUM', 0.5))
    min_cluster_size = int(config.get('MIN_CLUSTER_SIZE', 50))
    
    # Configurar logger
    cwd = os.getcwd()
    if 'insar_' in cwd:
        series_dir = cwd
        logger = LoggerConfig.setup_series_logger(
            series_dir=series_dir,
            log_name="fusion_weighted"
        )
    else:
        if 'processing/' in statistics_dir:
            aoi_project = statistics_dir.split('processing/')[1].split('/')[0]
            aoi_project_dir = f"processing/{aoi_project}"
            logger = LoggerConfig.setup_aoi_logger(
                aoi_project_dir=aoi_project_dir,
                log_name="fusion_weighted"
            )
        else:
            logger = LoggerConfig.setup_series_logger(
                series_dir=statistics_dir,
                log_name="fusion_weighted"
            )
    
    logger.info("="*80)
    logger.info("FUSIÓN PONDERADA - Detección de Fugas SAR+InSAR")
    logger.info("="*80)

    # Crear directorio de salida si no existe
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    logger.info(f"Configuración:")
    logger.info(f"  Statistics dir: {statistics_dir}")
    logger.info(f"  Output dir: {output_dir}")
    logger.info(f"  Threshold high: {threshold_high}")
    logger.info(f"  Threshold medium: {threshold_medium}")
    logger.info(f"  Min cluster size: {min_cluster_size} píxeles")
    
    print(f"\nConfiguración:")
    print(f"  Statistics dir: {statistics_dir}")
    print(f"  Output dir: {output_dir}")
    print(f"  Threshold high: {threshold_high}")
    print(f"  Threshold medium: {threshold_medium}")
    print(f"  Min cluster size: {min_cluster_size} píxeles")

    # ==== CARGAR CARACTERÍSTICAS ====
    print("\n[1/6] Cargando características...")

    # Coherencia media (InSAR)
    coh_mean_file = f"{statistics_dir}/coherence_mean.tif"
    coh_mean, transform, crs, profile = load_feature(coh_mean_file, "Coherencia media")

    if coh_mean is None:
        print("\nERROR: No se pudo cargar coherence_mean.tif")
        print("Ejecuta primero: ./scripts/process_insar.sh")
        sys.exit(1)

    # VV desviación estándar (SAR)
    vv_std_file = f"{statistics_dir}/vv_std.tif"
    vv_std, _, _, _ = load_feature(vv_std_file, "VV std")

    if vv_std is None:
        print("\nERROR: No se pudo cargar vv_std.tif")
        print("Ejecuta primero: ./scripts/process_sar.sh")
        sys.exit(1)

    # Entropy media (SAR - Textura)
    entropy_file = f"{statistics_dir}/entropy_mean.tif"
    entropy_mean, _, _, _ = load_feature(entropy_file, "Entropy media")

    if entropy_mean is None:
        print("\nERROR: No se pudo cargar entropy_mean.tif")
        print("Ejecuta primero: ./scripts/process_sar.sh")
        sys.exit(1)

    # Verificar dimensiones y remuestrear si es necesario
    if not (coh_mean.shape == vv_std.shape == entropy_mean.shape):
        print("\n⚠️  Las características tienen dimensiones diferentes")
        print(f"  coherence_mean (InSAR): {coh_mean.shape}")
        print(f"  vv_std (SAR): {vv_std.shape}")
        print(f"  entropy_mean (SAR): {entropy_mean.shape}")

        # Usar las dimensiones del SAR como referencia (tienen el subset del AOI correcto)
        # Prioridad: vv_std > entropy_mean > coh_mean
        if vv_std.shape == entropy_mean.shape:
            target_shape = vv_std.shape
            print(f"\n  → Usando dimensiones SAR (con subset AOI): {target_shape}")
        else:
            # Si SAR tiene dimensiones inconsistentes, usar el más grande
            shapes = [vv_std.shape, entropy_mean.shape]
            target_shape = max(shapes, key=lambda s: s[0] * s[1])
            print(f"\n  → Usando dimensiones SAR más grande: {target_shape}")

        from scipy.ndimage import zoom

        # Remuestrear coh_mean si es necesario
        if coh_mean.shape != target_shape:
            zoom_factors = (target_shape[0] / coh_mean.shape[0], target_shape[1] / coh_mean.shape[1])
            coh_mean = zoom(coh_mean, zoom_factors, order=1)
            print(f"  ✓ coherence_mean (InSAR) remuestreado de {coh_mean.shape} → {target_shape}")

        # Remuestrear vv_std si es necesario
        if vv_std.shape != target_shape:
            zoom_factors = (target_shape[0] / vv_std.shape[0], target_shape[1] / vv_std.shape[1])
            vv_std = zoom(vv_std, zoom_factors, order=1)
            print(f"  ✓ vv_std remuestreado")

        # Remuestrear entropy_mean si es necesario
        if entropy_mean.shape != target_shape:
            zoom_factors = (target_shape[0] / entropy_mean.shape[0], target_shape[1] / entropy_mean.shape[1])
            entropy_mean = zoom(entropy_mean, zoom_factors, order=1)
            print(f"  ✓ entropy_mean remuestreado")

        # Actualizar profile para el tamaño nuevo (usar el de SAR si está disponible)
        # Cargar profile de SAR para tener el extent/transform correcto del AOI
        sar_profile = None
        with rasterio.open(vv_std_file) as src:
            sar_profile = src.profile.copy()
            sar_transform = src.transform
        
        if sar_profile:
            profile.update({
                'height': target_shape[0],
                'width': target_shape[1],
                'transform': sar_transform
            })
            print(f"  ✓ Usando georeferencia del SAR (con AOI correcto)")
        else:
            # Fallback: actualizar solo dimensiones
            profile['height'] = target_shape[0]
            profile['width'] = target_shape[1]
            from rasterio.transform import Affine
            profile['transform'] = Affine(
                transform.a * (coh_mean.shape[1] / target_shape[1]),
                transform.b,
                transform.c,
                transform.d,
                transform.e * (coh_mean.shape[0] / target_shape[0]),
                transform.f
            )

    print(f"\n  Dimensiones finales: {coh_mean.shape[0]} × {coh_mean.shape[1]}")

    # ==== NORMALIZAR CARACTERÍSTICAS ====
    print("\n[2/6] Normalizando características...")

    # Coherencia: invertir (bajo = sospechoso)
    coh_mean_norm = 1 - normalize(coh_mean)
    print(f"  ✓ Coherencia normalizada (invertida)")

    # VV std: alto = sospechoso
    vv_std_norm = normalize(vv_std)
    print(f"  ✓ VV std normalizada")

    # Entropy: alto = sospechoso
    entropy_norm = normalize(entropy_mean)
    print(f"  ✓ Entropy normalizada")

    # ==== FUSIÓN PONDERADA ====
    print("\n[3/6] Aplicando fusión ponderada...")

    print(f"\n  Pesos utilizados:")
    print(f"    Coherencia: {WEIGHTS['coherence']:.2f}")
    print(f"    VV std: {WEIGHTS['vv_std']:.2f}")
    print(f"    Entropy: {WEIGHTS['entropy']:.2f}")
    print(f"    Total: {sum(WEIGHTS.values()):.2f}")

    leak_probability = (
        WEIGHTS['coherence'] * coh_mean_norm +
        WEIGHTS['vv_std'] * vv_std_norm +
        WEIGHTS['entropy'] * entropy_norm
    )

    print(f"\n  ✓ Probabilidad de fuga calculada")
    print(f"    Min: {np.nanmin(leak_probability):.3f}")
    print(f"    Max: {np.nanmax(leak_probability):.3f}")
    print(f"    Media: {np.nanmean(leak_probability):.3f}")

    # ==== APLICAR UMBRALES ====
    print("\n[4/6] Aplicando umbrales...")

    leak_mask_high = leak_probability > threshold_high
    leak_mask_medium = (leak_probability > threshold_medium) & (leak_probability <= threshold_high)

    n_high = np.sum(leak_mask_high)
    n_medium = np.sum(leak_mask_medium)

    print(f"  Píxeles con riesgo alto (>{threshold_high}): {n_high} ({n_high/leak_probability.size*100:.2f}%)")
    print(f"  Píxeles con riesgo medio ({threshold_medium}-{threshold_high}): {n_medium} ({n_medium/leak_probability.size*100:.2f}%)")

    # ==== FILTRADO ESPACIAL ====
    print("\n[5/6] Aplicando filtrado espacial...")

    # Filtro de mediana para eliminar ruido
    leak_mask_filtered = uniform_filter(leak_mask_high.astype(float), size=5) > 0.4

    # Etiquetar clusters conectados
    labeled_array, num_features = label(leak_mask_filtered)
    print(f"  Clusters detectados: {num_features}")

    # Filtrar clusters pequeños
    for i in range(1, num_features + 1):
        cluster_size = np.sum(labeled_array == i)
        if cluster_size < min_cluster_size:
            labeled_array[labeled_array == i] = 0

    # Recalcular número de clusters
    unique_labels = np.unique(labeled_array)
    num_features_filtered = len(unique_labels) - 1  # -1 para excluir el 0

    print(f"  Clusters después de filtrar (>{min_cluster_size} px): {num_features_filtered}")

    # Máscara final
    leak_mask_final = labeled_array > 0

    # ==== GUARDAR RESULTADOS ====
    print("\n[6/6] Guardando resultados...")

    # Actualizar perfil para escritura
    profile.update(
        dtype='float32',
        count=1,
        compress='lzw',
        nodata=np.nan,
        driver='GTiff'
    )

    # 1. Mapa de probabilidad
    output_prob = f"{output_dir}/leak_probability_map.tif"
    with rasterio.open(output_prob, 'w', **profile) as dst:
        dst.write(leak_probability.astype('float32'), 1)
    print(f"  ✓ {output_prob}")

    # 2. Máscara de alto riesgo
    profile_mask = profile.copy()
    profile_mask.update(dtype='uint8', nodata=0, driver='GTiff')

    output_mask_high = f"{output_dir}/leak_zones_high_risk.tif"
    with rasterio.open(output_mask_high, 'w', **profile_mask) as dst:
        dst.write(leak_mask_final.astype('uint8'), 1)
    print(f"  ✓ {output_mask_high}")

    # 3. Máscara de riesgo medio
    output_mask_medium = f"{output_dir}/leak_zones_medium_risk.tif"
    with rasterio.open(output_mask_medium, 'w', **profile_mask) as dst:
        dst.write(leak_mask_medium.astype('uint8'), 1)
    print(f"  ✓ {output_mask_medium}")

    # 4. Clusters etiquetados
    profile_clusters = profile.copy()
    profile_clusters.update(dtype='int32', nodata=0)

    output_clusters = f"{output_dir}/leak_clusters.tif"
    with rasterio.open(output_clusters, 'w', **profile_clusters) as dst:
        dst.write(labeled_array.astype('int32'), 1)
    print(f"  ✓ {output_clusters}")

    # ==== VISUALIZACIÓN ====
    print("\nGenerando visualización...")

    fig, axes = plt.subplots(2, 3, figsize=(18, 12))

    # Coherencia media
    im1 = axes[0, 0].imshow(coh_mean, cmap='RdYlGn', vmin=0, vmax=1)
    axes[0, 0].set_title('Coherencia Media (InSAR)', fontsize=12, fontweight='bold')
    axes[0, 0].axis('off')
    plt.colorbar(im1, ax=axes[0, 0], fraction=0.046, pad=0.04)

    # VV std
    im2 = axes[0, 1].imshow(vv_std, cmap='plasma')
    axes[0, 1].set_title('Desv. Std. VV (SAR)', fontsize=12, fontweight='bold')
    axes[0, 1].axis('off')
    plt.colorbar(im2, ax=axes[0, 1], fraction=0.046, pad=0.04)

    # Entropy
    im3 = axes[0, 2].imshow(entropy_mean, cmap='viridis')
    axes[0, 2].set_title('Entropy Media (SAR)', fontsize=12, fontweight='bold')
    axes[0, 2].axis('off')
    plt.colorbar(im3, ax=axes[0, 2], fraction=0.046, pad=0.04)

    # Probabilidad de fuga
    im4 = axes[1, 0].imshow(leak_probability, cmap='hot', vmin=0, vmax=1)
    axes[1, 0].set_title('Probabilidad de Fuga', fontsize=12, fontweight='bold')
    axes[1, 0].axis('off')
    cbar4 = plt.colorbar(im4, ax=axes[1, 0], fraction=0.046, pad=0.04)
    cbar4.set_label('Probabilidad [0-1]', rotation=270, labelpad=15)

    # Máscara de detección
    im5 = axes[1, 1].imshow(leak_mask_final, cmap='Reds')
    axes[1, 1].set_title(f'Zonas de Alto Riesgo (>{threshold_high})', fontsize=12, fontweight='bold')
    axes[1, 1].axis('off')

    # Clusters etiquetados
    im6 = axes[1, 2].imshow(labeled_array, cmap='tab20')
    axes[1, 2].set_title(f'Clusters Detectados: {num_features_filtered}', fontsize=12, fontweight='bold')
    axes[1, 2].axis('off')

    plt.tight_layout()
    output_viz = f"{output_dir}/visualization.png"
    plt.savefig(output_viz, dpi=300, bbox_inches='tight')
    print(f"  ✓ {output_viz}")

    # ==== ESTADÍSTICAS FINALES ====
    print("\n" + "="*80)
    print("ESTADÍSTICAS FINALES")
    print("="*80)

    total_pixels = leak_probability.size
    leak_pixels = np.sum(leak_mask_final)
    leak_area_m2 = leak_pixels * 100  # Asumiendo 10m x 10m píxeles
    leak_area_ha = leak_area_m2 / 10000

    print(f"\nPíxeles totales: {total_pixels:,}")
    print(f"Píxeles con fugas detectadas: {leak_pixels:,} ({leak_pixels/total_pixels*100:.2f}%)")
    print(f"Área estimada de fugas: {leak_area_m2:.0f} m² ({leak_area_ha:.2f} ha)")
    print(f"Número de clusters: {num_features_filtered}")

    # Estadísticas por cluster
    if num_features_filtered > 0:
        print(f"\nTop 5 Clusters Más Grandes:")
        cluster_sizes = []
        for i in range(1, num_features_filtered + 1):
            cluster_size = np.sum(labeled_array == i)
            if cluster_size > 0:
                cluster_sizes.append((i, cluster_size))

        cluster_sizes.sort(key=lambda x: x[1], reverse=True)

        for i, (cluster_id, cluster_size) in enumerate(cluster_sizes[:5], 1):
            cluster_area_m2 = cluster_size * 100
            print(f"  {i}. Cluster {cluster_id}: {cluster_size} píxeles ({cluster_area_m2:.0f} m²)")

    # Guardar estadísticas en JSON
    stats = {
        'method': 'weighted_fusion',
        'weights': WEIGHTS,
        'thresholds': {
            'high': threshold_high,
            'medium': threshold_medium
        },
        'total_pixels': int(total_pixels),
        'leak_pixels': int(leak_pixels),
        'leak_percentage': float(leak_pixels/total_pixels*100),
        'leak_area_m2': float(leak_area_m2),
        'leak_area_ha': float(leak_area_ha),
        'num_clusters': int(num_features_filtered),
        'probability_stats': {
            'min': float(np.nanmin(leak_probability)),
            'max': float(np.nanmax(leak_probability)),
            'mean': float(np.nanmean(leak_probability)),
            'std': float(np.nanstd(leak_probability))
        }
    }

    stats_file = f"{output_dir}/statistics_report.json"
    with open(stats_file, 'w') as f:
        json.dump(stats, f, indent=2)
    print(f"\nEstadísticas guardadas: {stats_file}")

    # Guardar también en formato texto
    stats_txt = f"{output_dir}/statistics_report.txt"
    with open(stats_txt, 'w') as f:
        f.write("ESTADÍSTICAS DE DETECCIÓN DE FUGAS\n")
        f.write("="*80 + "\n\n")
        f.write(f"Método: Fusión Ponderada\n\n")
        f.write(f"Pesos:\n")
        f.write(f"  - Coherencia: {WEIGHTS['coherence']:.2f}\n")
        f.write(f"  - VV std: {WEIGHTS['vv_std']:.2f}\n")
        f.write(f"  - Entropy: {WEIGHTS['entropy']:.2f}\n\n")
        f.write(f"Umbrales:\n")
        f.write(f"  - Alto riesgo: >{threshold_high}\n")
        f.write(f"  - Riesgo medio: {threshold_medium}-{threshold_high}\n\n")
        f.write(f"Resultados:\n")
        f.write(f"  - Píxeles totales: {total_pixels:,}\n")
        f.write(f"  - Píxeles con fugas: {leak_pixels:,} ({leak_pixels/total_pixels*100:.2f}%)\n")
        f.write(f"  - Área estimada: {leak_area_m2:.0f} m² ({leak_area_ha:.2f} ha)\n")
        f.write(f"  - Clusters detectados: {num_features_filtered}\n")

    print(f"Estadísticas guardadas: {stats_txt}")

    logger.info("="*80)
    logger.info("¡PROCESAMIENTO COMPLETADO!")
    logger.info("="*80)
    logger.info(f"Archivos generados:")
    logger.info(f"  1. {output_prob}")
    logger.info(f"  2. {output_mask_high}")
    logger.info(f"  3. {output_mask_medium}")
    logger.info(f"  4. {output_clusters}")
    logger.info(f"  5. {output_viz}")
    logger.info(f"  6. {stats_file}")
    
    print("\n" + "="*80)
    print("¡PROCESAMIENTO COMPLETADO!")
    print("="*80)
    print(f"\nArchivos generados:")
    print(f"  1. {output_prob}")
    print(f"  2. {output_mask_high}")
    print(f"  3. {output_mask_medium}")
    print(f"  4. {output_clusters}")
    print(f"  5. {output_viz}")
    print(f"  6. {stats_file}")
    print(f"\nPróximo paso:")
    print(f"  - Abrir {output_prob} en QGIS para visualizar zonas de riesgo")
    print(f"  - Revisar {stats_txt} para estadísticas detalladas")

if __name__ == "__main__":
    main()
