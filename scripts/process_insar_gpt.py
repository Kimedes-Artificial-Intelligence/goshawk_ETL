#!/usr/bin/env python3
"""
Script: process_insar_gpt.py
Descripción: Procesa pares de imágenes SLC para InSAR usando GPT directamente
Uso: python scripts/process_insar_gpt.py [--use-preprocessed]

Workflow InSAR completo usando GPT/XML en lugar de snapista
"""

import os
import sys
import subprocess
import tempfile
import glob
from pathlib import Path
from typing import Optional, List, Tuple, Literal, Union

# Importar utilidades comunes
sys.path.insert(0, os.path.dirname(__file__))
from processing_utils import (
    load_config,
    extract_date_from_filename,
    logger
)
from burst_utils import select_representative_bursts
from insar_repository import InSARRepository


def create_insar_workflow_xml(
    master_path: Union[Path, str],
    slave_path: Union[Path, str],
    output_path: Union[Path, str],
    is_preprocessed: bool = False,
    aoi_wkt: Optional[str] = None,
    subswath: str = 'IW1'
) -> str:
    """
    Crea XML para workflow InSAR completo

    Para pre-procesados: Back-Geocoding → Interferogram → Deburst → TopoPhase → Multilook → Goldstein → Terrain → Subset → Write
    Para originales: ApplyOrbit(2) → TOPSAR-Split(2) → Back-Geocoding → Interferogram → Deburst → TopoPhase → ... → Subset → Write

    IMPORTANTE: Los productos preprocesados con --insar-mode YA tienen:
      1. TOPSAR-Split aplicado (sub-swath seleccionado)
      2. Apply-Orbit-File aplicado (órbitas corregidas)
      3. SIN Subset geográfico (geometría radar - se aplica DESPUÉS de Back-Geocoding)
      4. SIN Deburst (estructura de bursts INTACTA para Back-Geocoding)

    Args:
        aoi_wkt: WKT string del AOI para subset geográfico (ej: "POLYGON((lon lat, ...))")
        subswath: Sub-swath a procesar (default: 'IW1', fallback: 'IW2')
    """
    if is_preprocessed:
        # Workflow para productos pre-procesados (.dim) con --insar-mode
        # NOTA: Los productos preprocesados con --insar-mode YA tienen:
        #   1. TOPSAR-Split (sub-swath seleccionado)
        #   2. Apply-Orbit-File (órbitas precisas aplicadas en preprocesamiento)
        #   3. SIN Subset geográfico (geometría radar - se hace DESPUÉS de Back-Geocoding)
        #   4. MANTIENEN estructura de bursts (NO se aplicó TOPSAR-Deburst)
        # Por lo tanto:
        #   - NO necesitan Apply-Orbit-File (ya aplicado)
        #   - SÍ necesitan Back-Geocoding (para co-registrar par)
        #   - SÍ necesitan TOPSAR-Deburst (DESPUÉS de Interferogram)
        # Workflow: Read → Back-Geocoding → Interferogram → Deburst → TopoPhase → Multilook → Goldstein → Terrain → Subset → Write
        xml = f"""<graph id="InSAR_Preprocessed">
  <version>1.0</version>

  <node id="Read-Master">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{master_path}</file>
    </parameters>
  </node>

  <node id="Read-Slave">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{slave_path}</file>
    </parameters>
  </node>

  <!-- Apply-Orbit-File YA aplicado en preprocesamiento (preprocess_products.py) -->
  <!-- Por tanto conectamos directamente Read → Back-Geocoding -->

  <node id="Back-Geocoding">
    <operator>Back-Geocoding</operator>
    <sources>
      <sourceProduct refid="Read-Master"/>
      <sourceProduct.1 refid="Read-Slave"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <resamplingType>BILINEAR_INTERPOLATION</resamplingType>
      <maskOutAreaWithoutElevation>true</maskOutAreaWithoutElevation>
      <outputRangeAzimuthOffset>false</outputRangeAzimuthOffset>
      <outputDerampDemodPhase>false</outputDerampDemodPhase>
    </parameters>
  </node>

  <!-- Enhanced Spectral Diversity: CRÍTICO para TOPS Sentinel-1 -->
  <!-- Corrige errores de co-registro sub-pixel en fronteras de bursts -->
  <node id="Enhanced-Spectral-Diversity">
    <operator>Enhanced-Spectral-Diversity</operator>
    <sources>
      <sourceProduct refid="Back-Geocoding"/>
    </sources>
    <parameters>
      <fineWinWidthStr>512</fineWinWidthStr>
      <fineWinHeightStr>512</fineWinHeightStr>
      <fineWinAccAzimuth>16</fineWinAccAzimuth>
      <fineWinAccRange>16</fineWinAccRange>
      <fineWinOversampling>128</fineWinOversampling>
      <cohThreshold>0.3</cohThreshold>
      <numBlocksPerOverlap>10</numBlocksPerOverlap>
    </parameters>
  </node>

  <!-- Issue #6: Coherencia estable con ventana 3x10 (30 looks, ESA recomienda ≥30) -->
  
  <node id="Interferogram">
    <operator>Interferogram</operator>
    <sources>
      <sourceProduct refid="Enhanced-Spectral-Diversity"/>
    </sources>
    <parameters>
      <subtractFlatEarthPhase>true</subtractFlatEarthPhase>
      <srpPolynomialDegree>5</srpPolynomialDegree>
      <srpNumberPoints>501</srpNumberPoints>
      <orbitDegree>3</orbitDegree>
      <includeCoherence>true</includeCoherence>
      <!-- Issue #6: Ventana ajustada a 3x10 (30 looks) para coherencia estable -->
      <!-- Recomendación ESA: mínimo 10x3 para estimación robusta (~50m resolución) -->
      <cohWinAz>3</cohWinAz>
      <cohWinRg>10</cohWinRg>
      <squarePixel>false</squarePixel>
    </parameters>
  </node>

  <node id="TOPSAR-Deburst">
    <operator>TOPSAR-Deburst</operator>
    <sources>
      <sourceProduct refid="Interferogram"/>
    </sources>
    <parameters>
      <selectedPolarisations>VV,VH</selectedPolarisations>
    </parameters>
  </node>

  <node id="TopoPhaseRemoval">
    <operator>TopoPhaseRemoval</operator>
    <sources>
      <sourceProduct refid="TOPSAR-Deburst"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <tileExtensionPercent>100</tileExtensionPercent>
      <outputTopoPhaseBand>false</outputTopoPhaseBand>
      <outputElevationBand>false</outputElevationBand>
      <outputLatLonBands>false</outputLatLonBands>
    </parameters>
  </node>

  <!-- MODIFICADO: Multilook reducido a 2x1 (mínimo para preservar resolución) -->
  <!-- Para detección de humedad necesitamos máxima resolución espacial -->
  <node id="Multilook">
    <operator>Multilook</operator>
    <sources>
      <sourceProduct refid="TopoPhaseRemoval"/>
    </sources>
    <parameters>
      <nRgLooks>2</nRgLooks>
      <nAzLooks>1</nAzLooks>
      <outputIntensity>false</outputIntensity>
      <grSquarePixel>false</grSquarePixel>
    </parameters>
  </node>

  <!-- ELIMINADO: GoldsteinPhaseFiltering -->
  <!-- El filtro Goldstein suaviza la fase interpolando datos vecinos -->
  <!-- Una fuga de agua destruye la fase (baja coherencia); el filtro -->
  <!-- intentaría "reparar" ese daño, ocultando la anomalía de humedad -->

  <!-- Terrain-Correction geocodifica intensidad y coherencia -->
  <node id="Terrain-Correction">
    <operator>Terrain-Correction</operator>
    <sources>
      <sourceProduct refid="Multilook"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <imgResamplingMethod>BILINEAR_INTERPOLATION</imgResamplingMethod>
      <pixelSpacingInMeter>10.0</pixelSpacingInMeter>
      <mapProjection>WGS84(DD)</mapProjection>
      <alignToStandardGrid>false</alignToStandardGrid>
      <nodataValueAtSea>true</nodataValueAtSea>
      <saveDEM>false</saveDEM>
      <saveLatLon>false</saveLatLon>
      <saveSelectedSourceBand>true</saveSelectedSourceBand>
      <applyRadiometricNormalization>true</applyRadiometricNormalization>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="Terrain-Correction"/>
    </sources>
    <parameters>
      <file>{output_path}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>

</graph>"""
    else:
        # Workflow completo para productos SLC originales (.SAFE)
        xml = f"""<graph id="InSAR_Complete">
  <version>1.0</version>

  <node id="Read-Master">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{master_path}</file>
    </parameters>
  </node>

  <node id="Read-Slave">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{slave_path}</file>
    </parameters>
  </node>

  <node id="Apply-Orbit-File-Master">
    <operator>Apply-Orbit-File</operator>
    <sources>
      <sourceProduct refid="Read-Master"/>
    </sources>
    <parameters>
      <orbitType>Sentinel Precise (Auto Download)</orbitType>
      <polyDegree>3</polyDegree>
      <continueOnFail>false</continueOnFail>
    </parameters>
  </node>

  <node id="Apply-Orbit-File-Slave">
    <operator>Apply-Orbit-File</operator>
    <sources>
      <sourceProduct refid="Read-Slave"/>
    </sources>
    <parameters>
      <orbitType>Sentinel Precise (Auto Download)</orbitType>
      <polyDegree>3</polyDegree>
      <continueOnFail>false</continueOnFail>
    </parameters>
  </node>

  <node id="TOPSAR-Split-Master">
    <operator>TOPSAR-Split</operator>
    <sources>
      <sourceProduct refid="Apply-Orbit-File-Master"/>
    </sources>
    <parameters>
      <subswath>{subswath}</subswath>
      <selectedPolarisations>VV,VH</selectedPolarisations>
    </parameters>
  </node>

  <node id="TOPSAR-Split-Slave">
    <operator>TOPSAR-Split</operator>
    <sources>
      <sourceProduct refid="Apply-Orbit-File-Slave"/>
    </sources>
    <parameters>
      <subswath>{subswath}</subswath>
      <selectedPolarisations>VV,VH</selectedPolarisations>
    </parameters>
  </node>

  <node id="Back-Geocoding">
    <operator>Back-Geocoding</operator>
    <sources>
      <sourceProduct refid="TOPSAR-Split-Master"/>
      <sourceProduct.1 refid="TOPSAR-Split-Slave"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <resamplingType>BILINEAR_INTERPOLATION</resamplingType>
      <maskOutAreaWithoutElevation>true</maskOutAreaWithoutElevation>
      <outputRangeAzimuthOffset>false</outputRangeAzimuthOffset>
      <outputDerampDemodPhase>false</outputDerampDemodPhase>
    </parameters>
  </node>

  <!-- Enhanced Spectral Diversity: CRÍTICO para TOPS Sentinel-1 -->
  <!-- Corrige errores de co-registro sub-pixel en fronteras de bursts -->
  <node id="Enhanced-Spectral-Diversity">
    <operator>Enhanced-Spectral-Diversity</operator>
    <sources>
      <sourceProduct refid="Back-Geocoding"/>
    </sources>
    <parameters>
      <fineWinWidthStr>512</fineWinWidthStr>
      <fineWinHeightStr>512</fineWinHeightStr>
      <fineWinAccAzimuth>16</fineWinAccAzimuth>
      <fineWinAccRange>16</fineWinAccRange>
      <fineWinOversampling>128</fineWinOversampling>
      <cohThreshold>0.3</cohThreshold>
      <numBlocksPerOverlap>10</numBlocksPerOverlap>
    </parameters>
  </node>

  <!-- Issue #6: Coherencia estable con ventana 3x10 (30 looks, ESA recomienda ≥30) -->
  
  <node id="Interferogram">
    <operator>Interferogram</operator>
    <sources>
      <sourceProduct refid="Enhanced-Spectral-Diversity"/>
    </sources>
    <parameters>
      <subtractFlatEarthPhase>true</subtractFlatEarthPhase>
      <srpPolynomialDegree>5</srpPolynomialDegree>
      <srpNumberPoints>501</srpNumberPoints>
      <orbitDegree>3</orbitDegree>
      <includeCoherence>true</includeCoherence>
      <!-- Issue #6: Ventana ajustada a 3x10 (30 looks) para coherencia estable -->
      <!-- Recomendación ESA: mínimo 10x3 para estimación robusta (~50m resolución) -->
      <cohWinAz>3</cohWinAz>
      <cohWinRg>10</cohWinRg>
      <squarePixel>false</squarePixel>
    </parameters>
  </node>

  <node id="TOPSAR-Deburst">
    <operator>TOPSAR-Deburst</operator>
    <sources>
      <sourceProduct refid="Interferogram"/>
    </sources>
    <parameters>
      <selectedPolarisations>VV,VH</selectedPolarisations>
    </parameters>
  </node>

  <node id="TopoPhaseRemoval">
    <operator>TopoPhaseRemoval</operator>
    <sources>
      <sourceProduct refid="TOPSAR-Deburst"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <tileExtensionPercent>100</tileExtensionPercent>
      <outputTopoPhaseBand>false</outputTopoPhaseBand>
      <outputElevationBand>false</outputElevationBand>
      <outputLatLonBands>false</outputLatLonBands>
    </parameters>
  </node>

  <!-- MODIFICADO: Multilook reducido a 2x1 (mínimo para preservar resolución) -->
  <!-- Para detección de humedad necesitamos máxima resolución espacial -->
  <node id="Multilook">
    <operator>Multilook</operator>
    <sources>
      <sourceProduct refid="TopoPhaseRemoval"/>
    </sources>
    <parameters>
      <nRgLooks>2</nRgLooks>
      <nAzLooks>1</nAzLooks>
      <outputIntensity>false</outputIntensity>
      <grSquarePixel>false</grSquarePixel>
    </parameters>
  </node>

  <!-- ELIMINADO: GoldsteinPhaseFiltering -->
  <!-- El filtro Goldstein suaviza la fase interpolando datos vecinos -->
  <!-- Una fuga de agua destruye la fase (baja coherencia); el filtro -->
  <!-- intentaría "reparar" ese daño, ocultando la anomalía de humedad -->

  <!-- Terrain-Correction geocodifica intensidad y coherencia -->
  <node id="Terrain-Correction">
    <operator>Terrain-Correction</operator>
    <sources>
      <sourceProduct refid="Multilook"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <imgResamplingMethod>BILINEAR_INTERPOLATION</imgResamplingMethod>
      <pixelSpacingInMeter>10.0</pixelSpacingInMeter>
      <mapProjection>WGS84(DD)</mapProjection>
      <alignToStandardGrid>false</alignToStandardGrid>
      <nodataValueAtSea>true</nodataValueAtSea>
      <saveDEM>false</saveDEM>
      <saveLatLon>false</saveLatLon>
      <saveSelectedSourceBand>true</saveSelectedSourceBand>
      <applyRadiometricNormalization>true</applyRadiometricNormalization>
    </parameters>
  </node>

  <node id="Subset">
    <operator>Subset</operator>
    <sources>
      <sourceProduct refid="Terrain-Correction"/>
    </sources>
    <parameters>
      <geoRegion>{aoi_wkt if aoi_wkt else ''}</geoRegion>
      <copyMetadata>true</copyMetadata>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="{'Subset' if aoi_wkt else 'Terrain-Correction'}"/>
    </sources>
    <parameters>
      <file>{output_path}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>

</graph>"""

    return xml


