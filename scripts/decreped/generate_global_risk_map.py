#!/usr/bin/env python3
"""
Script: generate_global_risk_map.py
Descripción: Genera mapa de riesgo global para todo el periodo usando TODOS los pares
Método: Agrega estadísticas de todos los pares y aplica fusión ponderada
Uso: python3 scripts/generate_global_risk_map.py
"""

import numpy as np
import rasterio
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.ndimage import uniform_filter, label, zoom
from pathlib import Path
import json
import os
import sys
from glob import glob

sys.path.append(os.path.dirname(__file__))
from logging_utils import LoggerConfig

CONFIG_FILE = "config.txt"

WEIGHTS = {
    'coherence': 0.40,
    'vv_variability': 0.30,
    'entropy_variability': 0.30
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
                    if '#' in value:
                        value = value.split('#')[0]
                    config[key.strip()] = value.strip().strip('"')
    except FileNotFoundError:
        print(f"ADVERTENCIA: No se encontró {CONFIG_FILE}")
    return config

def normalize(data, low_percentile=5, high_percentile=95):
    """Normalización robusta usando percentiles"""
    valid = data[np.isfinite(data)]
    if len(valid) == 0:
        return data
    vmin = np.percentile(valid, low_percentile)
    vmax = np.percentile(valid, high_percentile)
    normed = (data - vmin) / (vmax - vmin + 1e-10)
    return np.clip(normed, 0, 1)

def load_raster(file_path):
    """Cargar raster desde archivo"""
    try:
        with rasterio.open(file_path) as src:
            data = src.read(1)
            profile = src.profile
        return data, profile
    except Exception as e:
        return None, None

def process_pueblo_global(pueblo_dir, config, logger, force_urban=None):
    """Procesa un pueblo y genera mapa de riesgo global de todo el periodo
    
    Args:
        pueblo_dir: Directorio del pueblo
        config: Configuración
        logger: Logger
        force_urban: None (auto), True (solo urbano), False (solo completo)
    """
    pueblo_name = os.path.basename(pueblo_dir)
    
    print(f"\n{'='*80}")
    print(f"Procesando pueblo: {pueblo_name.upper()}")
    print(f"{'='*80}")
    
    logger.info(f"Generando mapa global para {pueblo_name}")
    
    # Determinar qué versión procesar
    if force_urban is True:
        # Forzar versión urbana
        pair_dirs = sorted(glob(f"{pueblo_dir}/urban_products/insar_*/fusion/pairs/pair_*"))
        is_urban = True
        version_str = "urbanos"
    elif force_urban is False:
        # Forzar versión completa
        pair_dirs = sorted(glob(f"{pueblo_dir}/insar_*/fusion/pairs/pair_*"))
        is_urban = False
        version_str = "completos"
    else:
        # Auto (comportamiento anterior para compatibilidad)
        pair_dirs = sorted(glob(f"{pueblo_dir}/urban_products/insar_*/fusion/pairs/pair_*"))
        is_urban = len(pair_dirs) > 0
        
        if not is_urban:
            pair_dirs = sorted(glob(f"{pueblo_dir}/insar_*/fusion/pairs/pair_*"))
        
        version_str = "urbanos" if is_urban else "completos"
    
    if not pair_dirs:
        print(f"  ✗ No se encontraron pares en {pueblo_dir}")
        return False
    
    print(f"  Encontrados {len(pair_dirs)} pares {version_str}")
    logger.info(f"Procesando {len(pair_dirs)} pares {version_str}")
    
    # Listas para acumular datos
    coherence_list = []
    vv_master_list = []
    vv_slave_list = []
    entropy_master_list = []
    entropy_slave_list = []
    
    reference_shape = None
    reference_profile = None
    
    # Detectar si estamos en urban_products
    is_urban = '/urban_products/' in str(pair_dirs[0])
    suffix = '_urban' if is_urban else ''
    
    # Cargar datos de todos los pares
    print("\n[1/5] Cargando datos de todos los pares...")
    for i, pair_dir in enumerate(pair_dirs, 1):
        pair_name = os.path.basename(pair_dir)
        print(f"  [{i}/{len(pair_dirs)}] {pair_name}...", end=" ")
        
        # Cargar coherencia (con sufijo si es urbano)
        coh_file = os.path.join(pair_dir, f"coherence{suffix}.tif")
        coh, profile = load_raster(coh_file)
        
        if coh is None:
            print("✗ (sin coherencia)")
            continue
        
        if reference_shape is None:
            reference_shape = coh.shape
            reference_profile = profile
        
        # Remuestrear si es necesario
        if coh.shape != reference_shape:
            zoom_factors = (reference_shape[0] / coh.shape[0], reference_shape[1] / coh.shape[1])
            coh = zoom(coh, zoom_factors, order=1)
        
        # Cargar VV (con sufijo si es urbano)
        vv_m, _ = load_raster(os.path.join(pair_dir, f"vv_master{suffix}.tif"))
        vv_s, _ = load_raster(os.path.join(pair_dir, f"vv_slave{suffix}.tif"))
        
        if vv_m is None or vv_s is None:
            print("✗ (sin VV)")
            continue
        
        if vv_m.shape != reference_shape:
            zoom_factors = (reference_shape[0] / vv_m.shape[0], reference_shape[1] / vv_m.shape[1])
            vv_m = zoom(vv_m, zoom_factors, order=1)
        if vv_s.shape != reference_shape:
            zoom_factors = (reference_shape[0] / vv_s.shape[0], reference_shape[1] / vv_s.shape[1])
            vv_s = zoom(vv_s, zoom_factors, order=1)
        
        # Cargar entropy (con sufijo si es urbano)
        ent_m, _ = load_raster(os.path.join(pair_dir, f"entropy_master{suffix}.tif"))
        ent_s, _ = load_raster(os.path.join(pair_dir, f"entropy_slave{suffix}.tif"))
        
        if ent_m is None or ent_s is None:
            print("✗ (sin entropy)")
            continue
        
        if ent_m.shape != reference_shape:
            zoom_factors = (reference_shape[0] / ent_m.shape[0], reference_shape[1] / ent_m.shape[1])
            ent_m = zoom(ent_m, zoom_factors, order=1)
        if ent_s.shape != reference_shape:
            zoom_factors = (reference_shape[0] / ent_s.shape[0], reference_shape[1] / ent_s.shape[1])
            ent_s = zoom(ent_s, zoom_factors, order=1)
        
        coherence_list.append(coh)
        vv_master_list.append(vv_m)
        vv_slave_list.append(vv_s)
        entropy_master_list.append(ent_m)
        entropy_slave_list.append(ent_s)
        
        print("✓")
    
    if not coherence_list:
        print("\n  ✗ No se pudieron cargar datos válidos")
        logger.error(f"No se encontraron datos válidos para {pueblo_name}")
        return False
    
    print(f"\n  ✓ Cargados {len(coherence_list)} pares válidos")
    
    # Calcular estadísticas globales
    print("\n[2/5] Calculando estadísticas globales...")
    
    # Stack de arrays
    coherence_stack = np.stack(coherence_list, axis=0)
    vv_master_stack = np.stack(vv_master_list, axis=0)
    vv_slave_stack = np.stack(vv_slave_list, axis=0)
    entropy_master_stack = np.stack(entropy_master_list, axis=0)
    entropy_slave_stack = np.stack(entropy_slave_list, axis=0)
    
    # Coherencia: media temporal (baja coherencia persistente = sospechoso)
    coherence_mean = np.nanmean(coherence_stack, axis=0)
    print(f"  ✓ Coherencia media: min={np.nanmin(coherence_mean):.4f}, max={np.nanmax(coherence_mean):.4f}")
    
    # VV: desviación estándar temporal (alta variabilidad = sospechoso)
    vv_all = np.concatenate([vv_master_stack, vv_slave_stack], axis=0)
    vv_std = np.nanstd(vv_all, axis=0)
    print(f"  ✓ VV std: min={np.nanmin(vv_std):.4f}, max={np.nanmax(vv_std):.4f}")
    
    # Entropy: desviación estándar temporal
    entropy_all = np.concatenate([entropy_master_stack, entropy_slave_stack], axis=0)
    entropy_std = np.nanstd(entropy_all, axis=0)
    print(f"  ✓ Entropy std: min={np.nanmin(entropy_std):.4f}, max={np.nanmax(entropy_std):.4f}")
    
    # Normalizar características
    print("\n[3/5] Normalizando características...")
    coh_norm = 1 - normalize(coherence_mean)  # Invertir
    vv_norm = normalize(vv_std)
    entropy_norm = normalize(entropy_std)
    
    print(f"  ✓ Coherencia normalizada (invertida)")
    print(f"  ✓ VV std normalizada")
    print(f"  ✓ Entropy std normalizada")
    
    # Fusión ponderada
    print("\n[4/5] Calculando mapa de riesgo global...")
    risk_probability = (
        WEIGHTS['coherence'] * coh_norm +
        WEIGHTS['vv_variability'] * vv_norm +
        WEIGHTS['entropy_variability'] * entropy_norm
    )
    
    print(f"  ✓ Probabilidad calculada:")
    print(f"    Min: {np.nanmin(risk_probability):.3f}")
    print(f"    Max: {np.nanmax(risk_probability):.3f}")
    print(f"    Media: {np.nanmean(risk_probability):.3f}")
    print(f"    Std: {np.nanstd(risk_probability):.3f}")
    
    # Aplicar umbrales
    threshold_high = float(config.get('THRESHOLD_HIGH', 0.7))
    threshold_medium = float(config.get('THRESHOLD_MEDIUM', 0.5))
    min_cluster_size = int(config.get('MIN_CLUSTER_SIZE', 50))
    
    risk_high = risk_probability > threshold_high
    risk_medium = (risk_probability > threshold_medium) & (risk_probability <= threshold_high)
    
    n_high = np.sum(risk_high)
    n_medium = np.sum(risk_medium)
    
    print(f"\n  Píxeles riesgo alto (>{threshold_high}): {n_high} ({n_high/risk_probability.size*100:.2f}%)")
    print(f"  Píxeles riesgo medio ({threshold_medium}-{threshold_high}): {n_medium} ({n_medium/risk_probability.size*100:.2f}%)")
    
    # Filtrado espacial
    print("\n  Aplicando filtrado espacial...")
    risk_filtered = uniform_filter(risk_high.astype(float), size=5) > 0.4
    labeled_array, num_features = label(risk_filtered)
    print(f"  Clusters detectados: {num_features}")
    
    # Filtrar clusters pequeños
    for i in range(1, num_features + 1):
        cluster_size = np.sum(labeled_array == i)
        if cluster_size < min_cluster_size:
            labeled_array[labeled_array == i] = 0
    
    unique_labels = np.unique(labeled_array)
    num_features_filtered = len(unique_labels) - 1
    print(f"  Clusters filtrados (>{min_cluster_size} px): {num_features_filtered}")
    
    risk_final = labeled_array > 0
    
    # Guardar resultados
    print("\n[5/5] Guardando resultados...")
    
    # Crear directorio de salida según versión
    if is_urban:
        output_dir = os.path.join(pueblo_dir, "urban_products", "risk_map_global")
        stats_dir = os.path.join(pueblo_dir, "urban_products", "stats_global")
    else:
        output_dir = os.path.join(pueblo_dir, "risk_map_global")
        stats_dir = os.path.join(pueblo_dir, "stats_global")
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(stats_dir).mkdir(parents=True, exist_ok=True)
    
    reference_profile.update(
        dtype='float32',
        count=1,
        compress='lzw',
        nodata=np.nan
    )
    
    # Mapa de probabilidad
    prob_file = os.path.join(output_dir, "risk_probability_global.tif")
    with rasterio.open(prob_file, 'w', **reference_profile) as dst:
        dst.write(risk_probability.astype('float32'), 1)
    print(f"  ✓ {prob_file}")
    
    # Máscaras
    profile_mask = reference_profile.copy()
    profile_mask.update(dtype='uint8', nodata=0)
    
    high_file = os.path.join(output_dir, "risk_high_global.tif")
    with rasterio.open(high_file, 'w', **profile_mask) as dst:
        dst.write(risk_final.astype('uint8'), 1)
    print(f"  ✓ {high_file}")
    
    medium_file = os.path.join(output_dir, "risk_medium_global.tif")
    with rasterio.open(medium_file, 'w', **profile_mask) as dst:
        dst.write(risk_medium.astype('uint8'), 1)
    print(f"  ✓ {medium_file}")
    
    # Clusters
    profile_clusters = reference_profile.copy()
    profile_clusters.update(dtype='int32', nodata=0)
    
    clusters_file = os.path.join(output_dir, "risk_clusters_global.tif")
    with rasterio.open(clusters_file, 'w', **profile_clusters) as dst:
        dst.write(labeled_array.astype('int32'), 1)
    print(f"  ✓ {clusters_file}")
    
    # Guardar estadísticas agregadas
    print("\n  Guardando estadísticas agregadas intermedias...")
    
    # Ya creado arriba según versión
    
    coh_mean_file = os.path.join(stats_dir, "coherence_mean.tif")
    with rasterio.open(coh_mean_file, 'w', **reference_profile) as dst:
        dst.write(coherence_mean.astype('float32'), 1)
    print(f"  ✓ {coh_mean_file}")
    
    vv_std_file = os.path.join(stats_dir, "vv_std.tif")
    with rasterio.open(vv_std_file, 'w', **reference_profile) as dst:
        dst.write(vv_std.astype('float32'), 1)
    print(f"  ✓ {vv_std_file}")
    
    entropy_mean_file = os.path.join(stats_dir, "entropy_mean.tif")
    with rasterio.open(entropy_mean_file, 'w', **reference_profile) as dst:
        dst.write(entropy_std.astype('float32'), 1)
    print(f"  ✓ {entropy_mean_file}")
    
    # Visualización
    print("\n  Generando visualización...")
    
    fig, axes = plt.subplots(2, 3, figsize=(20, 13))
    fig.suptitle(f'Mapa de Riesgo Global - {pueblo_name.upper()} - {len(coherence_list)} pares', 
                 fontsize=18, fontweight='bold')
    
    # Coherencia media
    im1 = axes[0, 0].imshow(coherence_mean, cmap='RdYlGn', vmin=0, vmax=1)
    axes[0, 0].set_title('Coherencia Media Temporal', fontsize=14, fontweight='bold')
    axes[0, 0].axis('off')
    plt.colorbar(im1, ax=axes[0, 0], fraction=0.046, pad=0.04)
    
    # VV std
    im2 = axes[0, 1].imshow(vv_std, cmap='plasma')
    axes[0, 1].set_title('Desv. Std. VV Temporal', fontsize=14, fontweight='bold')
    axes[0, 1].axis('off')
    plt.colorbar(im2, ax=axes[0, 1], fraction=0.046, pad=0.04)
    
    # Entropy std
    im3 = axes[0, 2].imshow(entropy_std, cmap='viridis')
    axes[0, 2].set_title('Desv. Std. Entropy Temporal', fontsize=14, fontweight='bold')
    axes[0, 2].axis('off')
    plt.colorbar(im3, ax=axes[0, 2], fraction=0.046, pad=0.04)
    
    # Probabilidad
    im4 = axes[1, 0].imshow(risk_probability, cmap='hot', vmin=0, vmax=1)
    axes[1, 0].set_title('Probabilidad de Riesgo Global', fontsize=14, fontweight='bold')
    axes[1, 0].axis('off')
    cbar4 = plt.colorbar(im4, ax=axes[1, 0], fraction=0.046, pad=0.04)
    cbar4.set_label('Probabilidad [0-1]', rotation=270, labelpad=20)
    
    # Alto riesgo
    im5 = axes[1, 1].imshow(risk_final, cmap='Reds')
    axes[1, 1].set_title(f'Alto Riesgo (>{threshold_high})', fontsize=14, fontweight='bold')
    axes[1, 1].axis('off')
    
    # Clusters
    im6 = axes[1, 2].imshow(labeled_array, cmap='tab20')
    axes[1, 2].set_title(f'Clusters: {num_features_filtered}', fontsize=14, fontweight='bold')
    axes[1, 2].axis('off')
    
    plt.tight_layout()
    viz_file = os.path.join(output_dir, "visualization_global.png")
    plt.savefig(viz_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  ✓ {viz_file}")
    
    # Estadísticas
    total_pixels = risk_probability.size
    risk_pixels = np.sum(risk_final)
    risk_area_m2 = risk_pixels * 100
    risk_area_ha = risk_area_m2 / 10000
    
    # Extraer fechas del periodo
    pair_dates = []
    for pair_dir in pair_dirs:
        pair_name = os.path.basename(pair_dir)
        if pair_name.startswith('pair_'):
            dates = pair_name.replace('pair_', '').split('_')
            pair_dates.extend(dates)
    
    if pair_dates:
        first_date = min(pair_dates)
        last_date = max(pair_dates)
        period = f"{first_date} - {last_date}"
    else:
        period = "Desconocido"
    
    stats = {
        'pueblo': pueblo_name,
        'num_pairs': len(coherence_list),
        'period': period,
        'method': 'global_weighted_fusion',
        'weights': WEIGHTS,
        'thresholds': {
            'high': threshold_high,
            'medium': threshold_medium
        },
        'total_pixels': int(total_pixels),
        'risk_pixels_high': int(risk_pixels),
        'risk_pixels_medium': int(n_medium),
        'risk_percentage_high': float(risk_pixels/total_pixels*100),
        'risk_percentage_medium': float(n_medium/total_pixels*100),
        'risk_area_m2': float(risk_area_m2),
        'risk_area_ha': float(risk_area_ha),
        'num_clusters': int(num_features_filtered),
        'probability_stats': {
            'min': float(np.nanmin(risk_probability)),
            'max': float(np.nanmax(risk_probability)),
            'mean': float(np.nanmean(risk_probability)),
            'std': float(np.nanstd(risk_probability))
        }
    }
    
    stats_file = os.path.join(output_dir, "statistics_global.json")
    with open(stats_file, 'w') as f:
        json.dump(stats, f, indent=2)
    print(f"  ✓ {stats_file}")
    
    # Reporte texto
    report_file = os.path.join(output_dir, "report_global.txt")
    with open(report_file, 'w') as f:
        f.write(f"MAPA DE RIESGO GLOBAL - {pueblo_name.upper()}\n")
        f.write("="*80 + "\n\n")
        f.write(f"Periodo: {period}\n")
        f.write(f"Número de pares procesados: {len(coherence_list)}\n\n")
        f.write(f"Píxeles totales: {total_pixels:,}\n")
        f.write(f"Píxeles riesgo alto: {risk_pixels:,} ({risk_pixels/total_pixels*100:.2f}%)\n")
        f.write(f"Píxeles riesgo medio: {n_medium:,} ({n_medium/total_pixels*100:.2f}%)\n")
        f.write(f"Área riesgo alto: {risk_area_m2:.0f} m² ({risk_area_ha:.2f} ha)\n")
        f.write(f"Clusters detectados: {num_features_filtered}\n\n")
        
        if num_features_filtered > 0:
            f.write("Top 10 Clusters:\n")
            cluster_sizes = []
            for i in range(1, num_features_filtered + 1):
                cluster_size = np.sum(labeled_array == i)
                if cluster_size > 0:
                    cluster_sizes.append((i, cluster_size))
            cluster_sizes.sort(key=lambda x: x[1], reverse=True)
            
            for i, (cluster_id, cluster_size) in enumerate(cluster_sizes[:10], 1):
                cluster_area = cluster_size * 100
                f.write(f"  {i}. Cluster {cluster_id}: {cluster_size} px ({cluster_area:.0f} m²)\n")
    
    print(f"  ✓ {report_file}")
    
    logger.info(f"Mapa global {pueblo_name} completado: {num_features_filtered} clusters, {risk_pixels} píxeles alto riesgo")
    
    print(f"\n✓ Pueblo {pueblo_name} completado")
    print(f"  Periodo: {period}")
    print(f"  Pares procesados: {len(coherence_list)}")
    print(f"  Clusters detectados: {num_features_filtered}")
    
    return True

def main():
    print("="*80)
    print("GENERACIÓN DE MAPAS DE RIESGO GLOBALES (TODO EL PERIODO)")
    print("="*80)
    
    config = load_config()
    
    # Buscar pueblos
    pueblos = sorted(glob("processing/*"))
    pueblos = [p for p in pueblos if os.path.isdir(p) and not p.endswith('__pycache__')]
    
    if not pueblos:
        print("\n✗ No se encontraron pueblos en processing/")
        sys.exit(1)
    
    print(f"\nEncontrados {len(pueblos)} pueblos\n")
    
    total_processed = 0
    total_failed = 0
    
    for pueblo_dir in pueblos:
        pueblo_name = os.path.basename(pueblo_dir)
        
        # Setup logger
        logger = LoggerConfig.setup_aoi_logger(
            aoi_project_dir=pueblo_dir,
            log_name="risk_map_global"
        )
        
        # Verificar qué versiones existen
        has_full = len(glob(f"{pueblo_dir}/insar_*/fusion/pairs/pair_*")) > 0
        has_urban = len(glob(f"{pueblo_dir}/urban_products/insar_*/fusion/pairs/pair_*")) > 0
        
        if not has_full and not has_urban:
            print(f"⊘ {pueblo_name}: Sin productos procesados, saltando...")
            continue
        
        # Procesar versión completa si existe
        if has_full:
            try:
                print(f"\n→ {pueblo_name}: Procesando versión COMPLETA...")
                if process_pueblo_global(pueblo_dir, config, logger, force_urban=False):
                    total_processed += 1
                    print(f"  ✓ Mapa global completo generado")
                else:
                    total_failed += 1
            except Exception as e:
                print(f"\n✗ ERROR procesando {pueblo_name} (completo): {e}")
                logger.error(f"Error generando mapa global completo: {e}")
                total_failed += 1
        
        # Procesar versión urbana si existe
        if has_urban:
            try:
                print(f"\n→ {pueblo_name}: Procesando versión URBANA...")
                if process_pueblo_global(pueblo_dir, config, logger, force_urban=True):
                    total_processed += 1
                    print(f"  ✓ Mapa global urbano generado")
                else:
                    total_failed += 1
            except Exception as e:
                print(f"\n✗ ERROR procesando {pueblo_name} (urbano): {e}")
                logger.error(f"Error generando mapa global urbano: {e}")
                total_failed += 1
    
    # Resumen final
    print("\n" + "="*80)
    print("RESUMEN FINAL")
    print("="*80)
    print(f"Mapas procesados: {total_processed}")
    print(f"Con errores: {total_failed}")
    print(f"Total: {total_processed + total_failed}")
    print("\n✓ Proceso completado")
    print("\nLos mapas globales de riesgo se encuentran en:")
    print("  • Completos: processing/<pueblo>/risk_map_global/")
    print("  • Urbanos:   processing/<pueblo>/urban_products/risk_map_global/")
    print("\nEstadísticas agregadas guardadas en:")
    print("  • Completos: processing/<pueblo>/stats_global/*.tif")
    print("  • Urbanos:   processing/<pueblo>/urban_products/stats_global/*.tif")

if __name__ == "__main__":
    main()
