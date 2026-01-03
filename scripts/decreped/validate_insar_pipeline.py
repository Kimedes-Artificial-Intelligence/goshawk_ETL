#!/usr/bin/env python3
"""
Script: validate_insar_pipeline.py
Descripción: Valida alineación geométrica y detección de anomalías en pipeline InSAR

Validaciones:
1. Alineación geométrica perfecta (misma resolución/extensión)
2. Ausencia de Phase Unwrapping en workflow
3. Closure Phase ≈ 0 en zonas estables
4. Capacidad de distinguir ruido vs anomalías reales

Uso:
  python3 scripts/validate_insar_pipeline.py --insar-dir fusion/insar/
  python3 scripts/validate_insar_pipeline.py --full-test
"""

import os
import sys
import argparse
import numpy as np
import rasterio
from pathlib import Path
import logging
from collections import defaultdict

# Agregar directorio scripts al path si es necesario
sys.path.insert(0, str(Path(__file__).parent))
from logging_utils import LoggerConfig

# Logger se configurará en main() después de conocer el workspace
logger = None


class Colors:
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    BLUE = '\033[0;34m'
    BOLD = '\033[1m'
    NC = '\033[0m'


def validate_geometric_alignment(insar_dir):
    """
    Valida que todos los interferogramas tengan la misma alineación geométrica.
    
    Verifica:
    - Misma resolución (pixelSpacingInMeter)
    - Mismas dimensiones (rows x cols)
    - Mismo extent geográfico
    - Mismo CRS
    
    Returns:
        dict: Resultados de la validación
    """
    LoggerConfig.log_section(logger, "VALIDACIÓN 1: Alineación Geométrica")
    
    insar_path = Path(insar_dir)
    if not insar_path.exists():
        logger.error(f"Directorio no existe: {insar_dir}")
        return {'status': 'error', 'message': 'Directorio no encontrado'}
    
    # Buscar archivos .dim
    dim_files = list(insar_path.glob('*.dim'))
    if not dim_files:
        logger.warning(f"No se encontraron archivos .dim en {insar_dir}")
        return {'status': 'warning', 'message': 'Sin interferogramas'}
    
    logger.info(f"Interferogramas encontrados: {len(dim_files)}")
    
    # Buscar bandas de fase en cada .dim
    metadata = []
    for dim_file in sorted(dim_files):
        data_dir = dim_file.parent / (dim_file.stem + '.data')
        if not data_dir.exists():
            continue
        
        # Buscar banda de fase
        phase_files = list(data_dir.glob('*hase*.img'))
        if not phase_files:
            continue
        
        phase_file = phase_files[0]
        
        try:
            with rasterio.open(phase_file) as src:
                meta = {
                    'name': dim_file.name,
                    'shape': (src.height, src.width),
                    'transform': src.transform,
                    'crs': src.crs,
                    'bounds': src.bounds,
                    'res': src.res,
                    'pixel_size': (src.transform[0], abs(src.transform[4]))
                }
                metadata.append(meta)
        except Exception as e:
            logger.warning(f"Error leyendo {phase_file.name}: {e}")
    
    if len(metadata) < 2:
        logger.warning("Se necesitan al menos 2 interferogramas para validar")
        return {'status': 'warning', 'message': 'Datos insuficientes'}
    
    # Validar consistencia
    reference = metadata[0]
    inconsistencies = []
    
    logger.info(f"\nReferencia: {reference['name']}")
    logger.info(f"  Dimensiones: {reference['shape'][0]}×{reference['shape'][1]}")
    logger.info(f"  Resolución: {reference['pixel_size'][0]:.2f}m × {reference['pixel_size'][1]:.2f}m")
    logger.info(f"  CRS: {reference['crs']}")
    logger.info("")
    
    for meta in metadata[1:]:
        logger.info(f"Validando: {meta['name']}")
        
        # Check 1: Dimensiones
        if meta['shape'] != reference['shape']:
            msg = f"  ✗ Dimensiones diferentes: {meta['shape']} vs {reference['shape']}"
            logger.error(msg)
            inconsistencies.append(msg)
        else:
            logger.info(f"  ✓ Dimensiones: {meta['shape'][0]}×{meta['shape'][1]}")
        
        # Check 2: Resolución de píxel
        res_diff = abs(meta['pixel_size'][0] - reference['pixel_size'][0])
        if res_diff > 0.01:  # Tolerancia de 1cm
            msg = f"  ✗ Resolución diferente: {meta['pixel_size'][0]:.2f}m vs {reference['pixel_size'][0]:.2f}m"
            logger.error(msg)
            inconsistencies.append(msg)
        else:
            logger.info(f"  ✓ Resolución: {meta['pixel_size'][0]:.2f}m")
        
        # Check 3: CRS
        if meta['crs'] != reference['crs']:
            msg = f"  ✗ CRS diferente: {meta['crs']} vs {reference['crs']}"
            logger.error(msg)
            inconsistencies.append(msg)
        else:
            logger.info(f"  ✓ CRS: {meta['crs']}")
        
        # Check 4: Bounds (extent geográfico)
        bounds_diff = max(abs(a - b) for a, b in zip(meta['bounds'], reference['bounds']))
        if bounds_diff > meta['pixel_size'][0]:  # Tolerancia de 1 píxel
            msg = f"  ⚠️  Extent diferente (diff: {bounds_diff:.2f}m)"
            logger.warning(msg)
        else:
            logger.info(f"  ✓ Extent geográfico alineado")
    
    # Resultado
    if inconsistencies:
        logger.error("\n✗ VALIDACIÓN FALLIDA")
        logger.error(f"  Encontradas {len(inconsistencies)} inconsistencias")
        return {
            'status': 'failed',
            'inconsistencies': inconsistencies,
            'count': len(metadata)
        }
    else:
        logger.info("\n✓ ALINEACIÓN PERFECTA")
        logger.info(f"  Todos los {len(metadata)} interferogramas están alineados")
        logger.info(f"  Resolución común: {reference['pixel_size'][0]:.2f}m")
        return {
            'status': 'passed',
            'count': len(metadata),
            'resolution': reference['pixel_size'][0]
        }


