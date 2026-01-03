#!/usr/bin/env python3
"""
Script: process_sar_gpt.py
Descripción: Procesa imágenes GRD usando GPT directamente
Uso: 
  python scripts/process_sar_gpt.py                    # Preprocesa automáticamente y luego procesa
  python scripts/process_sar_gpt.py --use-preprocessed # Usa productos ya preprocesados

NUEVO (2024-12-24): El script ahora preprocesa automáticamente los productos GRD
antes de procesarlos, recortándolos al AOI para ahorrar 80-90% de tiempo.

Workflow automático:
  1. Ejecuta preprocess_products.py --grd (recorta al AOI)
  2. Procesa los productos pre-procesados (Calibration → Speckle → Terrain → GLCM)

Si el preprocesamiento falla, continúa con productos originales (modo legacy).
"""

import os
import sys
import glob
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal, Union
from concurrent.futures import ProcessPoolExecutor, as_completed

# Importar utilidades comunes
sys.path.insert(0, os.path.dirname(__file__))
from processing_utils import (
    load_config,
    extract_date_from_filename,
    find_grd_products,
    validate_product,
    logging
)
from burst_utils import auto_merge_bursts, auto_select_grd_products

# Importar sistema de logging centralizado
from logging_utils import LoggerConfig

# Logger se configurará según el contexto (serie o proyecto)
logger = None