def validate_insar_output(output_path: Union[Path, str]) -> bool:
    """
    Valida que el producto InSAR se generó correctamente verificando:
    1. Existencia de archivos .img en .data
    2. Presencia de bandas críticas: Phase_ifg_* y coh_*

    Args:
        output_path: Ruta al archivo .dim de salida

    Returns:
        True si el producto es válido, False si no
    """
    if not os.path.exists(output_path):
        logger.warning(f"  ⚠️  Archivo .dim no existe: {output_path}")
        return False

    # Verificar directorio .data
    data_dir = output_path.replace('.dim', '.data')
    if not os.path.isdir(data_dir):
        logger.warning(f"  ⚠️  Directorio .data no existe: {data_dir}")
        return False

    # Buscar archivos .img (datos raster)
    img_files = glob.glob(os.path.join(data_dir, '*.img'))

    if len(img_files) == 0:
        logger.error(f"  ✗ Procesamiento incompleto: NO hay archivos .img en {data_dir}")
        logger.error(f"  GPT generó solo metadatos pero no datos raster")
        return False
    
    # Verificar bandas críticas
    phase_bands = [f for f in img_files if 'Phase_ifg' in f or 'phase' in f.lower()]
    coh_bands = [f for f in img_files if 'coh' in f.lower()]
    
    has_phase = len(phase_bands) > 0
    has_coherence = len(coh_bands) > 0
    
    # Verificar tamaños
    total_size = sum(os.path.getsize(f) for f in img_files)
    if total_size == 0:
        logger.error(f"  ✗ Archivos .img existen pero están vacíos")
        return False

    logger.info(f"  ✓ Validación: {len(img_files)} bandas, {total_size / (1024**3):.2f} GB")
    
    # Issue #2: Verificar bandas esenciales
    if has_phase:
        logger.info(f"    ✓ Banda de fase presente: {len(phase_bands)} archivo(s)")
    else:
        logger.warning(f"    ⚠️  Banda de fase NO encontrada (crítico para Closure Phase)")
    
    if has_coherence:
        logger.info(f"    ✓ Banda de coherencia presente: {len(coh_bands)} archivo(s)")
    else:
        logger.warning(f"    ⚠️  Banda de coherencia NO encontrada")
    
    # Producto válido si tiene archivos, pero advertir si faltan bandas críticas
    if not has_phase or not has_coherence:
        logger.warning(f"    ⚠️  Producto incompleto - revisar configuración saveSelectedSourceBand")
    
    return True


