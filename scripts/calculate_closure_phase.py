#!/usr/bin/env python3
"""
Script: calculate_closure_phase.py
Descripción: Calcula Closure Phase para detección de fugas de agua (PSLDA)

Referencia: Yan et al. (2024) - "Interpretable PCA and SVM-Based Leak Detection 
Algorithm for Identifying Water Leakage Using SAR-Derived Moisture Content and 
InSAR Closure Phase" - IEEE JSTARS, Vol. 17

Uso:
  python3 scripts/calculate_closure_phase.py <ifg_12> <ifg_23> <ifg_13> [--output <dir>]

Fórmula (exponenciales complejas, robusta para zonas urbanas):
  Φ' = angle(e^(iφ₁₂) × e^(iφ₂₃) × e^(-iφ₁₃))
     = wrap(φ₁₂ + φ₂₃ - φ₁₃)

Donde:
  φ₁₂ = fase del interferograma 1→2 (par corto)
  φ₂₃ = fase del interferograma 2→3 (par corto)
  φ₁₃ = fase del interferograma 1→3 (par largo)

Interpretación física:
  Φ' ≈ 0  → No hay cambio de humedad del suelo
  Φ' ≠ 0  → Cambio de humedad detectado (posible fuga)
  
El closure phase es inmune a:
  - Deformación superficial (d₁₂ + d₂₃ = d₁₃ → se cancela)
  - Fase atmosférica (misma razón)
  
Solo es sensible a cambios en la constante dieléctrica (humedad del suelo).

Output para PSLDA:
  - closure_phase: Φ' en radianes [-π, π]
  - abs_closure_phase: |Φ'| en radianes [0, π] ← USADO POR PSLDA
"""

import os
import sys
import argparse
import numpy as np
import rasterio
from pathlib import Path
import logging

# Agregar directorio scripts al path si es necesario
sys.path.insert(0, str(Path(__file__).parent))
from logging_utils import LoggerConfig

# Logger se configurará en main() después de conocer el directorio de salida
logger = None


def find_phase_band(dim_file):
    """
    Busca la banda de fase en el directorio .data asociado al .dim
    
    Args:
        dim_file: Ruta al archivo .dim
        
    Returns:
        Path: Ruta al archivo .img de la banda de fase
        
    Raises:
        FileNotFoundError: Si no se encuentra la banda de fase
    """
    dim_path = Path(dim_file)
    if not dim_path.exists():
        raise FileNotFoundError(f"Archivo .dim no existe: {dim_file}")
    
    # Directorio .data
    data_dir = dim_path.parent / (dim_path.stem + '.data')
    if not data_dir.exists():
        raise FileNotFoundError(f"Directorio .data no existe: {data_dir}")
    
    # Buscar banda de fase (diferentes convenciones de nombres)
    phase_patterns = [
        'Phase_ifg*.img',
        'phase*.img',
        '*phase*.img'
    ]
    
    for pattern in phase_patterns:
        phase_files = list(data_dir.glob(pattern))
        if phase_files:
            logger.info(f"  Banda de fase encontrada: {phase_files[0].name}")
            return phase_files[0]
    
    raise FileNotFoundError(
        f"No se encontró banda de fase en {data_dir}\n"
        f"Archivos disponibles: {list(data_dir.glob('*.img'))}"
    )


def validate_dimensions(phase_files):
    """
    Valida que los tres interferogramas tengan dimensiones idénticas
    
    Args:
        phase_files: Lista de 3 rutas a archivos .img de fase
        
    Returns:
        tuple: (height, width, transform, crs) del primer raster
        
    Raises:
        ValueError: Si las dimensiones no coinciden
    """
    logger.info("\nValidando dimensiones de interferogramas...")
    
    dimensions = []
    metadata = []
    
    for i, phase_file in enumerate(phase_files, 1):
        with rasterio.open(phase_file) as src:
            dims = (src.height, src.width)
            dimensions.append(dims)
            metadata.append({
                'transform': src.transform,
                'crs': src.crs,
                'dtype': src.dtypes[0]
            })
            logger.info(f"  Ifg {i}: {dims[0]}×{dims[1]} pixels")
    
    # Verificar que todas las dimensiones coinciden
    if not all(d == dimensions[0] for d in dimensions):
        raise ValueError(
            f"ERROR: Dimensiones inconsistentes:\n"
            f"  Ifg 1: {dimensions[0]}\n"
            f"  Ifg 2: {dimensions[1]}\n"
            f"  Ifg 3: {dimensions[2]}\n"
            f"Los tres interferogramas deben tener dimensiones idénticas."
        )
    
    logger.info(f"  ✓ Todas las dimensiones coinciden: {dimensions[0][0]}×{dimensions[0][1]}")
    
    return dimensions[0][0], dimensions[0][1], metadata[0]['transform'], metadata[0]['crs']