def create_sar_workflow_xml(
    input_path: Union[Path, str],
    output_path: Union[Path, str],
    is_preprocessed: bool = False,
    aoi_wkt: Optional[str] = None
) -> str:
    """
    Crea XML para workflow SAR completo

    OPTIMIZADO PARA DETECCIÓN DE ANOMALÍAS DE HUMEDAD (fugas de agua)

    Para pre-procesados: Calibration → Speckle → Terrain-Correction → GLCM → Subset → Write
    Para originales: ApplyOrbit → BorderNoise → Calibration → Speckle → Terrain-Correction → GLCM → Subset → Write

    CAMBIOS CLAVE:
    - GLCM se calcula DESPUÉS de Terrain-Correction (textura real, no distorsión geométrica)
    - Filtro Speckle: Refined Lee 3x3 (preserva anomalías puntuales)
    - Terrain-Correction a 5m de resolución

    Args:
        aoi_wkt: WKT string del AOI para subset geográfico (ej: "POLYGON((lon lat, ...))")
    """
    if is_preprocessed:
        # Workflow para productos pre-procesados (.dim del subset)
        xml = f"""<graph id="SAR_Preprocessed">
  <version>1.0</version>

  <node id="Read">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{input_path}</file>
    </parameters>
  </node>

  <node id="Calibration">
    <operator>Calibration</operator>
    <sources>
      <sourceProduct refid="Read"/>
    </sources>
    <parameters>
      <auxFile>Latest Auxiliary File</auxFile>
      <outputImageInComplex>false</outputImageInComplex>
      <outputImageScaleInDb>false</outputImageScaleInDb>
      <createGammaBand>false</createGammaBand>
      <createBetaBand>false</createBetaBand>
      <selectedPolarisations>VV,VH</selectedPolarisations>
      <outputSigmaBand>true</outputSigmaBand>
      <outputGammaBand>false</outputGammaBand>
      <outputBetaBand>false</outputBetaBand>
    </parameters>
  </node>

  <!-- MODIFICADO: Filtro de moteado conservador para preservar anomalías puntuales -->
  <node id="Speckle-Filter">
    <operator>Speckle-Filter</operator>
    <sources>
      <sourceProduct refid="Calibration"/>
    </sources>
    <parameters>
      <!-- MODIFICADO: De Lee Sigma 7x7 a Refined Lee 3x3 -->
      <!-- Una ventana 7x7 es demasiado grande y borra anomalías puntuales -->
      <!-- de humedad en el asfalto. Refined Lee preserva mejor los bordes -->
      <filter>Refined Lee</filter>
      <filterSizeX>3</filterSizeX>
      <filterSizeY>3</filterSizeY>
      <dampingFactor>2</dampingFactor>
      <estimateENL>true</estimateENL>
      <enl>1.0</enl>
      <numLooksStr>1</numLooksStr>
      <targetWindowSizeStr>3x3</targetWindowSizeStr>
      <sigmaStr>0.9</sigmaStr>
      <anSize>50</anSize>
    </parameters>
  </node>

  <!-- MODIFICADO: Terrain-Correction ANTES de GLCM -->
  <!-- Calcular textura sobre imagen slant-range confunde la geometría -->
  <!-- de edificios (foreshortening) con rugosidad del suelo -->
  <node id="Terrain-Correction">
    <operator>Terrain-Correction</operator>
    <sources>
      <sourceProduct refid="Speckle-Filter"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <imgResamplingMethod>BILINEAR_INTERPOLATION</imgResamplingMethod>
      <!-- Issue #8: Resolución óptima 10m (nativa de GRD, sin sobre-interpolación) -->
      <pixelSpacingInMeter>10.0</pixelSpacingInMeter>
      <mapProjection>WGS84(DD)</mapProjection>
      <alignToStandardGrid>false</alignToStandardGrid>
      <nodataValueAtSea>true</nodataValueAtSea>
      <!-- AÑADIDO: Guardar DEM (elevación H0) para inversión de humedad -->
      <saveDEM>true</saveDEM>
      <saveLatLon>false</saveLatLon>
      <saveSelectedSourceBand>true</saveSelectedSourceBand>
      <applyRadiometricNormalization>false</applyRadiometricNormalization>
      <saveGammaNought>false</saveGammaNought>
      <saveBetaNought>false</saveBetaNought>
      <saveSigmaNought>false</saveSigmaNought>
      <!-- AÑADIDO: Guardar ángulo de incidencia local (LIA) para inversión de humedad -->
      <saveLocalIncidenceAngle>true</saveLocalIncidenceAngle>
      <saveProjectedLocalIncidenceAngle>false</saveProjectedLocalIncidenceAngle>
      <auxFile>Latest Auxiliary File</auxFile>
    </parameters>
  </node>

  <!-- GLCM ahora se calcula DESPUÉS de Terrain-Correction -->
  <!-- Esto asegura que la textura representa rugosidad del suelo real, -->
  <!-- no distorsiones geométricas de la imagen en slant-range -->
  <!-- NOTA: GLCM solo procesa las bandas de backscatter, no DEM ni LIA -->
  <node id="GLCM">
    <operator>GLCM</operator>
    <sources>
      <sourceProduct refid="Terrain-Correction"/>
    </sources>
    <parameters>
      <windowSizeStr>7x7</windowSizeStr>
      <angleStr>ALL</angleStr>
      <quantizerStr>Probabilistic Quantizer</quantizerStr>
      <quantizationLevelsStr>32</quantizationLevelsStr>
      <displacement>1</displacement>
      <outputASM>true</outputASM>
      <outputContrast>true</outputContrast>
      <outputDissimilarity>true</outputDissimilarity>
      <outputEntropy>true</outputEntropy>
      <outputCorrelation>true</outputCorrelation>
      <outputMean>true</outputMean>
      <outputVariance>true</outputVariance>
      <outputMAX>true</outputMAX>
      <outputEnergy>false</outputEnergy>
      <outputHomogeneity>false</outputHomogeneity>
    </parameters>
  </node>

  <!-- BandMerge: Combinar Sigma0 originales + texturas GLCM -->
  <node id="BandMerge">
    <operator>BandMerge</operator>
    <sources>
      <sourceProduct.1 refid="Terrain-Correction"/>
      <sourceProduct.2 refid="GLCM"/>
    </sources>
    <parameters/>
  </node>

  <node id="Subset">
    <operator>Subset</operator>
    <sources>
      <sourceProduct refid="BandMerge"/>
    </sources>
    <parameters>
      <geoRegion>{aoi_wkt if aoi_wkt else ''}</geoRegion>
      <copyMetadata>true</copyMetadata>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="Subset"/>
    </sources>
    <parameters>
      <file>{output_path}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>

</graph>"""
    else:
        # Workflow completo para productos originales (.SAFE)
        xml = f"""<graph id="SAR_Complete">
  <version>1.0</version>

  <node id="Read">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{input_path}</file>
    </parameters>
  </node>

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

  <node id="Remove-GRD-Border-Noise">
    <operator>Remove-GRD-Border-Noise</operator>
    <sources>
      <sourceProduct refid="Apply-Orbit-File"/>
    </sources>
    <parameters>
      <selectedPolarisations>VV,VH</selectedPolarisations>
      <borderLimit>500</borderLimit>
      <trimThreshold>0.5</trimThreshold>
    </parameters>
  </node>

  <node id="Calibration">
    <operator>Calibration</operator>
    <sources>
      <sourceProduct refid="Remove-GRD-Border-Noise"/>
    </sources>
    <parameters>
      <auxFile>Latest Auxiliary File</auxFile>
      <outputImageInComplex>false</outputImageInComplex>
      <outputImageScaleInDb>false</outputImageScaleInDb>
      <createGammaBand>false</createGammaBand>
      <createBetaBand>false</createBetaBand>
      <selectedPolarisations>VV,VH</selectedPolarisations>
      <outputSigmaBand>true</outputSigmaBand>
      <outputGammaBand>false</outputGammaBand>
      <outputBetaBand>false</outputBetaBand>
    </parameters>
  </node>

  <!-- MODIFICADO: Filtro de moteado conservador para preservar anomalías puntuales -->
  <node id="Speckle-Filter">
    <operator>Speckle-Filter</operator>
    <sources>
      <sourceProduct refid="Calibration"/>
    </sources>
    <parameters>
      <!-- MODIFICADO: De Lee Sigma 7x7 a Refined Lee 3x3 -->
      <!-- Una ventana 7x7 es demasiado grande y borra anomalías puntuales -->
      <!-- de humedad en el asfalto. Refined Lee preserva mejor los bordes -->
      <filter>Refined Lee</filter>
      <filterSizeX>3</filterSizeX>
      <filterSizeY>3</filterSizeY>
      <dampingFactor>2</dampingFactor>
      <estimateENL>true</estimateENL>
      <enl>1.0</enl>
      <numLooksStr>1</numLooksStr>
      <targetWindowSizeStr>3x3</targetWindowSizeStr>
      <sigmaStr>0.9</sigmaStr>
      <anSize>50</anSize>
    </parameters>
  </node>

  <!-- MODIFICADO: Terrain-Correction ANTES de GLCM -->
  <!-- Calcular textura sobre imagen slant-range confunde la geometría -->
  <!-- de edificios (foreshortening) con rugosidad del suelo -->
  <node id="Terrain-Correction">
    <operator>Terrain-Correction</operator>
    <sources>
      <sourceProduct refid="Speckle-Filter"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <imgResamplingMethod>BILINEAR_INTERPOLATION</imgResamplingMethod>
      <!-- Issue #8: Resolución óptima 10m (nativa de GRD, sin sobre-interpolación) -->
      <pixelSpacingInMeter>10.0</pixelSpacingInMeter>
      <mapProjection>WGS84(DD)</mapProjection>
      <alignToStandardGrid>false</alignToStandardGrid>
      <nodataValueAtSea>true</nodataValueAtSea>
      <!-- AÑADIDO: Guardar DEM (elevación H0) para inversión de humedad -->
      <saveDEM>true</saveDEM>
      <saveLatLon>false</saveLatLon>
      <saveSelectedSourceBand>true</saveSelectedSourceBand>
      <applyRadiometricNormalization>false</applyRadiometricNormalization>
      <saveGammaNought>false</saveGammaNought>
      <saveBetaNought>false</saveBetaNought>
      <saveSigmaNought>false</saveSigmaNought>
      <!-- AÑADIDO: Guardar ángulo de incidencia local (LIA) para inversión de humedad -->
      <saveLocalIncidenceAngle>true</saveLocalIncidenceAngle>
      <saveProjectedLocalIncidenceAngle>false</saveProjectedLocalIncidenceAngle>
      <auxFile>Latest Auxiliary File</auxFile>
    </parameters>
  </node>

  <!-- GLCM ahora se calcula DESPUÉS de Terrain-Correction -->
  <!-- Esto asegura que la textura representa rugosidad del suelo real, -->
  <!-- no distorsiones geométricas de la imagen en slant-range -->
  <!-- NOTA: GLCM solo procesa las bandas de backscatter, no DEM ni LIA -->
  <node id="GLCM">
    <operator>GLCM</operator>
    <sources>
      <sourceProduct refid="Terrain-Correction"/>
    </sources>
    <parameters>
      <windowSizeStr>7x7</windowSizeStr>
      <angleStr>ALL</angleStr>
      <quantizerStr>Probabilistic Quantizer</quantizerStr>
      <quantizationLevelsStr>32</quantizationLevelsStr>
      <displacement>1</displacement>
      <outputASM>true</outputASM>
      <outputContrast>true</outputContrast>
      <outputDissimilarity>true</outputDissimilarity>
      <outputEntropy>true</outputEntropy>
      <outputCorrelation>true</outputCorrelation>
      <outputMean>true</outputMean>
      <outputVariance>true</outputVariance>
      <outputMAX>true</outputMAX>
      <outputEnergy>false</outputEnergy>
      <outputHomogeneity>false</outputHomogeneity>
    </parameters>
  </node>

  <!-- BandMerge: Combinar Sigma0 originales + texturas GLCM -->
  <node id="BandMerge">
    <operator>BandMerge</operator>
    <sources>
      <sourceProduct.1 refid="Terrain-Correction"/>
      <sourceProduct.2 refid="GLCM"/>
    </sources>
    <parameters/>
  </node>

  <node id="Subset">
    <operator>Subset</operator>
    <sources>
      <sourceProduct refid="BandMerge"/>
    </sources>
    <parameters>
      <geoRegion>{aoi_wkt if aoi_wkt else ''}</geoRegion>
      <copyMetadata>true</copyMetadata>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="Subset"/>
    </sources>
    <parameters>
      <file>{output_path}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>

</graph>"""

    return xml