def process_pair_with_gpt(
    master_path: Union[Path, str],
    slave_path: Union[Path, str],
    output_path: Union[Path, str],
    is_preprocessed: bool = False,
    aoi_wkt: Optional[str] = None,
    configured_subswath: str = 'IW2'
) -> bool:
    """
    Procesa un par InSAR usando GPT con el sub-swath configurado

    IMPORTANTE: Para InSAR, el master y slave deben tener el MISMO subswath.
    Esta función NO realiza fallback automático a otros subswaths, ya que
    esto es técnicamente incorrecto según los requisitos de SNAP Back-Geocoding.

    Args:
        master_path: Ruta al producto master
        slave_path: Ruta al producto slave
        output_path: Ruta de salida del producto InSAR
        is_preprocessed: Si los productos ya están preprocesados
        aoi_wkt: AOI en formato WKT (opcional)
        configured_subswath: Sub-swath a usar (IW1/IW2/IW3), default IW2

    Returns:
        True si el procesamiento fue exitoso, False en caso contrario
    """
    # Validate that master and slave are different products
    master_path_str = str(master_path)
    slave_path_str = str(slave_path)

    if master_path_str == slave_path_str:
        logger.error(f"  ✗ Error: Master and slave are the same product: {Path(master_path).name}")
        return False

    # Extract dates to validate they're different (prevent same-date interferograms)
    master_date = extract_date_from_filename(Path(master_path).name)
    slave_date = extract_date_from_filename(Path(slave_path).name)

    if master_date == slave_date:
        logger.error(f"  ✗ Error: Master and slave have the same date: {master_date}")
        logger.error(f"    Master: {Path(master_path).name}")
        logger.error(f"    Slave: {Path(slave_path).name}")
        return False

    subswath = configured_subswath

    try:
        logger.info(f"  → Procesando con sub-swath: {subswath}")

        # Crear XML
        xml = create_insar_workflow_xml(master_path, slave_path, output_path, is_preprocessed, aoi_wkt, subswath)

        # Guardar XML temporal
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as tf:
            tf.write(xml)
            xml_file = tf.name

        try:
            # Ejecutar GPT
            logger.info("  ⚙️  Ejecutando GPT...")
            logger.info(f"  → Workflow: {'Pre-procesado' if is_preprocessed else 'Completo'}")

            result = subprocess.run(
                ['gpt', xml_file, '-c', '8G'],
                capture_output=True,
                text=True,
                timeout=7200  # 120 min timeout (InSAR es más lento)
            )

            # Validar el resultado
            if result.returncode != 0:
                logger.error(f"  ✗ Error en GPT con {subswath} (exit code {result.returncode})")
                if result.stderr:
                    # Guardar error completo en archivo para depuración
                    error_file = f"/tmp/gpt_error_{subswath}_{os.getpid()}.log"
                    with open(error_file, 'w') as f:
                        f.write(result.stderr)
                    logger.error(f"  Error completo guardado en: {error_file}")

                    # Buscar la causa raíz del error
                    stderr_lines = result.stderr.strip().split('\n')

                    # Buscar líneas "Caused by" o "Error:" que contienen el error real
                    error_lines = [line for line in stderr_lines if 'Caused by:' in line or 'Error:' in line or 'Exception' in line]

                    if error_lines:
                        logger.error("Errores encontrados:")
                        for line in error_lines[:10]:  # Primeros 10 errores
                            logger.error(f"  {line.strip()}")

                return False

            # GPT retornó exit code 0, pero verificar que realmente generó datos
            logger.info("  → Validando salida del procesamiento...")
            if not validate_insar_output(output_path):
                logger.error(f"  ✗ GPT con {subswath} completó pero no generó datos válidos")
                logger.error("  Esto puede deberse a:")
                logger.error("    - Memoria insuficiente durante el procesamiento")
                logger.error("    - Corrupción de datos de entrada")
                logger.error("    - Bug en SNAP/GPT")
                return False

            logger.info(f"  ✅ Procesamiento exitoso con {subswath}")
            return True

        finally:
            # Limpiar XML temporal
            if os.path.exists(xml_file):
                os.unlink(xml_file)

    except Exception as e:
        logger.error(f"  ✗ Error con {subswath}: {e}")
        return False