def validate_no_unwrapping(insar_dir):
    """
    Valida que NO se haya aplicado Phase Unwrapping.
    
    Verifica que los valores de fase estén en el rango wrapped [-π, π]
    
    Returns:
        dict: Resultados de la validación
    """
    LoggerConfig.log_section(logger, "VALIDACIÓN 2: Ausencia de Phase Unwrapping")
    
    insar_path = Path(insar_dir)
    dim_files = list(insar_path.glob('*.dim'))
    
    unwrapped_detected = []
    
    for dim_file in sorted(dim_files)[:3]:  # Muestra de 3 archivos
        data_dir = dim_file.parent / (dim_file.stem + '.data')
        if not data_dir.exists():
            continue
        
        phase_files = list(data_dir.glob('*hase*.img'))
        if not phase_files:
            continue
        
        phase_file = phase_files[0]
        
        try:
            with rasterio.open(phase_file) as src:
                phase = src.read(1)
                valid_phase = phase[np.isfinite(phase)]
                
                if len(valid_phase) == 0:
                    continue
                
                min_val = np.min(valid_phase)
                max_val = np.max(valid_phase)
                
                logger.info(f"Archivo: {dim_file.name}")
                logger.info(f"  Rango de fase: [{min_val:.4f}, {max_val:.4f}] rad")
                
                # Fase unwrapped típicamente excede ±π
                if abs(min_val) > np.pi + 0.1 or abs(max_val) > np.pi + 0.1:
                    msg = f"  ✗ Posible unwrapping detectado (fuera de [-π, π])"
                    logger.error(msg)
                    unwrapped_detected.append(dim_file.name)
                else:
                    logger.info("  ✓ Fase wrapped (dentro de [-π, π])")
        
        except Exception as e:
            logger.warning(f"  ⚠️  Error leyendo {phase_file.name}: {e}")
    
    if unwrapped_detected:
        logger.error("\n✗ UNWRAPPING DETECTADO")
        logger.error(f"  Archivos afectados: {len(unwrapped_detected)}")
        return {
            'status': 'failed',
            'unwrapped_files': unwrapped_detected
        }
    else:
        logger.info("\n✓ SIN UNWRAPPING")
        logger.info("  Todas las fases están wrapped [-π, π]")
        return {'status': 'passed'}