def process_with_gpt(
    input_path: Union[Path, str],
    output_path: Union[Path, str],
    is_preprocessed: bool = False,
    aoi_wkt: Optional[str] = None
) -> Literal['success', 'skipped', 'failed']:
    """
    Procesa un producto GRD usando GPT

    Returns:
        'success': Procesamiento exitoso
        'skipped': Producto fuera del AOI
        'failed': Error en el procesamiento
    """
    try:
        # Crear XML
        xml = create_sar_workflow_xml(input_path, output_path, is_preprocessed, aoi_wkt)

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
                timeout=7200  # 2 horas timeout (para GLCM con múltiples productos)
            )

            if result.returncode == 0 or os.path.exists(output_path):
                return 'success'
            else:
                # Verificar si el error es por subset vacío (producto fuera del AOI)
                if result.stderr:
                    stderr_lower = result.stderr.lower()
                    # Errores comunes cuando el producto no intersecta con el AOI
                    empty_subset_indicators = [
                        'empty region',
                        'no intersection',
                        'subset is empty',
                        'invalid subset',
                        'region does not intersect',
                        'geometry does not overlap'
                    ]

                    if any(indicator in stderr_lower for indicator in empty_subset_indicators):
                        logger.warning(f"  ⚠️  Producto fuera del AOI - saltando")
                        return 'skipped'  # Producto fuera del AOI, no es un error

                # Error real de GPT
                logger.error(f"  ✗ Error en GPT (exit code {result.returncode})")
                if result.stderr:
                    # Mostrar inicio (donde está el error real) y fin (stack trace)
                    stderr = result.stderr
                    if len(stderr) > 3000:
                        logger.error(f"STDERR (inicio): {stderr[:2000]}")
                        logger.error(f"STDERR (fin): ...{stderr[-1000:]}")
                    else:
                        logger.error(f"STDERR: {stderr}")
                return 'failed'

        finally:
            # Limpiar XML temporal
            if os.path.exists(xml_file):
                os.unlink(xml_file)

    except Exception as e:
        logger.error(f"  ✗ Error: {e}")
        return 'failed'