def generate_insar_pairs(
    slc_products: List[str],
    include_long_pairs: bool = True
) -> List[Tuple[str, str, Literal['short', 'long']]]:
    """
    Genera lista de pares InSAR incluyendo pares largos para Closure Phase.

    Para N imágenes:
    - Pares cortos (salto +1): N-1 pares (A→B, B→C, C→D, ...)
    - Pares largos (salto +2): N-2 pares (A→C, B→D, C→E, ...)

    Los pares largos son necesarios para cerrar el bucle interferométrico
    y calcular la Closure Phase, crucial para validar la calidad InSAR.

    IMPORTANTE: Valida que los pares sean temporalmente consecutivos.
    Si hay un salto temporal grande (>12 días para short, >24 días para long),
    el par se descarta automáticamente con una advertencia.

    Límites basados en ciclo de repetición de Sentinel-1:
    - 1 satélite (2023-2024): 12 días
    - 2 satélites (2014-2022): 6 días
    - Pares cortos: máximo 12 días (1 ciclo)
    - Pares largos: máximo 24 días (2 ciclos)

    Args:
        slc_products: Lista ordenada de productos SLC
        include_long_pairs: Si True, incluye pares con salto +2

    Returns:
        Lista de tuplas: [(master, slave, pair_type), ...]
        pair_type: 'short' (salto +1) o 'long' (salto +2)
    """
    from datetime import datetime

    pairs = []

    # Pares consecutivos (salto +1) - Short pairs
    for i in range(len(slc_products) - 1):
        master = slc_products[i]
        slave = slc_products[i + 1]

        # Validar que el salto temporal sea razonable (<= 60 días)
        master_date_str = extract_date_from_filename(os.path.basename(master))
        slave_date_str = extract_date_from_filename(os.path.basename(slave))

        if master_date_str and slave_date_str:
            # Parsear fechas (formato: YYYYMMDDTHHMMSS)
            master_date = datetime.strptime(master_date_str[:8], '%Y%m%d')
            slave_date = datetime.strptime(slave_date_str[:8], '%Y%m%d')

            # Calcular diferencia en días
            days_diff = abs((slave_date - master_date).days)

            # Para pares cortos, aceptar máximo 12 días (1 ciclo de Sentinel-1)
            # Sentinel-1A/B tienen ciclo de repetición de 12 días por satélite
            # Con 2 satélites (2014-2022): revisita cada 6 días
            # Con 1 satélite (2023-2024): revisita cada 12 días
            if days_diff > 12:
                logger.warning(f"⚠️  PAR CORTO RECHAZADO (salto temporal {days_diff} días > 12):")
                logger.warning(f"    Master: {master_date_str[:8]} - {os.path.basename(master)[:60]}")
                logger.warning(f"    Slave:  {slave_date_str[:8]} - {os.path.basename(slave)[:60]}")
                logger.warning(f"    Probablemente faltan productos SLC intermedios")
                continue

        pairs.append((master, slave, 'short'))

    # Pares largos (salto +2) - Long pairs para Closure Phase
    if include_long_pairs and len(slc_products) >= 3:
        for i in range(len(slc_products) - 2):
            master = slc_products[i]
            slave = slc_products[i + 2]

            # Validar que el salto temporal sea razonable (<= 120 días para pares largos)
            master_date_str = extract_date_from_filename(os.path.basename(master))
            slave_date_str = extract_date_from_filename(os.path.basename(slave))

            if master_date_str and slave_date_str:
                master_date = datetime.strptime(master_date_str[:8], '%Y%m%d')
                slave_date = datetime.strptime(slave_date_str[:8], '%Y%m%d')
                days_diff = abs((slave_date - master_date).days)

                # Para pares largos (salto +2), aceptar máximo 24 días (2 ciclos)
                # Con 1 satélite: 12 + 12 = 24 días
                # Con 2 satélites: 6 + 6 = 12 días (pero permitimos hasta 24 por seguridad)
                if days_diff > 24:
                    logger.warning(f"⚠️  PAR LARGO RECHAZADO (salto temporal {days_diff} días > 24):")
                    logger.warning(f"    Master: {master_date_str[:8]} - {os.path.basename(master)[:60]}")
                    logger.warning(f"    Slave:  {slave_date_str[:8]} - {os.path.basename(slave)[:60]}")
                    logger.warning(f"    Probablemente faltan productos SLC intermedios")
                    continue

            pairs.append((master, slave, 'long'))

    return pairs


