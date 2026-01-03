#!/usr/bin/env python3
"""
Script: generate_risk_maps_per_pair.py
Descripción: Genera mapas de riesgo de fugas de agua para cada par de fechas
Uso: python3 scripts/generate_risk_maps_per_pair.py
"""

import numpy as np
import rasterio
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.ndimage import uniform_filter, label
from pathlib import Path
import json
import os
import sys
from glob import glob

# Importar sistema de logging
sys.path.append(os.path.dirname(__file__))
from logging_utils import LoggerConfig

# Configuración
CONFIG_FILE = "config.txt"

# Pesos de fusión
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
        print(f"  ✗ ERROR cargando {file_path}: {e}")
        return None, None

def process_pair(pair_dir, config, logger):
    """Procesa un par de fechas y genera mapa de riesgo"""
    pair_name = os.path.basename(pair_dir)
    print(f"\n{'='*80}")
    print(f"Procesando: {pair_name}")
    print(f"{'='*80}")
    
    logger.info(f"Procesando par: {pair_name}")
    
    # Crear directorio de salida
    output_dir = os.path.join(pair_dir, "risk_map")
    Path(output_dir).mkdir(exist_ok=True)
    
    # Cargar coherencia (con sufijo _urban si estamos en urban_products)
    is_urban = '/urban_products/' in pair_dir
    suffix = '_urban' if is_urban else ''
    
    coh_file = os.path.join(pair_dir, f"coherence{suffix}.tif")
    if not os.path.exists(coh_file):
        print(f"  ✗ No existe {coh_file}")
        logger.warning(f"No existe coherence{suffix}.tif en {pair_name}")
        return False
    
    coherence, profile = load_raster(coh_file)
    if coherence is None:
        return False
    print(f"  ✓ Coherencia cargada: {coherence.shape}")
    
    # Cargar VV master y slave
    vv_master_file = os.path.join(pair_dir, f"vv_master{suffix}.tif")
    vv_slave_file = os.path.join(pair_dir, f"vv_slave{suffix}.tif")
    
    if not os.path.exists(vv_master_file) or not os.path.exists(vv_slave_file):
        print(f"  ✗ Faltan archivos VV")
        logger.warning(f"Faltan archivos VV en {pair_name}")
        return False
    
    vv_master, _ = load_raster(vv_master_file)
    vv_slave, _ = load_raster(vv_slave_file)
    
    if vv_master is None or vv_slave is None:
        return False
    
    print(f"  ✓ VV master/slave cargados: {vv_master.shape}, {vv_slave.shape}")
    
    # Asegurar que todas las dimensiones coincidan
    target_shape = coherence.shape
    
    if vv_master.shape != target_shape:
        from scipy.ndimage import zoom
        zoom_factors = (target_shape[0] / vv_master.shape[0], target_shape[1] / vv_master.shape[1])
        vv_master = zoom(vv_master, zoom_factors, order=1)
        print(f"  ✓ VV master remuestreado a {target_shape}")
    
    if vv_slave.shape != target_shape:
        from scipy.ndimage import zoom
        zoom_factors = (target_shape[0] / vv_slave.shape[0], target_shape[1] / vv_slave.shape[1])
        vv_slave = zoom(vv_slave, zoom_factors, order=1)
        print(f"  ✓ VV slave remuestreado a {target_shape}")
    
    # Calcular variabilidad VV (diferencia absoluta normalizada)
    vv_diff = np.abs(vv_slave - vv_master) / (np.abs(vv_master) + 1e-10)
    print(f"  ✓ Variabilidad VV calculada")
    
    # Cargar entropy master y slave (con sufijo si es urbano)
    ent_master_file = os.path.join(pair_dir, f"entropy_master{suffix}.tif")
    ent_slave_file = os.path.join(pair_dir, f"entropy_slave{suffix}.tif")
    
    if not os.path.exists(ent_master_file) or not os.path.exists(ent_slave_file):
        print(f"  ✗ Faltan archivos entropy")
        logger.warning(f"Faltan archivos entropy en {pair_name}")
        return False
    
    ent_master, _ = load_raster(ent_master_file)
    ent_slave, _ = load_raster(ent_slave_file)
    
    if ent_master is None or ent_slave is None:
        return False
    
    print(f"  ✓ Entropy master/slave cargados: {ent_master.shape}, {ent_slave.shape}")
    
    if ent_master.shape != target_shape:
        from scipy.ndimage import zoom
        zoom_factors = (target_shape[0] / ent_master.shape[0], target_shape[1] / ent_master.shape[1])
        ent_master = zoom(ent_master, zoom_factors, order=1)
        print(f"  ✓ Entropy master remuestreado a {target_shape}")
    
    if ent_slave.shape != target_shape:
        from scipy.ndimage import zoom
        zoom_factors = (target_shape[0] / ent_slave.shape[0], target_shape[1] / ent_slave.shape[1])
        ent_slave = zoom(ent_slave, zoom_factors, order=1)
        print(f"  ✓ Entropy slave remuestreado a {target_shape}")
    
    # Calcular variabilidad entropy
    ent_diff = np.abs(ent_slave - ent_master)
    print(f"  ✓ Variabilidad entropy calculada")
    
    # Normalizar características
    print("\nNormalizando características...")
    coh_norm = 1 - normalize(coherence)  # Invertir: baja coherencia = sospechoso
    vv_var_norm = normalize(vv_diff)
    ent_var_norm = normalize(ent_diff)
    
    print(f"  ✓ Coherencia normalizada (min={np.nanmin(coh_norm):.3f}, max={np.nanmax(coh_norm):.3f})")
    print(f"  ✓ VV variabilidad normalizada (min={np.nanmin(vv_var_norm):.3f}, max={np.nanmax(vv_var_norm):.3f})")
    print(f"  ✓ Entropy variabilidad normalizada (min={np.nanmin(ent_var_norm):.3f}, max={np.nanmax(ent_var_norm):.3f})")
    
    # Fusión ponderada
    print("\nCalculando probabilidad de riesgo...")
    risk_probability = (
        WEIGHTS['coherence'] * coh_norm +
        WEIGHTS['vv_variability'] * vv_var_norm +
        WEIGHTS['entropy_variability'] * ent_var_norm
    )
    
    print(f"  ✓ Probabilidad calculada:")
    print(f"    Min: {np.nanmin(risk_probability):.3f}")
    print(f"    Max: {np.nanmax(risk_probability):.3f}")
    print(f"    Media: {np.nanmean(risk_probability):.3f}")
    
    # Aplicar umbrales
    threshold_high = float(config.get('THRESHOLD_HIGH', 0.7))
    threshold_medium = float(config.get('THRESHOLD_MEDIUM', 0.5))
    min_cluster_size = int(config.get('MIN_CLUSTER_SIZE', 50))
    
    print(f"\nAplicando umbrales (alto={threshold_high}, medio={threshold_medium})...")
    
    risk_high = risk_probability > threshold_high
    risk_medium = (risk_probability > threshold_medium) & (risk_probability <= threshold_high)
    
    n_high = np.sum(risk_high)
    n_medium = np.sum(risk_medium)
    
    print(f"  Píxeles riesgo alto: {n_high} ({n_high/risk_probability.size*100:.2f}%)")
    print(f"  Píxeles riesgo medio: {n_medium} ({n_medium/risk_probability.size*100:.2f}%)")
    
    # Filtrado espacial
    print("\nAplicando filtrado espacial...")
    risk_filtered = uniform_filter(risk_high.astype(float), size=5) > 0.4
    
    # Etiquetar clusters
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
    print("\nGuardando resultados...")
    
    profile.update(
        dtype='float32',
        count=1,
        compress='lzw',
        nodata=np.nan
    )
    
    # Mapa de probabilidad
    prob_file = os.path.join(output_dir, "risk_probability.tif")
    with rasterio.open(prob_file, 'w', **profile) as dst:
        dst.write(risk_probability.astype('float32'), 1)
    print(f"  ✓ {prob_file}")
    
    # Máscara alto riesgo
    profile_mask = profile.copy()
    profile_mask.update(dtype='uint8', nodata=0)
    
    high_file = os.path.join(output_dir, "risk_high.tif")
    with rasterio.open(high_file, 'w', **profile_mask) as dst:
        dst.write(risk_final.astype('uint8'), 1)
    print(f"  ✓ {high_file}")
    
    # Máscara medio riesgo
    medium_file = os.path.join(output_dir, "risk_medium.tif")
    with rasterio.open(medium_file, 'w', **profile_mask) as dst:
        dst.write(risk_medium.astype('uint8'), 1)
    print(f"  ✓ {medium_file}")
    
    # Clusters
    profile_clusters = profile.copy()
    profile_clusters.update(dtype='int32', nodata=0)
    
    clusters_file = os.path.join(output_dir, "risk_clusters.tif")
    with rasterio.open(clusters_file, 'w', **profile_clusters) as dst:
        dst.write(labeled_array.astype('int32'), 1)
    print(f"  ✓ {clusters_file}")
    
    # Visualización
    print("\nGenerando visualización...")
    
    fig, axes = plt.subplots(2, 3, figsize=(18, 12))
    fig.suptitle(f'Mapa de Riesgo - {pair_name}', fontsize=16, fontweight='bold')
    
    # Coherencia
    im1 = axes[0, 0].imshow(coherence, cmap='RdYlGn', vmin=0, vmax=1)
    axes[0, 0].set_title('Coherencia', fontsize=12, fontweight='bold')
    axes[0, 0].axis('off')
    plt.colorbar(im1, ax=axes[0, 0], fraction=0.046, pad=0.04)
    
    # VV variabilidad
    im2 = axes[0, 1].imshow(vv_diff, cmap='plasma')
    axes[0, 1].set_title('Variabilidad VV', fontsize=12, fontweight='bold')
    axes[0, 1].axis('off')
    plt.colorbar(im2, ax=axes[0, 1], fraction=0.046, pad=0.04)
    
    # Entropy variabilidad
    im3 = axes[0, 2].imshow(ent_diff, cmap='viridis')
    axes[0, 2].set_title('Variabilidad Entropy', fontsize=12, fontweight='bold')
    axes[0, 2].axis('off')
    plt.colorbar(im3, ax=axes[0, 2], fraction=0.046, pad=0.04)
    
    # Probabilidad de riesgo
    im4 = axes[1, 0].imshow(risk_probability, cmap='hot', vmin=0, vmax=1)
    axes[1, 0].set_title('Probabilidad de Riesgo', fontsize=12, fontweight='bold')
    axes[1, 0].axis('off')
    cbar4 = plt.colorbar(im4, ax=axes[1, 0], fraction=0.046, pad=0.04)
    cbar4.set_label('Probabilidad [0-1]', rotation=270, labelpad=15)
    
    # Alto riesgo
    im5 = axes[1, 1].imshow(risk_final, cmap='Reds')
    axes[1, 1].set_title(f'Alto Riesgo (>{threshold_high})', fontsize=12, fontweight='bold')
    axes[1, 1].axis('off')
    
    # Clusters
    im6 = axes[1, 2].imshow(labeled_array, cmap='tab20')
    axes[1, 2].set_title(f'Clusters: {num_features_filtered}', fontsize=12, fontweight='bold')
    axes[1, 2].axis('off')
    
    plt.tight_layout()
    viz_file = os.path.join(output_dir, "visualization.png")
    plt.savefig(viz_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  ✓ {viz_file}")
    
    # Estadísticas
    total_pixels = risk_probability.size
    risk_pixels = np.sum(risk_final)
    risk_area_m2 = risk_pixels * 100
    risk_area_ha = risk_area_m2 / 10000
    
    stats = {
        'pair_name': pair_name,
        'method': 'weighted_fusion',
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
    
    stats_file = os.path.join(output_dir, "statistics.json")
    with open(stats_file, 'w') as f:
        json.dump(stats, f, indent=2)
    print(f"  ✓ {stats_file}")
    
    # Reporte texto
    report_file = os.path.join(output_dir, "report.txt")
    with open(report_file, 'w') as f:
        f.write(f"MAPA DE RIESGO DE FUGAS - {pair_name}\n")
        f.write("="*80 + "\n\n")
        f.write(f"Píxeles totales: {total_pixels:,}\n")
        f.write(f"Píxeles riesgo alto: {risk_pixels:,} ({risk_pixels/total_pixels*100:.2f}%)\n")
        f.write(f"Píxeles riesgo medio: {n_medium:,} ({n_medium/total_pixels*100:.2f}%)\n")
        f.write(f"Área riesgo alto: {risk_area_m2:.0f} m² ({risk_area_ha:.2f} ha)\n")
        f.write(f"Clusters detectados: {num_features_filtered}\n\n")
        
        if num_features_filtered > 0:
            f.write("Top 5 Clusters:\n")
            cluster_sizes = []
            for i in range(1, num_features_filtered + 1):
                cluster_size = np.sum(labeled_array == i)
                if cluster_size > 0:
                    cluster_sizes.append((i, cluster_size))
            cluster_sizes.sort(key=lambda x: x[1], reverse=True)
            
            for i, (cluster_id, cluster_size) in enumerate(cluster_sizes[:5], 1):
                cluster_area = cluster_size * 100
                f.write(f"  {i}. Cluster {cluster_id}: {cluster_size} px ({cluster_area:.0f} m²)\n")
    
    print(f"  ✓ {report_file}")
    
    logger.info(f"Par {pair_name} procesado: {num_features_filtered} clusters, {risk_pixels} píxeles riesgo alto")
    
    print(f"\n✓ Par {pair_name} completado")
    return True

def main():
    print("="*80)
    print("GENERACIÓN DE MAPAS DE RIESGO POR PAR DE FECHAS")
    print("="*80)
    
    config = load_config()
    
    # Buscar todas las carpetas de pairs (completos y urbanos)
    pair_dirs_full = sorted(glob("processing/*/insar_*/fusion/pairs/pair_*"))
    pair_dirs_urban = sorted(glob("processing/*/urban_products/insar_*/fusion/pairs/pair_*"))
    
    # Combinar ambos, priorizando urbanos si existen ambos
    all_pairs = {}
    
    # Primero añadir completos
    for path in pair_dirs_full:
        key = path.replace('/insar_', '|').split('|')[0] + '|' + path.split('pair_')[-1]
        all_pairs[key] = {'full': path, 'urban': None}
    
    # Luego añadir urbanos
    for path in pair_dirs_urban:
        base_path = path.replace('/urban_products', '').replace('_urban', '')
        key = base_path.replace('/insar_', '|').split('|')[0] + '|' + path.split('pair_')[-1]
        if key in all_pairs:
            all_pairs[key]['urban'] = path
        else:
            all_pairs[key] = {'full': None, 'urban': path}
    
    if not all_pairs:
        print("\n✗ No se encontraron carpetas de pares")
        print("Estructura esperada:")
        print("  - processing/<pueblo>/insar_*/fusion/pairs/pair_YYYYMMDD_YYYYMMDD/")
        print("  - processing/<pueblo>/urban_products/insar_*/fusion/pairs/pair_YYYYMMDD_YYYYMMDD/")
        sys.exit(1)
    
    # Contar cuántos de cada tipo
    n_full = sum(1 for v in all_pairs.values() if v['full'])
    n_urban = sum(1 for v in all_pairs.values() if v['urban'])
    n_both = sum(1 for v in all_pairs.values() if v['full'] and v['urban'])
    
    print(f"\nEncontrados:")
    print(f"  • {n_full} pares completos")
    print(f"  • {n_urban} pares urbanos")
    print(f"  • {n_both} pares con ambas versiones")
    print()
    
    # Agrupar por pueblo
    pueblos = {}
    for key, paths in all_pairs.items():
        # Extraer pueblo del path
        if paths['full']:
            pueblo = paths['full'].split('/')[1]
        else:
            pueblo = paths['urban'].split('/')[1]
        
        if pueblo not in pueblos:
            pueblos[pueblo] = {'full': [], 'urban': []}
        
        # Añadir a lista de procesamiento
        if paths['full']:
            pueblos[pueblo]['full'].append(paths['full'])
        if paths['urban']:
            pueblos[pueblo]['urban'].append(paths['urban'])
    
    # Procesar cada pueblo
    total_processed = 0
    total_failed = 0
    
    for pueblo, pair_types in pueblos.items():
        n_full = len(pair_types['full'])
        n_urban = len(pair_types['urban'])
        
        print(f"\n{'#'*80}")
        print(f"# PUEBLO: {pueblo.upper()}")
        if n_full and n_urban:
            print(f"# {n_full} pares completos + {n_urban} pares urbanos")
        elif n_full:
            print(f"# {n_full} pares completos")
        else:
            print(f"# {n_urban} pares urbanos")
        print(f"{'#'*80}")
        
        # Setup logger para el pueblo
        aoi_project_dir = f"processing/{pueblo}"
        logger = LoggerConfig.setup_aoi_logger(
            aoi_project_dir=aoi_project_dir,
            log_name="risk_maps"
        )
        
        logger.info("="*80)
        logger.info(f"Generando mapas de riesgo para {pueblo}")
        logger.info("="*80)
        
        # Procesar pares completos
        for pair_dir in pair_types['full']:
            try:
                if process_pair(pair_dir, config, logger):
                    total_processed += 1
                else:
                    total_failed += 1
            except Exception as e:
                print(f"\n✗ ERROR procesando {pair_dir}: {e}")
                logger.error(f"Error procesando {os.path.basename(pair_dir)}: {e}")
                total_failed += 1
        
        # Procesar pares urbanos
        for pair_dir in pair_types['urban']:
            try:
                if process_pair(pair_dir, config, logger):
                    total_processed += 1
                else:
                    total_failed += 1
            except Exception as e:
                print(f"\n✗ ERROR procesando {pair_dir}: {e}")
                logger.error(f"Error procesando {os.path.basename(pair_dir)}: {e}")
                total_failed += 1
    
    # Resumen final
    print("\n" + "="*80)
    print("RESUMEN FINAL")
    print("="*80)
    print(f"Pares procesados exitosamente: {total_processed}")
    print(f"Pares con errores: {total_failed}")
    print(f"Total: {total_processed + total_failed}")
    print("\n✓ Proceso completado")
    print("\nLos mapas de riesgo se encuentran en:")
    print("  • Completos: processing/<pueblo>/insar_*/fusion/pairs/pair_*/risk_map/")
    print("  • Urbanos:   processing/<pueblo>/urban_products/insar_*/fusion/pairs/pair_*/risk_map/")

if __name__ == "__main__":
    main()