def process_single_grd(args_tuple):
    """
    Procesa un producto GRD individual (función helper para paralelización)

    Args:
        args_tuple: (index, total, grd_path, output_name, output_dir, is_preprocessed, aoi_wkt)

    Returns:
        tuple: (output_name, result_status)
    """
    index, total, grd_path, output_name, output_dir, is_preprocessed, aoi_wkt = args_tuple

    basename = os.path.basename(grd_path)
    output_file = os.path.join(output_dir, 'sar', f'{output_name}.dim')

    # Importar logger localmente para evitar problemas con multiprocessing
    from logging_utils import LoggerConfig
    from pathlib import Path

    # Recrear logger en el proceso hijo
    cwd = Path.cwd()
    if 'grd_processed' in str(cwd):
        process_logger = LoggerConfig.setup_series_logger(
            series_dir=str(cwd),
            log_name="grd_processing"
        )
    elif 'insar_' in str(cwd):
        process_logger = LoggerConfig.setup_series_logger(
            series_dir=str(cwd),
            log_name="grd_processing"
        )
    else:
        from processing_utils import logger as fallback_logger
        process_logger = fallback_logger

    # Verificar si el producto está completamente procesado
    if os.path.exists(output_file):
        data_dir = output_file.replace('.dim', '.data')
        expected_textures = [
            'Gamma0_VH_ASM.img', 'Gamma0_VH_Contrast.img', 'Gamma0_VH_Dissimilarity.img',
            'Gamma0_VH_Entropy.img', 'Gamma0_VH_GLCMCorrelation.img', 'Gamma0_VH_GLCMMean.img',
            'Gamma0_VH_GLCMVariance.img', 'Gamma0_VH_MAX.img',
            'Gamma0_VV_ASM.img', 'Gamma0_VV_Contrast.img', 'Gamma0_VV_Dissimilarity.img',
            'Gamma0_VV_Entropy.img', 'Gamma0_VV_GLCMCorrelation.img', 'Gamma0_VV_GLCMMean.img',
            'Gamma0_VV_GLCMVariance.img', 'Gamma0_VV_MAX.img',
            # AÑADIDO: Bandas para inversión de humedad del suelo
            'elevation.img',             # DEM (H0)
            'localIncidenceAngle.img'    # LIA
        ]

        all_textures_exist = all(
            os.path.exists(os.path.join(data_dir, texture))
            for texture in expected_textures
        )

        if all_textures_exist:
            process_logger.info(f"[{index}/{total}] ✓ Ya procesado: {output_name}")
            return (output_name, 'already_processed')
        else:
            process_logger.warning(f"[{index}/{total}] ⚠️  Producto incompleto (faltan bandas): {output_name}")
            process_logger.info(f"  → Reprocesando...")
            # Limpiar archivos incompletos
            if os.path.exists(output_file):
                os.remove(output_file)
            if os.path.exists(data_dir):
                import shutil
                shutil.rmtree(data_dir)

    process_logger.info(f"[{index}/{total}] Procesando: {output_name}")
    process_logger.info(f"  Input: {basename}")

    result = process_with_gpt(grd_path, output_file, is_preprocessed=is_preprocessed, aoi_wkt=aoi_wkt)

    if result == 'success':
        process_logger.info(f"  ✅ Completado: {output_file}")
    elif result == 'skipped':
        process_logger.info(f"  ⏭️  Saltado (fuera del AOI)")
    else:  # 'failed'
        process_logger.error(f"  ❌ FALLÓ")

    return output_name, result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description='Procesamiento SAR usando GPT')
    parser.add_argument('--use-preprocessed', action='store_true',
                        help='Usar productos pre-procesados')
    parser.add_argument('--start-date', type=str,
                        help='Fecha inicial (YYYY-MM-DD) - opcional, para compatibilidad')
    parser.add_argument('--end-date', type=str,
                        help='Fecha final (YYYY-MM-DD) - opcional, para compatibilidad')
    args = parser.parse_args()

    # Configurar logger según contexto
    global logger
    cwd = Path.cwd()

    # Detectar si estamos en un directorio de proyecto o serie
    if 'grd_processed' in str(cwd):
        # Estamos en processing/{project}/grd_processed/
        log_dir = cwd / "logs"
        log_dir.mkdir(exist_ok=True)
        logger = LoggerConfig.setup_series_logger(
            series_dir=str(cwd),
            log_name="grd_processing"
        )
    elif 'insar_' in str(cwd):
        # Estamos en una serie individual (legacy)
        log_dir = cwd / "logs"
        log_dir.mkdir(exist_ok=True)
        logger = LoggerConfig.setup_series_logger(
            series_dir=str(cwd),
            log_name="grd_processing"
        )
    else:
        # Fallback: usar logger de processing_utils
        from processing_utils import logger as fallback_logger
        logger = fallback_logger

    logger.info("=" * 80)
    logger.info("PROCESAMIENTO SAR CON GPT")
    logger.info("=" * 80)

    # Cargar configuración
    config = load_config()
    aoi_wkt = config.get('AOI', None)

    if aoi_wkt:
        logger.info(f"AOI configurado: {aoi_wkt[:60]}...")
    else:
        logger.warning("No se encontró AOI en config.txt - procesando escena completa")

    # Variable para almacenar resultado de preprocesamiento
    result = None
    
    # NUEVO: Si no se especifica --use-preprocessed, ejecutar preprocesamiento automáticamente
    if not args.use_preprocessed:
        logger.info("\n" + "=" * 80)
        logger.info("PASO 1: PRE-PROCESAMIENTO GRD (Subset al AOI)")
        logger.info("=" * 80)
        logger.info("📋 Ejecutando preprocesamiento automático para optimizar procesamiento...")
        logger.info("   Esto recorta los productos al AOI (ahorra 80-90% de tiempo)")
        logger.info("")
        
        # Ejecutar preprocesamiento
        preprocess_script = os.path.join(os.path.dirname(__file__), 'preprocess_products.py')
        preprocess_cmd = [sys.executable, preprocess_script, '--grd']
        
        try:
            logger.info(f"⚙️  Comando: python {os.path.basename(preprocess_script)} --grd")
            result = subprocess.run(
                preprocess_cmd,
                check=False,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info("✅ Preprocesamiento completado exitosamente")
                logger.info("")
            else:
                logger.warning(f"⚠️  Preprocesamiento falló (exit code {result.returncode})")
                logger.warning("   Continuando con productos originales...")
                if result.stderr:
                    logger.debug(f"   Error: {result.stderr[:500]}")
                logger.info("")
                # Continuar con productos originales si falla
                grd_dir = config.get('GRD_DIR', 'data/sentinel1_grd')
                logger.info("MODO: Productos ORIGINALES (preprocesamiento falló)")
                args.use_preprocessed = False
        
        except Exception as e:
            logger.warning(f"⚠️  No se pudo ejecutar preprocesamiento: {e}")
            logger.warning("   Continuando con productos originales...")
            grd_dir = config.get('GRD_DIR', 'data/sentinel1_grd')
            logger.info("MODO: Productos ORIGINALES (preprocesamiento no disponible)")
            args.use_preprocessed = False
    
    # Determinar directorio según si hay preprocessed disponibles
    if args.use_preprocessed or (result is not None and result.returncode == 0):
        grd_dir = config.get('PREPROCESSED_GRD_DIR', 'data/preprocessed_grd')
        if not args.use_preprocessed:
            # Si acabamos de preprocesar, añadir separador
            logger.info("=" * 80)
            logger.info("PASO 2: PROCESAMIENTO SAR")
            logger.info("=" * 80)
        logger.info("MODO: Productos PRE-PROCESADOS")
        args.use_preprocessed = True
    else:
        grd_dir = config.get('GRD_DIR', 'data/sentinel1_grd')
        logger.info("MODO: Productos ORIGINALES")

    output_dir = config.get('OUTPUT_DIR', 'processed')
    
    # Obtener configuración de órbita
    target_orbit = config.get('ORBIT_DIRECTION', 'DESCENDING')

    # SELECCIÓN INTELIGENTE DE PRODUCTOS GRD
    # Para cada fecha, selecciona los slices necesarios para cubrir el AOI
    # Filtra por tipo de órbita ANTES de procesar
    logger.info(f"\n📂 Directorio GRD: {grd_dir}")
    grd_products_with_names = auto_select_grd_products(grd_dir, aoi_wkt, target_orbit)

    logger.info(f"\nProductos a procesar: {len(grd_products_with_names)}")
    logger.info("")

    # Procesar cada producto EN PARALELO (3 workers)
    os.makedirs(os.path.join(output_dir, 'sar'), exist_ok=True)

    logger.info(f"\n🚀 Procesando con 3 workers en paralelo...")
    logger.info("")

    processed = 0
    failed = 0
    skipped = 0

    # Preparar argumentos para paralelización
    tasks = [
        (i, len(grd_products_with_names), grd_path, output_name, output_dir, args.use_preprocessed, aoi_wkt)
        for i, (grd_path, output_name) in enumerate(grd_products_with_names, 1)
    ]

    # Procesar en paralelo con ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=3) as executor:
        # Enviar todas las tareas
        future_to_task = {
            executor.submit(process_single_grd, task): task
            for task in tasks
        }

        # Recolectar resultados conforme se completan
        for future in as_completed(future_to_task):
            task = future_to_task[future]
            try:
                output_name, result = future.result()

                if result == 'success':
                    processed += 1
                elif result == 'skipped':
                    skipped += 1
                elif result == 'already_processed':
                    processed += 1
                else:  # 'failed'
                    failed += 1

            except Exception as e:
                logger.error(f"  ❌ Excepción procesando producto: {e}")
                failed += 1

    logger.info("")
    logger.info("=" * 80)
    logger.info("RESUMEN")
    logger.info("=" * 80)
    logger.info(f"Procesados: {processed}")
    logger.info(f"Saltados (fuera del AOI): {skipped}")
    logger.info(f"Fallidos: {failed}")
    logger.info(f"Total: {len(grd_products_with_names)}")
    logger.info("")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        if logger:
            logger.warning("\nInterrumpido por el usuario")
        else:
            print("\nInterrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        if logger:
            logger.error(f"ERROR: {e}", exc_info=True)
        else:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(1)