def create_pol_decomposition_xml(
        input_path: Union[Path, str],
        output_path: Union[Path, str],
        is_preprocessed: bool = False
) -> str:
    """
    Crea XML para descomposición H/A/Alpha Dual-Pol en Sentinel-1.

    Workflow para productos preprocesados (.dim):
      Read -> Calibration -> Pol-Speckle-Filter -> Pol-Decomposition -> Terrain-Correction -> Write

    Workflow para productos originales (.SAFE):
      Read -> Apply-Orbit-File -> Calibration -> Pol-Speckle-Filter -> Pol-Decomposition -> Terrain-Correction -> Write

    Args:
        input_path: Ruta al producto SLC
        output_path: Ruta de salida
        is_preprocessed: True si el producto ya tiene Apply-Orbit-File (productos .dim preprocesados)
    """

    # Determinar nodo fuente para Calibration
    calibration_source = "Read" if is_preprocessed else "Apply-Orbit-File"

    # Nodo opcional Apply-Orbit-File (solo para productos originales)
    apply_orbit_node = "" if is_preprocessed else """
  <!-- Apply-Orbit-File MUST be applied for correct geolocation of original products -->
  <node id="Apply-Orbit-File">
    <operator>Apply-Orbit-File</operator>
    <sources>
      <sourceProduct refid="Read"/>
    </sources>
    <parameters>
      <orbitType>Sentinel Precise (Auto Download)</orbitType>
      <polyDegree>3</polyDegree>
      <continueOnFail>false</continueOnFail>
    </parameters>
  </node>
"""

    xml = f"""<graph id="S1_Polarimetric_Decomposition">
  <version>1.0</version>

  <node id="Read">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{input_path}</file>
    </parameters>
  </node>
{apply_orbit_node}
  <!-- Calibration MUST be applied before TOPSAR-Deburst for Sentinel-1 TOPS data -->
  <node id="Calibration">
    <operator>Calibration</operator>
    <sources>
      <sourceProduct refid="{calibration_source}"/>
    </sources>
    <parameters>
      <sourceBands/>
      <auxFile>Latest Auxiliary File</auxFile>
      <externalAuxFile/>
      <outputImageInComplex>true</outputImageInComplex> <outputImageScaleInDb>false</outputImageScaleInDb>
      <createGammaBand>false</createGammaBand>
      <createBetaBand>false</createBetaBand>
      <selectedPolarisations>VV,VH</selectedPolarisations> <outputSigmaBand>false</outputSigmaBand>
      <outputGammaBand>false</outputGammaBand>
      <outputBetaBand>false</outputBetaBand>
    </parameters>
  </node>

  <!-- TOPSAR-Deburst requerido para productos preprocesados con bursts -->
  <node id="TOPSAR-Deburst">
    <operator>TOPSAR-Deburst</operator>
    <sources>
      <sourceProduct refid="Calibration"/>
    </sources>
    <parameters>
      <selectedPolarisations>VV,VH</selectedPolarisations>
    </parameters>
  </node>

  <node id="Polarimetric-Speckle-Filter">
    <operator>Polarimetric-Speckle-Filter</operator>
    <sources>
      <sourceProduct refid="TOPSAR-Deburst"/>
    </sources>
    <parameters>
      <filter>Refined Lee Filter</filter>
      <filterSize>5</filterSize>
      <searchWindowSizeStr>9</searchWindowSizeStr>
      <numLooksStr>1</numLooksStr>
      <windowSize>7x7</windowSize>
      <targetWindowSizeStr>3x3</targetWindowSizeStr>
    </parameters>
  </node>

  <node id="Polarimetric-Decomposition">
    <operator>Polarimetric-Decomposition</operator>
    <sources>
      <sourceProduct refid="Polarimetric-Speckle-Filter"/>
    </sources>
    <parameters>
      <decomposition>H-Alpha Dual Pol Decomposition</decomposition>
      <windowSize>5</windowSize>
      <outputHAAlpha>true</outputHAAlpha>
    </parameters>
  </node>

  <node id="Terrain-Correction">
    <operator>Terrain-Correction</operator>
    <sources>
      <sourceProduct refid="Polarimetric-Decomposition"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <imgResamplingMethod>BILINEAR_INTERPOLATION</imgResamplingMethod>
      <pixelSpacingInMeter>10.0</pixelSpacingInMeter>
      <mapProjection>WGS84(DD)</mapProjection>
      <alignToStandardGrid>false</alignToStandardGrid>
      <nodataValueAtSea>true</nodataValueAtSea>
      <saveDEM>false</saveDEM>
      <saveLatLon>false</saveLatLon>
      <saveSelectedSourceBand>true</saveSelectedSourceBand>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="Terrain-Correction"/>
    </sources>
    <parameters>
      <file>{output_path}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>

</graph>"""
    return xml