def calculate_closure_phase_complex(phase_12, phase_23, phase_13):
    """
    Calcula Closure Phase usando formulación de exponenciales complejas.
    
    Esta formulación es robusta en zonas urbanas porque evita problemas
    de unwrapping al trabajar en el dominio complejo.
    
    Fórmula: Φ' = angle(e^(iφ₁₂) × e^(iφ₂₃) × e^(-iφ₁₃))
    
    Args:
        phase_12: Fase interferograma 1→2 (radianes, wrapped)
        phase_23: Fase interferograma 2→3 (radianes, wrapped)
        phase_13: Fase interferograma 1→3 (radianes, wrapped, par largo)
        
    Returns:
        tuple: (closure_phase, abs_closure_phase)
            - closure_phase: Φ' en radianes [-π, π]
            - abs_closure_phase: |Φ'| en radianes [0, π] (para PSLDA)
    """
    logger.info("\nCalculando Closure Phase con formulación compleja...")
    logger.info("  Fórmula: Φ' = angle(e^(iφ₁₂) × e^(iφ₂₃) × e^(-iφ₁₃))")
    
    # Convertir fases a exponenciales complejas
    exp_12 = np.exp(1j * phase_12)
    exp_23 = np.exp(1j * phase_23)
    exp_13_conj = np.exp(-1j * phase_13)  # Conjugado (signo negativo)
    
    # Producto complejo
    closure_complex = exp_12 * exp_23 * exp_13_conj
    
    # Extraer ángulo (closure phase) - automáticamente wrapped a [-π, π]
    closure_phase = np.angle(closure_complex)
    
    # Valor absoluto para PSLDA: |Φ'| en rango [0, π]
    # Según Yan et al.: "the absolute closure phase |Φ'| is often adopted 
    # since its magnitude directly reflects the variation degree of moisture contents"
    abs_closure_phase = np.abs(closure_phase)
    
    # Estadísticas
    valid_mask = np.isfinite(closure_phase)
    if np.any(valid_mask):
        mean_cp = np.mean(closure_phase[valid_mask])
        std_cp = np.std(closure_phase[valid_mask])
        mean_abs = np.mean(abs_closure_phase[valid_mask])
        max_abs = np.max(abs_closure_phase[valid_mask])
        
        logger.info(f"\n  Estadísticas Closure Phase (Φ'):")
        logger.info(f"    Media: {mean_cp:.4f} rad ({np.degrees(mean_cp):.2f}°)")
        logger.info(f"    Std:   {std_cp:.4f} rad ({np.degrees(std_cp):.2f}°)")
        
        logger.info(f"\n  Estadísticas |Φ'| (para PSLDA):")
        logger.info(f"    Media: {mean_abs:.4f} rad ({np.degrees(mean_abs):.2f}°)")
        logger.info(f"    Max:   {max_abs:.4f} rad ({np.degrees(max_abs):.2f}°)")
        
        # Interpretación según Yan et al.
        logger.info(f"\n  Interpretación:")
        if mean_abs < 0.3:
            logger.info(f"    ✓ |Φ'| bajo → Poca variación de humedad")
        elif mean_abs < 0.7:
            logger.info(f"    ⚠️  |Φ'| moderado → Variación de humedad moderada")
        else:
            logger.info(f"    ⚠️  |Φ'| alto → Alta variación de humedad (revisar posibles fugas)")
        
        # Calidad de datos
        if std_cp < 0.5:
            logger.info(f"    ✓ Calidad datos EXCELENTE (std < 0.5 rad)")
        elif std_cp < 1.0:
            logger.info(f"    ⚠️  Calidad datos ACEPTABLE (0.5 < std < 1.0 rad)")
        else:
            logger.info(f"    ✗ Calidad datos POBRE (std > 1.0 rad)")
    
    return closure_phase, abs_closure_phase