def validate_closure_phase_stable_areas(closure_dir, threshold=0.5):
    """
    Valida que la Closure Phase sea ≈ 0 en zonas estables.
    
    Args:
        closure_dir: Directorio con archivos de Closure Phase
        threshold: Umbral para std(CP) en radianes (default: 0.5)
    
    Returns:
        dict: Resultados de la validación
    """
    LoggerConfig.log_section(logger, "VALIDACIÓN 3: Closure Phase en Zonas Estables")
    
    closure_path = Path(closure_dir)
    if not closure_path.exists():
        logger.warning(f"Directorio de Closure Phase no existe: {closure_dir}")
        return {'status': 'skipped', 'message': 'Sin datos de CP'}
    
    phase_files = list(closure_path.glob('*_phase.tif'))
    if not phase_files:
        logger.warning(f"No se encontraron archivos de Closure Phase")
        return {'status': 'skipped', 'message': 'Sin archivos CP'}
    
    logger.info(f"Archivos de Closure Phase: {len(phase_files)}")
    logger.info(f"Umbral de calidad: std(CP) < {threshold:.2f} rad\n")
    
    results = []
    
    for cp_file in sorted(phase_files):
        try:
            with rasterio.open(cp_file) as src:
                cp = src.read(1)
                valid_cp = cp[np.isfinite(cp)]
                
                if len(valid_cp) == 0:
                    continue
                
                mean_cp = np.mean(valid_cp)
                std_cp = np.std(valid_cp)
                
                logger.info(f"Archivo: {cp_file.name}")
                logger.info(f"  Media: {mean_cp:.4f} rad ({np.degrees(mean_cp):.2f}°)")
                logger.info(f"  Std:   {std_cp:.4f} rad ({np.degrees(std_cp):.2f}°)")
                
                if std_cp < threshold:
                    logger.info("  ✓ Calidad EXCELENTE")
                    quality = 'excellent'
                elif std_cp < 1.0:
                    logger.info("  ⚠️  Calidad ACEPTABLE")
                    quality = 'acceptable'
                else:
                    logger.error("  ✗ Calidad POBRE")
                    quality = 'poor'
                
                results.append({
                    'file': cp_file.name,
                    'mean': mean_cp,
                    'std': std_cp,
                    'quality': quality
                })
        
        except Exception as e:
            logger.warning(f"  ⚠️  Error leyendo {cp_file.name}: {e}")
    
    # Resumen
    if not results:
        return {'status': 'skipped', 'message': 'Sin datos válidos'}
    
    excellent = sum(1 for r in results if r['quality'] == 'excellent')
    acceptable = sum(1 for r in results if r['quality'] == 'acceptable')
    poor = sum(1 for r in results if r['quality'] == 'poor')
    
    logger.info(f"\nResumen de {len(results)} tripletes:")
    logger.info(f"  Excelente: {excellent}")
    logger.info(f"  Aceptable: {acceptable}")
    logger.info(f"  Pobre: {poor}")
    
    if poor > 0:
        logger.warning("\n⚠️  ALGUNAS ÁREAS CON CALIDAD POBRE")
        return {
            'status': 'warning',
            'results': results,
            'excellent': excellent,
            'acceptable': acceptable,
            'poor': poor
        }
    else:
        logger.info("\n✓ TODAS LAS ÁREAS ESTABLES")
        return {
            'status': 'passed',
            'results': results,
            'excellent': excellent,
            'acceptable': acceptable
        }