def extract_track_number_robust(slc_source, repository):
    """
    Extrae track number de forma robusta soportando:
    - Productos .SAFE originales
    - Productos preprocessados .dim (formato: S1A_IW_SLC__..._split.dim)
    - Productos preprocessados con prefijo incorrecto (formato: SLC_YYYYMMDD_S1A_..._split.dim)
    - Symlinks a .SAFE en workspace
    
    Args:
        slc_source: Puede ser archivo .SAFE, .dim, o directorio (string o Path)
        repository: InSARRepository instance
        
    Returns:
        int: Track number (1-175) o None si falla
    """
    import re
    from pathlib import Path
    
    slc_path = Path(slc_source) if not isinstance(slc_source, Path) else slc_source
    
    # 1. Si es .SAFE, usar método existente del repositorio
    if str(slc_path).endswith('.SAFE'):
        return repository.extract_track_from_slc(str(slc_path))
    
    # 2. Si es directorio con .SAFE symlinks, buscar primero
    if slc_path.is_dir():
        safe_products = list(slc_path.glob("*.SAFE"))
        if safe_products:
            return repository.extract_track_from_slc(str(safe_products[0]))
        
        # Buscar .dim files también
        dim_files = list(slc_path.glob("*.dim"))
        if dim_files:
            slc_path = dim_files[0]
    
    # 3. Si es .dim preprocessado, parsear nombre
    if str(slc_path).endswith('.dim'):
        basename = slc_path.name
        
        # Patrón para productos Sentinel-1 (con o sin prefijo SLC_YYYYMMDD_)
        # Formato correcto: S1A_IW_SLC__1SDV_YYYYMMDDTHHMMSS_YYYYMMDDTHHMMSS_AAAAAA_BBBBBB_CCCC_split.dim
        # Formato incorrecto: SLC_YYYYMMDD_S1A_IW_SLC__1SDV_..._AAAAAA_..._split.dim
        #                                                        ^^^^^^
        #                                                   Absolute orbit
        pattern = r'S1([ABC])_IW_SLC__1S\w+_\d{8}T\d{6}_\d{8}T\d{6}_(\d{6})_'
        match = re.search(pattern, basename)
        
        if match:
            satellite = match.group(1)  # A, B, o C
            absolute_orbit = int(match.group(2))
            
            # Calcular track (órbita relativa)
            if satellite in ['A', 'C']:
                track = (absolute_orbit - 73) % 175 + 1
            elif satellite == 'B':
                track = (absolute_orbit - 27) % 175 + 1
            else:
                return None
            
            logger.debug(f"Track extraído de {basename}: {track} (órbita absoluta: {absolute_orbit}, sat: S1{satellite})")
            return track
    
    # 4. Buscar symlinks en directorio padre/slc/ (workspace structure)
    if slc_path.is_file():
        # Estructura típica: workspace/preprocessed_slc/*.dim
        #                    workspace/slc/*.SAFE (symlinks)
        workspace_slc = slc_path.parent.parent / "slc"
        if workspace_slc.exists():
            safe_products = list(workspace_slc.glob("*.SAFE"))
            if safe_products:
                return repository.extract_track_from_slc(str(safe_products[0]))
    
    # 5. Leer metadata del .dim como último recurso
    if str(slc_path).endswith('.dim') and slc_path.exists():
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(slc_path)
            
            # Buscar REL_ORBIT o relativeOrbitNumber en metadata
            for elem in tree.iter():
                if 'REL_ORBIT' in elem.tag or 'relativeOrbit' in elem.tag:
                    track = int(elem.text)
                    logger.debug(f"Track extraído de metadata XML: {track}")
                    return track
        except Exception as e:
            logger.debug(f"No se pudo leer metadata XML: {e}")
    
    logger.warning(f"No se pudo extraer track number de: {slc_source}")
    return None


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description='Procesamiento InSAR usando GPT')
    parser.add_argument('--use-preprocessed', action='store_true',
                        help='Usar productos pre-procesados')
    parser.add_argument('--use-repository', action='store_true',
                        help='Buscar productos en repositorio compartido antes de procesar')
    parser.add_argument('--save-to-repository', action='store_true',
                        help='Guardar productos procesados en repositorio compartido')
    parser.add_argument('--start-date', type=str,
                        help='Fecha inicial (YYYY-MM-DD) - opcional, para compatibilidad')
    parser.add_argument('--end-date', type=str,
                        help='Fecha final (YYYY-MM-DD) - opcional, para compatibilidad')
    parser.add_argument('--no-long-pairs', action='store_true',
                        help='Desactivar generación de pares largos (solo pares consecutivos)')
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("PROCESAMIENTO InSAR CON GPT")
    logger.info("=" * 80)

    # Cargar configuración
    config = load_config()

    if args.use_preprocessed:
        slc_dir = config.get('PREPROCESSED_SLC_DIR', 'data/preprocessed_slc')
        logger.info("MODO: Productos PRE-PROCESADOS (especificado por usuario)")
    else:
        slc_dir = config.get('SLC_DIR', 'data/sentinel1_slc')
        logger.info("MODO: Auto-detectar tipo de producto")

    output_dir = config.get('OUTPUT_DIR', 'processed')
    aoi_wkt = config.get('AOI', None)

    # Leer configuración de órbita y sub-swath (necesario siempre, no solo para repositorio)
    orbit_direction = config.get('ORBIT_DIRECTION', 'DESCENDING')
    subswath = config.get('SUBSWATH', 'IW2')  # Default IW2 en lugar de IW1

    # Configurar repositorio si está habilitado
    repository = None
    track_number = None

    if args.use_repository or args.save_to_repository:
        repository = InSARRepository()
        logger.info(f"\n📦 Repositorio habilitado: {orbit_direction} {subswath}")


    if aoi_wkt:
        logger.info(f"AOI configurado: {aoi_wkt[:60]}...")
    else:
        logger.info("Procesando escena completa (sin subset)")

    # Seleccionar bursts representativos (usar .SAFE originales)
    # NO usar SliceAssembly/MERGED para InSAR; Back-Geocoding requiere productos split
    logger.info(f"\n📂 Directorio SLC: {slc_dir}")
    slc_products = select_representative_bursts(slc_dir)

    if len(slc_products) < 2:
        logger.error(f"Se necesitan al menos 2 productos SLC. Encontrados: {len(slc_products)}")
        return 1

    # Generar pares InSAR (cortos + largos para Closure Phase)
    include_long = not args.no_long_pairs
    insar_pairs = generate_insar_pairs(slc_products, include_long_pairs=include_long)
    
    short_pairs = sum(1 for _, _, ptype in insar_pairs if ptype == 'short')
    long_pairs = sum(1 for _, _, ptype in insar_pairs if ptype == 'long')
    
    logger.info(f"\nEncontrados {len(slc_products)} productos SLC")
    logger.info(f"Pares cortos a procesar (salto +1): {short_pairs}")
    if include_long:
        logger.info(f"Pares largos a procesar (salto +2): {long_pairs}")
        logger.info(f"Total pares: {len(insar_pairs)} (para Closure Phase)")
    else:
        logger.info(f"Total pares: {len(insar_pairs)} (solo cortos)")
    logger.info("")

    # Crear directorios de salida (short y long)
    os.makedirs(os.path.join(output_dir, 'short'), exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'long'), exist_ok=True)

    # Extraer track number del primer SLC si usamos repositorio
    if repository and track_number is None and len(slc_products) > 0:
        track_number = extract_track_number_robust(slc_products[0], repository)
        if track_number:
            logger.info(f"📡 Track detectado: {track_number}")
            logger.info(f"   Repositorio: {repository.repo_base_dir}/{orbit_direction.lower()[:4]}_{subswath.lower()}/t{track_number:03d}/")
        else:
            logger.warning("⚠️  No se pudo detectar track number - repositorio deshabilitado")
            logger.warning(f"   Producto analizado: {slc_products[0]}")
            repository = None

    # Procesar pares (cortos + largos)
    processed = 0
    failed = 0
    skipped_from_repo = 0
    total_pairs = len(insar_pairs)

    for idx, (master, slave, pair_type) in enumerate(insar_pairs, 1):
        master_date = extract_date_from_filename(os.path.basename(master))
        slave_date = extract_date_from_filename(os.path.basename(slave))

        if not master_date or not slave_date:
            logger.error(f"[{idx}/{total_pairs}] No se pudieron extraer fechas")
            failed += 1
            continue

        # NOTA: Ya no necesitamos saltar pares del mismo día
        # porque auto_merge_bursts() ya los fusionó automáticamente

        pair_name = f"{master_date[:8]}_{slave_date[:8]}"

        # Guardar en subdirectorio según tipo de par
        if pair_type == 'long':
            output_file = os.path.join(output_dir, 'long', f'Ifg_{pair_name}_LONG.dim')
        else:
            output_file = os.path.join(output_dir, 'short', f'Ifg_{pair_name}.dim')

        if os.path.exists(output_file):
            logger.info(f"[{idx}/{total_pairs}] ✓ Ya procesado: {pair_name} ({pair_type})")
            processed += 1
            continue

        # ISSUE #4: Check database for existing InSAR pair
        # Extract scene_id from file paths
        master_scene_id = os.path.basename(master).replace('.SAFE', '').replace('.dim', '').split('_Orb')[0].split('_Stack')[0]
        slave_scene_id = os.path.basename(slave).replace('.SAFE', '').replace('.dim', '').split('_Orb')[0].split('_Stack')[0]

        try:
            from scripts.db_queries import insar_pair_exists
            from scripts.db_integration import init_db

            if not hasattr(insar_pair_exists, '_db_checked'):
                # Initialize DB only once
                insar_pair_exists._db_available = init_db()
                insar_pair_exists._db_checked = True

            if insar_pair_exists._db_available:
                if insar_pair_exists(master_scene_id, slave_scene_id, subswath, pair_type):
                    logger.info(f"[{idx}/{total_pairs}] 💾 Pair exists in database: {pair_name} ({pair_type}) - skipping")
                    skipped_from_repo += 1
                    processed += 1
                    continue
        except ImportError:
            pass  # DB not available, continue with normal processing

        # VERIFICAR REPOSITORIO ANTES DE PROCESAR
        if repository and args.use_repository and track_number:
            try:
                # Buscar producto en repositorio
                repo_track_dir = repository.get_track_dir(orbit_direction, subswath, track_number)
                pair_subdir = "long" if pair_type == 'long' else "short"
                repo_product_file = repo_track_dir / "insar" / pair_subdir / f"Ifg_{pair_name}{('_LONG' if pair_type == 'long' else '')}.dim"

                if repo_product_file.exists():
                    logger.info(f"[{idx}/{total_pairs}] 📦 Encontrado en repositorio: {pair_name} ({pair_type})")
                    # Crear symlink al repositorio
                    try:
                        if not Path(output_file).exists():
                            Path(output_file).symlink_to(repo_product_file.absolute())
                        # Symlink para .data
                        output_data = Path(output_file).with_suffix('.data')
                        repo_data = repo_product_file.with_suffix('.data')
                        if repo_data.exists() and not output_data.exists():
                            output_data.symlink_to(repo_data.absolute())

                        logger.info(f"  ✓ Symlink creado desde repositorio")
                        skipped_from_repo += 1
                        processed += 1
                        continue
                    except Exception as e:
                        logger.warning(f"  ⚠️  Error creando symlink: {e} - procesando normalmente")
            except Exception as e:
                logger.debug(f"  Error consultando repositorio: {e}")

        logger.info(f"[{idx}/{total_pairs}] Procesando par: {pair_name} ({pair_type.upper()})")
        logger.info(f"  Master: {os.path.basename(master)}")
        logger.info(f"  Slave:  {os.path.basename(slave)}")

        # Auto-detectar si son productos pre-procesados (.dim) o originales (.SAFE)
        # IMPORTANTE: los productos generados por SliceAssembly tienen 'MERGED' en el nombre
        # y aunque son .dim, no deben tratarse como pre-procesados (no tienen TOPSAR-Split)
        basename_master = os.path.basename(master)
        is_dim = master.endswith('.dim')
        is_merged = 'MERGED' in basename_master.upper()

        is_preprocessed = args.use_preprocessed or (is_dim and not is_merged)

        if is_preprocessed:
            logger.info(f"  → Tipo: Pre-procesado (.dim)")
        else:
            logger.info(f"  → Tipo: Original (.SAFE) or MERGED (requiere TOPSAR-Split)")

        success = process_pair_with_gpt(master, slave, output_file, is_preprocessed=is_preprocessed, aoi_wkt=aoi_wkt, configured_subswath=subswath)

        if success:
            logger.info(f"  ✅ Completado: {output_file}")

            # GUARDAR AL REPOSITORIO SI ESTÁ HABILITADO
            if repository and args.save_to_repository and track_number:
                try:
                    # Copiar producto al repositorio
                    repo_track_dir = repository.ensure_track_structure(orbit_direction, subswath, track_number)
                    pair_subdir = "long" if pair_type == 'long' else "short"
                    dest_dir = repo_track_dir / "insar" / pair_subdir
                    dest_file = dest_dir / Path(output_file).name

                    if not dest_file.exists():
                        import shutil
                        # Copiar .dim
                        shutil.copy2(output_file, dest_file)
                        # Copiar .data
                        output_data = Path(output_file).with_suffix('.data')
                        if output_data.exists():
                            dest_data = dest_file.with_suffix('.data')
                            shutil.copytree(output_data, dest_data, dirs_exist_ok=True)

                        logger.info(f"  📦 Guardado en repositorio: track {track_number}/{pair_subdir}/")

                        # OPTIMIZACIÓN: Reemplazar archivos locales por symlinks para ahorrar espacio
                        try:
                            # Verificar que la copia al repositorio fue exitosa
                            dest_data = dest_file.with_suffix('.data')
                            if dest_file.exists() and dest_data.exists():
                                # Calcular tamaño para logging
                                local_size_mb = Path(output_file).stat().st_size / (1024 * 1024)

                                # Eliminar archivos locales
                                logger.debug(f"  🗑️  Eliminando producto local para ahorrar espacio...")
                                local_file = Path(output_file)
                                local_data = Path(output_file).with_suffix('.data')

                                if local_file.exists() and not local_file.is_symlink():
                                    local_file.unlink()
                                    logger.debug(f"    ✓ Eliminado: {local_file.name}")

                                if local_data.exists() and not local_data.is_symlink():
                                    shutil.rmtree(local_data)
                                    logger.debug(f"    ✓ Eliminado: {local_data.name}/")

                                # Crear symlinks desde workspace → repositorio
                                local_file.symlink_to(dest_file.absolute())
                                local_data.symlink_to(dest_data.absolute())

                                logger.info(f"  🔗 Symlinks creados: workspace → repositorio (~{local_size_mb:.0f} MB ahorrados)")
                            else:
                                logger.warning(f"  ⚠️  Copia al repositorio incompleta - manteniendo archivos locales")
                        except Exception as e:
                            logger.warning(f"  ⚠️  Error creando symlinks: {e}")
                            logger.warning(f"  Producto guardado en repositorio pero duplicado en workspace")

                        # Actualizar metadata
                        metadata = repository.load_metadata(orbit_direction, subswath, track_number)
                        product_info = repository._extract_insar_info(dest_file, pair_type)
                        metadata['insar_products'].append(product_info)
                        repository.save_metadata(orbit_direction, subswath, track_number, metadata)
                    else:
                        logger.debug(f"  Producto ya existe en repositorio")

                except Exception as e:
                    logger.warning(f"  ⚠️  Error guardando al repositorio: {e}")

            # ISSUE #4: Register InSAR pair in database
            try:
                from scripts.db_queries import register_insar_pair
                from datetime import datetime

                if hasattr(insar_pair_exists, '_db_available') and insar_pair_exists._db_available:
                    # Calculate temporal baseline
                    from datetime import datetime as dt
                    master_dt = dt.strptime(master_date[:8], '%Y%m%d')
                    slave_dt = dt.strptime(slave_date[:8], '%Y%m%d')
                    temporal_baseline_days = abs((slave_dt - master_dt).days)

                    pair_id = register_insar_pair(
                        master_scene_id=master_scene_id,
                        slave_scene_id=slave_scene_id,
                        pair_type=pair_type,
                        subswath=subswath,
                        temporal_baseline_days=temporal_baseline_days,
                        file_path=str(Path(output_file).absolute()),
                        processing_version='2.0'
                    )

                    if pair_id:
                        logger.debug(f"  💾 Registered in database (pair_id={pair_id})")
                    else:
                        logger.warning(f"  ⚠️  Failed to register pair in database")
            except ImportError:
                pass  # DB not available
            except Exception as e:
                logger.warning(f"  ⚠️  Error registering in database: {e}")

            processed += 1
        else:
            logger.error(f"  ❌ FALLÓ")
            failed += 1

    logger.info("")
    logger.info("=" * 80)
    logger.info("RESUMEN")
    logger.info("=" * 80)
    logger.info(f"Pares procesados exitosamente: {processed}")
    if skipped_from_repo > 0:
        logger.info(f"  - Nuevos: {processed - skipped_from_repo}")
        logger.info(f"  - Desde repositorio: {skipped_from_repo}")
    logger.info(f"Pares fallidos: {failed}")
    logger.info(f"Total pares: {total_pairs}")
    if include_long:
        logger.info(f"  - Pares cortos (salto +1): {short_pairs}")
        logger.info(f"  - Pares largos (salto +2): {long_pairs}")
        logger.info("")
        logger.info("✓ Pares listos para análisis de Closure Phase")
    logger.info("")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning("\nInterrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        logger.error(f"ERROR: {e}", exc_info=True)
        sys.exit(1)