def save_closure_phase(closure_phase, abs_closure_phase, output_dir, 
                       transform, crs, triplet_dates):
    """
    Guarda Closure Phase como archivos GeoTIFF
    
    Args:
        closure_phase: Array con closure phase Φ' en radianes [-π, π]
        abs_closure_phase: Array con |Φ'| en radianes [0, π]
        output_dir: Directorio de salida
        transform: Transformación geográfica
        crs: Sistema de coordenadas
        triplet_dates: Tupla (date1, date2, date3) para el filename
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Generar nombre de archivo basado en fechas
    triplet_name = f"closure_{triplet_dates[0]}_{triplet_dates[1]}_{triplet_dates[2]}"
    
    # === Archivo 1: Closure Phase completo (2 bandas) ===
    combined_file = output_path / f"{triplet_name}.tif"
    logger.info(f"\nGuardando Closure Phase (2 bandas): {combined_file}")
    
    with rasterio.open(
        combined_file, 'w',
        driver='GTiff',
        height=closure_phase.shape[0],
        width=closure_phase.shape[1],
        count=2,
        dtype=rasterio.float32,
        crs=crs,
        transform=transform,
        compress='lzw',
        nodata=np.nan
    ) as dst:
        dst.write(closure_phase.astype(np.float32), 1)
        dst.write(abs_closure_phase.astype(np.float32), 2)
        dst.set_band_description(1, 'Closure Phase Phi (radians) [-pi, pi]')
        dst.set_band_description(2, 'Abs Closure Phase |Phi| (radians) [0, pi] - PSLDA input')
    
    logger.info(f"  ✓ Banda 1: Φ' (closure phase signed)")
    logger.info(f"  ✓ Banda 2: |Φ'| (closure phase absoluto - para PSLDA)")
    
    # === Archivo 2: Solo |Φ'| para PSLDA (1 banda) ===
    pslda_file = output_path / f"{triplet_name}_abs.tif"
    logger.info(f"\nGuardando |Φ'| para PSLDA: {pslda_file}")
    
    with rasterio.open(
        pslda_file, 'w',
        driver='GTiff',
        height=abs_closure_phase.shape[0],
        width=abs_closure_phase.shape[1],
        count=1,
        dtype=rasterio.float32,
        crs=crs,
        transform=transform,
        compress='lzw',
        nodata=np.nan
    ) as dst:
        dst.write(abs_closure_phase.astype(np.float32), 1)
        dst.set_band_description(1, 'Abs Closure Phase |Phi| for PSLDA')
    
    logger.info(f"  ✓ Guardado: {pslda_file}")
    
    return combined_file, pslda_file


def extract_dates_from_filename(filename):
    """
    Extrae fechas del nombre de archivo de interferograma.
    
    Args:
        filename: Nombre como 'Ifg_20220101_20220113.dim'
        
    Returns:
        tuple: (date1, date2) como strings 'YYYYMMDD'
    """
    import re
    pattern = r'Ifg_(\d{8})_(\d{8})'
    match = re.search(pattern, filename)
    if match:
        return match.groups()
    return None, None


def main():
    parser = argparse.ArgumentParser(
        description='Calcula Closure Phase para detección de fugas (PSLDA)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplo:
  python3 scripts/calculate_closure_phase.py \\
    processed/insar/Ifg_20220101_20220113.dim \\
    processed/insar/Ifg_20220113_20220125.dim \\
    processed/insar/Ifg_20220101_20220125_LONG.dim \\
    --output processed/closure_phase/

Interpretación para detección de fugas (Yan et al. 2024):
  |Φ'| ≈ 0  → No hay cambio de humedad (sin fuga)
  |Φ'| >> 0 → Cambio de humedad detectado (posible fuga)
  
El valor |Φ'| se usa como feature X₅ en el modelo PSLDA.
        """
    )
    
    parser.add_argument('ifg_12', help='Interferograma 1→2 (.dim) - par corto')
    parser.add_argument('ifg_23', help='Interferograma 2→3 (.dim) - par corto')
    parser.add_argument('ifg_13', help='Interferograma 1→3 (.dim) - par largo (_LONG)')
    parser.add_argument('--output', '-o', default='processed/closure_phase',
                       help='Directorio de salida (default: processed/closure_phase/)')
    
    args = parser.parse_args()

    # Configurar logger usando el directorio de salida
    global logger
    logger = LoggerConfig.setup_aoi_logger(
        aoi_project_dir=args.output,
        log_name='calculate_closure_phase',
        level=logging.INFO,
        console_level=logging.INFO
    )

    LoggerConfig.log_section(logger, "CÁLCULO DE CLOSURE PHASE PARA PSLDA")
    logger.info("Ref: Yan et al. (2024) - IEEE JSTARS")
    logger.info(f"\nTriplete de interferogramas:")
    logger.info(f"  φ₁₂ (corto): {args.ifg_12}")
    logger.info(f"  φ₂₃ (corto): {args.ifg_23}")
    logger.info(f"  φ₁₃ (largo): {args.ifg_13}")
    
    try:
        # 1. Buscar bandas de fase
        LoggerConfig.log_section(logger, "PASO 1: Localizar bandas de fase")
        
        phase_files = []
        for ifg in [args.ifg_12, args.ifg_23, args.ifg_13]:
            phase_file = find_phase_band(ifg)
            phase_files.append(phase_file)
        
        # 2. Validar dimensiones
        LoggerConfig.log_section(logger, "PASO 2: Validar dimensiones")
        
        height, width, transform, crs = validate_dimensions(phase_files)
        
        # 3. Leer fases
        LoggerConfig.log_section(logger, "PASO 3: Leer bandas de fase")
        
        phases = []
        for i, phase_file in enumerate(phase_files, 1):
            logger.info(f"  Leyendo Ifg {i}...")
            with rasterio.open(phase_file) as src:
                phase = src.read(1).astype(np.float64)
                phases.append(phase)
                
                valid_pixels = np.sum(np.isfinite(phase))
                total_pixels = phase.size
                logger.info(f"    Píxeles válidos: {valid_pixels}/{total_pixels} "
                          f"({100*valid_pixels/total_pixels:.1f}%)")
        
        # 4. Calcular Closure Phase
        LoggerConfig.log_section(logger, "PASO 4: Calcular Closure Phase")
        
        closure_phase, abs_closure_phase = calculate_closure_phase_complex(
            phases[0], phases[1], phases[2]
        )
        
        # 5. Extraer fechas para nombrar archivos
        date1, _ = extract_dates_from_filename(Path(args.ifg_12).name)
        _, date2 = extract_dates_from_filename(Path(args.ifg_12).name)
        _, date3 = extract_dates_from_filename(Path(args.ifg_23).name)
        
        if not all([date1, date2, date3]):
            # Fallback: usar nombres de archivo
            triplet_dates = (
                Path(args.ifg_12).stem,
                Path(args.ifg_23).stem,
                Path(args.ifg_13).stem
            )
        else:
            triplet_dates = (date1, date2, date3)
        
        # 6. Guardar resultados
        LoggerConfig.log_section(logger, "PASO 5: Guardar resultados")
        
        combined_file, pslda_file = save_closure_phase(
            closure_phase, abs_closure_phase,
            args.output, transform, crs, triplet_dates
        )
        
        # Resumen final
        LoggerConfig.log_section(logger, "RESUMEN")
        logger.info(f"✓ Closure Phase calculada exitosamente")
        logger.info(f"\nArchivos generados:")
        logger.info(f"  1. {combined_file}")
        logger.info(f"     └─ Banda 1: Φ' (signed)")
        logger.info(f"     └─ Banda 2: |Φ'| (absoluto)")
        logger.info(f"  2. {pslda_file}")
        logger.info(f"     └─ |Φ'| listo para PSLDA")
        logger.info(f"\nPara usar en PSLDA:")
        logger.info(f"  El archivo *_abs.tif contiene |Φ'| como feature X₅")
        logger.info(f"\nVisualización:")
        logger.info(f"  qgis {pslda_file}")
        logger.info("")
        
        return 0
        
    except Exception as e:
        logger.error(f"\n✗ ERROR: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
