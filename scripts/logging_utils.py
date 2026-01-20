#!/usr/bin/env python3
"""
Utilidades de logging centralizadas para el sistema InSAR

Proporciona configuración de logging para:
- Logs de alto nivel (AOI): processing/{aoi}/logs/
- Logs específicos de serie: processing/{aoi}/insar_desc_iwX/logs/
"""

import logging
import sys
from pathlib import Path
from datetime import datetime


class LoggerConfig:
    """Configurador de logging"""

    @staticmethod
    def setup_aoi_logger(aoi_project_dir, log_name, level=logging.INFO, console_level=None):
        """
        Configura logger para operaciones de alto nivel del AOI

        Args:
            aoi_project_dir: Directorio del proyecto AOI (ej: processing/arenys_munt)
            log_name: Nombre del archivo de log (ej: 'orbit_verification')
            level: Nivel de logging para archivo
            console_level: Nivel de logging para consola (None = usar WARNING por defecto)

        Returns:
            logging.Logger: Logger configurado
        """
        log_dir = Path(aoi_project_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"{log_name}.log"
        
        # Limpiar log anterior si existe
        LoggerConfig.clean_old_log(log_file)

        # Para el workflow completo, mostrar INFO en consola
        if console_level is None:
            console_level = logging.INFO if log_name == "workflow_complete" else logging.WARNING

        return LoggerConfig._setup_logger(
            name=f"aoi.{log_name}",
            log_file=log_file,
            level=level,
            console_level=console_level
        )

    @staticmethod
    def setup_series_logger(series_dir, log_name, level=logging.INFO, console_level=logging.WARNING):
        """
        Configura logger para operaciones específicas de una serie

        Args:
            series_dir: Directorio de la serie (ej: processing/arenys_munt/insar_desc_iw1)
            log_name: Nombre del archivo de log (ej: 'insar_processing')
            level: Nivel de logging para archivo
            console_level: Nivel de logging para consola

        Returns:
            logging.Logger: Logger configurado
        """
        log_dir = Path(series_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"{log_name}.log"
        
        # Limpiar log anterior si existe
        LoggerConfig.clean_old_log(log_file)

        return LoggerConfig._setup_logger(
            name=f"series.{log_name}",
            log_file=log_file,
            level=level,
            console_level=console_level
        )

    @staticmethod
    def setup_script_logger(script_name, log_dir='logs', level=logging.INFO, console_level=logging.WARNING):
        """
        Configura logger para scripts standalone sin estructura AOI

        Args:
            script_name: Nombre del script (usado para nombre de archivo)
            log_dir: Directorio base de logs (default: 'logs')
            level: Nivel de logging para archivo
            console_level: Nivel de logging para consola

        Returns:
            logging.Logger: Logger configurado

        Example:
            logger = LoggerConfig.setup_script_logger('preprocess_products')
            # Crea: logs/preprocess_products.log
        """
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        log_file = log_path / f"{script_name}.log"

        return LoggerConfig._setup_logger(
            name=f"script.{script_name}",
            log_file=log_file,
            level=level,
            console_level=console_level
        )

    @staticmethod
    def _setup_logger(name, log_file, level=logging.INFO, console_level=logging.WARNING):
        """
        Configura un logger con formato estándar

        Args:
            name: Nombre del logger
            log_file: Ruta al archivo de log
            level: Nivel de logging para archivo
            console_level: Nivel de logging para consola

        Returns:
            logging.Logger: Logger configurado
        """
        # Crear logger
        logger = logging.getLogger(name)
        logger.setLevel(level)

        # Limpiar handlers existentes
        logger.handlers = []

        # Formato de log
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Handler para archivo
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Handler para consola (nivel configurable)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # Evitar propagación a loggers padres
        logger.propagate = False

        # Log inicial
        logger.info(f"{'='*80}")
        logger.info(f"Log iniciado: {datetime.now().isoformat()}")
        logger.info(f"{'='*80}")

        return logger

    @staticmethod
    def log_section(logger, title):
        """Helper para crear secciones visuales en los logs"""
        logger.info("")
        logger.info(f"{'='*80}")
        logger.info(f"{title}")
        logger.info(f"{'='*80}")

    @staticmethod
    def log_completion(logger, success=True, duration=None):
        """Helper para registrar la finalización de un proceso"""
        logger.info("")
        logger.info(f"{'='*80}")
        if success:
            logger.info("✓ PROCESO COMPLETADO EXITOSAMENTE")
        else:
            logger.error("✗ PROCESO FINALIZADO CON ERRORES")

        if duration:
            logger.info(f"Duración: {duration}")

        logger.info(f"Finalizado: {datetime.now().isoformat()}")
        logger.info(f"{'='*80}")

    @staticmethod
    def log_progress(logger, current, total, item_name):
        """
        Helper para logging de progreso en iteraciones

        Args:
            logger: Logger configurado
            current: Índice actual (1-based)
            total: Total de items
            item_name: Nombre o descripción del item actual

        Example:
            for i, product in enumerate(products, 1):
                LoggerConfig.log_progress(logger, i, len(products), product.name)
        """
        pct = (current / total) * 100 if total > 0 else 0
        logger.info(f"[{current}/{total}] ({pct:.1f}%) {item_name}")

    @staticmethod
    def clean_old_log(log_file):
        """
        Limpia un archivo de log existente antes de reprocesar
        
        Args:
            log_file: Ruta al archivo de log a limpiar
        """
        log_path = Path(log_file)
        if log_path.exists():
            try:
                log_path.unlink()
            except Exception:
                pass  # Ignorar errores si el archivo está en uso

    @staticmethod
    def clean_series_logs(series_dir):
        """
        Limpia TODOS los logs de una serie antes de reprocesar
        Elimina logs con timestamp y logs regulares
        
        Args:
            series_dir: Directorio de la serie
        """
        log_dir = Path(series_dir) / "logs"
        if not log_dir.exists():
            return
        
        try:
            # Eliminar todos los archivos .log
            for log_file in log_dir.glob("*.log"):
                try:
                    log_file.unlink()
                except Exception:
                    pass  # Ignorar errores
        except Exception:
            pass

    @staticmethod
    def log_exception(logger, msg, exc_info=True):
        """
        Helper para logging de excepciones con traceback completo

        Args:
            logger: Logger configurado
            msg: Mensaje descriptivo del error
            exc_info: Si True, incluye traceback completo (default: True)

        Example:
            try:
                process_data()
            except Exception as e:
                LoggerConfig.log_exception(logger, f"Error procesando: {e}")
                raise
        """
        logger.error(msg, exc_info=exc_info)