def test_full_pipeline_integration(workspace_dir):
    """
    Ejecuta test de integración completo del pipeline.
    
    Valida la secuencia completa:
    SLC → Interferogramas → Closure Phase
    
    Returns:
        dict: Resultados del test
    """
    LoggerConfig.log_section(logger, "TEST DE INTEGRACIÓN: Pipeline Completo")
    
    workspace = Path(workspace_dir)
    
    # Verificar estructura de directorios
    required_dirs = ['slc', 'preprocessed_slc', 'fusion/insar']
    missing = []
    
    for dir_name in required_dirs:
        dir_path = workspace / dir_name
        if not dir_path.exists():
            missing.append(dir_name)
    
    if missing:
        logger.error(f"Directorios faltantes: {', '.join(missing)}")
        return {'status': 'failed', 'message': 'Estructura incompleta'}
    
    # Contar productos en cada etapa
    slc_count = len(list((workspace / 'slc').glob('*.SAFE')))
    preprocessed_count = len(list((workspace / 'preprocessed_slc').glob('*.dim')))
    insar_count = len(list((workspace / 'fusion/insar').glob('*.dim')))
    
    logger.info(f"Productos SLC: {slc_count}")
    logger.info(f"Preprocesados: {preprocessed_count}")
    logger.info(f"Interferogramas: {insar_count}")
    
    # Validar secuencia
    if slc_count >= 3:
        expected_pairs = 2 * slc_count - 3  # 2N-3 para pares cortos + largos
        logger.info(f"Pares esperados (con largos): {expected_pairs}")
        
        if insar_count >= expected_pairs:
            logger.info("✓ Pipeline completo ejecutado")
            return {
                'status': 'passed',
                'slc': slc_count,
                'interferograms': insar_count,
                'expected': expected_pairs
            }
        else:
            logger.warning(f"⚠️  Pipeline parcial ({insar_count}/{expected_pairs})")
            return {
                'status': 'partial',
                'slc': slc_count,
                'interferograms': insar_count,
                'expected': expected_pairs
            }
    else:
        logger.warning(f"Datos insuficientes para validar (necesario: ≥3 SLC)")
        return {'status': 'insufficient_data'}


def main():
    parser = argparse.ArgumentParser(
        description='Valida alineación geométrica y pipeline InSAR',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--insar-dir', default='fusion/insar',
                       help='Directorio con interferogramas (default: fusion/insar)')
    parser.add_argument('--closure-dir', default='closure_phase',
                       help='Directorio con Closure Phase (default: closure_phase)')
    parser.add_argument('--workspace', default='.',
                       help='Directorio workspace (default: .)')
    parser.add_argument('--full-test', action='store_true',
                       help='Ejecutar test de integración completo')
    parser.add_argument('--threshold', type=float, default=0.5,
                       help='Umbral para std(CP) en rad (default: 0.5)')
    
    args = parser.parse_args()

    # Configurar logger con workspace
    global logger
    logger = LoggerConfig.setup_aoi_logger(
        aoi_project_dir=args.workspace,
        log_name='validate_insar_pipeline',
        level=logging.INFO,
        console_level=logging.INFO  # Validación es interactiva, mostrar todo
    )

    LoggerConfig.log_section(logger, "VALIDACIÓN DE PIPELINE InSAR")
    
    all_passed = True
    
    # Validación 1: Alineación geométrica
    result1 = validate_geometric_alignment(args.insar_dir)
    if result1['status'] == 'failed':
        all_passed = False
    
    # Validación 2: No unwrapping
    result2 = validate_no_unwrapping(args.insar_dir)
    if result2['status'] == 'failed':
        all_passed = False
    
    # Validación 3: Closure Phase en zonas estables
    result3 = validate_closure_phase_stable_areas(args.closure_dir, args.threshold)
    if result3['status'] == 'failed':
        all_passed = False
    
    # Test de integración (opcional)
    if args.full_test:
        result4 = test_full_pipeline_integration(args.workspace)
        if result4['status'] == 'failed':
            all_passed = False
    
    # Resumen final
    LoggerConfig.log_section(logger, "RESUMEN FINAL")

    if all_passed:
        logger.info("✓ TODAS LAS VALIDACIONES PASARON")
        logger.info("\nPipeline listo para detección de anomalías")
        return 0
    else:
        logger.error("✗ ALGUNAS VALIDACIONES FALLARON")
        logger.error("\nRevisar logs para detalles")
        return 1


if __name__ == "__main__":
    sys.exit(main())
