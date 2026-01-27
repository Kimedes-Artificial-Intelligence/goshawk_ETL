#!/usr/bin/env python3
"""
Smart Workflow Planner - Optimiza el workflow consultando la base de datos.

Este script consulta la BD para determinar qué productos ya están procesados
y optimiza el workflow para evitar reprocesamiento innecesario.

Lógica:
1. Usuario selecciona AOI + fechas + órbitas
2. Consulta BD: ¿Qué productos ya existen en el track?
3. Decisión inteligente:
   - Si productos InSAR YA procesados → Solo crop
   - Si productos parcialmente procesados → Completar procesamiento
   - Si no existen → Workflow completo (descarga + proceso)

Author: goshawk_ETL + satelit_metadata integration
Version: 1.0
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add scripts to path
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from db_integration import get_db_integration

logger = logging.getLogger(__name__)


class WorkflowDecision:
    """Representa una decisión de workflow para un track específico."""

    def __init__(
        self,
        orbit_direction: str,
        subswath: str,
        track_number: int,
        aoi_bbox: Dict,
        date_range: Tuple[datetime, datetime],
    ):
        self.orbit_direction = orbit_direction
        self.subswath = subswath
        self.track_number = track_number
        self.aoi_bbox = aoi_bbox
        self.start_date, self.end_date = date_range

        # Will be populated by analyze()
        self.needs_download = True
        self.needs_processing = True
        self.needs_crop_only = False
        self.existing_products = []
        self.missing_dates = []
        self.reason = ""

    def __repr__(self):
        return (
            f"WorkflowDecision({self.orbit_direction}/{self.subswath}/t{self.track_number:03d}: "
            f"download={self.needs_download}, process={self.needs_processing}, crop_only={self.needs_crop_only})"
        )


class SmartWorkflowPlanner:
    """Planifica el workflow consultando la BD para evitar reprocesamiento."""

    def __init__(self):
        self.db = get_db_integration()
        self.db_available = self.db.enabled

    def analyze_track_coverage(
        self,
        orbit_direction: str,
        subswath: str,
        track_number: int,
        start_date: datetime,
        end_date: datetime,
        aoi_bbox: Optional[Dict] = None,
    ) -> WorkflowDecision:
        """
        Analiza la cobertura de productos en un track específico.

        Args:
            orbit_direction: ASCENDING o DESCENDING
            subswath: IW1, IW2, IW3
            track_number: Número de track (1-175)
            start_date: Fecha inicio
            end_date: Fecha fin
            aoi_bbox: Bounding box del AOI (opcional, para filtro espacial)

        Returns:
            WorkflowDecision con la estrategia recomendada
        """
        decision = WorkflowDecision(
            orbit_direction, subswath, track_number, aoi_bbox, (start_date, end_date)
        )

        if not self.db_available:
            # Sin BD, asumir workflow completo
            decision.reason = "Database not available - full workflow required"
            return decision

        try:
            # Obtener estadísticas del track
            stats = self.db.get_track_statistics(orbit_direction, subswath, track_number)

            # Verificar si el track está completamente vacío (sin SLCs ni productos procesados)
            if not stats:
                # Sin stats - necesita todo
                decision.needs_download = True
                decision.needs_processing = True
                decision.needs_crop_only = False
                decision.reason = "Track not found in database - full workflow required"
                return decision

            has_any_products = (
                stats.get("total_slc", 0) > 0 or
                stats.get("total_insar_short", 0) > 0 or
                stats.get("total_insar_long", 0) > 0 or
                stats.get("total_polarimetry", 0) > 0
            )

            if not has_any_products:
                # Track vacío - necesita todo
                decision.needs_download = True
                decision.needs_processing = True
                decision.needs_crop_only = False
                decision.reason = "Track empty in database - full workflow required"
                return decision

            # Consultar productos existentes en el rango de fechas
            from satelit_db.database import get_session
            from satelit_db.api import SatelitDBAPI

            with get_session() as session:
                api = SatelitDBAPI(session)

                # SLCs en el rango de fechas
                # NOTA: Los SLCs tienen subswath='IW' (genérico) porque contienen IW1+IW2+IW3
                # Por lo tanto, buscamos con subswath='IW' independientemente del subswath solicitado
                slcs = api.find_products_by_criteria(
                    product_type="SLC",
                    orbit_direction=orbit_direction,
                    subswath="IW",  # SLCs siempre tienen subswath genérico 'IW'
                    track_number=track_number,
                    start_date=start_date,
                    end_date=end_date,
                )

                if not slcs:
                    decision.reason = f"No products in date range {start_date.date()} - {end_date.date()}"
                    return decision

                # Verificar productos InSAR
                insar_short = api.find_products_by_criteria(
                    product_type="INSAR_SHORT",
                    orbit_direction=orbit_direction,
                    subswath=subswath,
                    track_number=track_number,
                    start_date=start_date,
                    end_date=end_date,
                )

                insar_long = api.find_products_by_criteria(
                    product_type="INSAR_LONG",
                    orbit_direction=orbit_direction,
                    subswath=subswath,
                    track_number=track_number,
                    start_date=start_date,
                    end_date=end_date,
                )

                # Verificar polarimetría
                polar = api.find_products_by_criteria(
                    product_type="POLARIMETRY",
                    orbit_direction=orbit_direction,
                    subswath=subswath,
                    track_number=track_number,
                    start_date=start_date,
                    end_date=end_date,
                )

                decision.existing_products = {
                    "slc": len(slcs),
                    "insar_short": len(insar_short),
                    "insar_long": len(insar_long),
                    "polarimetry": len(polar),
                }

                # DECISIÓN: ¿Qué necesitamos hacer?

                # Caso 1: TODO está procesado → Solo crop
                if (
                    len(slcs) > 0
                    and len(insar_short) > 0
                    and len(insar_long) > 0
                    and len(polar) > 0
                ):
                    # Verificar que SLCs están PROCESADOS
                    processed_slcs = [s for s in slcs if s.processing_status == "PROCESSED"]

                    if len(processed_slcs) == len(slcs):
                        decision.needs_download = False
                        decision.needs_processing = False
                        decision.needs_crop_only = True
                        decision.reason = (
                            f"✅ All products processed ({len(slcs)} SLCs, "
                            f"{len(insar_short)} InSAR short, {len(insar_long)} long, "
                            f"{len(polar)} polarimetry) - CROP ONLY"
                        )
                        return decision

                # Caso 2: SLCs descargados pero no procesados → Procesar (sin descargar)
                downloaded_slcs = [
                    s for s in slcs if s.processing_status in ["DOWNLOADED", "PROCESSED"]
                ]

                if len(downloaded_slcs) == len(slcs) and len(slcs) > 0:
                    decision.needs_download = False
                    decision.needs_processing = True
                    decision.needs_crop_only = False
                    decision.reason = (
                        f"⚡ SLCs already downloaded ({len(slcs)}) - "
                        f"SKIP DOWNLOAD, PROCESS ONLY"
                    )
                    return decision

                # Caso 3: Productos parciales → Completar workflow
                if len(slcs) > 0:
                    decision.needs_download = True  # Puede que falten SLCs
                    decision.needs_processing = True
                    decision.needs_crop_only = False
                    decision.reason = (
                        f"🔄 Partial products ({len(slcs)} SLCs, "
                        f"{len(insar_short)+len(insar_long)} InSAR) - "
                        f"COMPLETE WORKFLOW"
                    )
                    return decision

        except Exception as e:
            logger.warning(f"Error analyzing track coverage: {e}")
            decision.reason = f"Error querying database: {str(e)} - full workflow"

        return decision

    def plan_workflow(
        self,
        aoi_geojson: str,
        start_date: datetime,
        end_date: datetime,
        orbit_directions: List[str],
        subswaths: List[str],
    ) -> Dict[str, WorkflowDecision]:
        """
        Planifica el workflow completo para todas las combinaciones de órbitas/subswaths.

        Args:
            aoi_geojson: Path al archivo GeoJSON del AOI
            start_date: Fecha de inicio
            end_date: Fecha de fin
            orbit_directions: Lista de direcciones de órbita (ej: ['DESCENDING'])
            subswaths: Lista de subswaths (ej: ['IW1', 'IW2'])

        Returns:
            Dict de WorkflowDecisions por track_id
        """
        decisions = {}

        # Load AOI bbox (simplified)
        try:
            with open(aoi_geojson) as f:
                aoi_data = json.load(f)
                coords = aoi_data["features"][0]["geometry"]["coordinates"][0]
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                aoi_bbox = {
                    "min_lon": min(lons),
                    "max_lon": max(lons),
                    "min_lat": min(lats),
                    "max_lat": max(lats),
                }
        except Exception as e:
            logger.warning(f"Could not load AOI bbox: {e}")
            aoi_bbox = None

        # Analizar cada combinación
        for orbit_direction in orbit_directions:
            for subswath in subswaths:
                # Necesitaríamos conocer el track de antemano
                # Por ahora, asumimos tracks comunes para Catalunya
                # En producción, esto se obtendría del primer SLC encontrado

                # Tracks comunes para Catalunya (ejemplo)
                common_tracks = [88, 110] if orbit_direction == "DESCENDING" else [15, 37]

                for track in common_tracks:
                    track_id = f"{orbit_direction.lower()[:4]}_{subswath.lower()}_t{track:03d}"

                    decision = self.analyze_track_coverage(
                        orbit_direction, subswath, track, start_date, end_date, aoi_bbox
                    )

                    decisions[track_id] = decision

        return decisions

    def print_workflow_plan(self, decisions: Dict[str, WorkflowDecision]):
        """Imprime el plan de workflow de forma legible."""
        print("\n" + "=" * 80)
        print("SMART WORKFLOW PLAN")
        print("=" * 80)

        if not self.db_available:
            print("\n⚠️  Database not available - assuming full workflow for all tracks")
            print("   Install satelit_db and start database for optimization")
            return

        for track_id, decision in decisions.items():
            print(f"\n📊 Track: {track_id}")
            print(f"   Decision: {decision.reason}")

            if decision.existing_products:
                print(f"   Existing: {decision.existing_products}")

            print(f"   Actions:")
            if decision.needs_download:
                print(f"      🌐 DOWNLOAD products")
            if decision.needs_processing:
                print(f"      ⚙️  PROCESS InSAR + Polarimetry")
            if decision.needs_crop_only:
                print(f"      ✂️  CROP to AOI only (FAST!)")

        print("\n" + "=" * 80)

        # Summary
        crop_only = sum(1 for d in decisions.values() if d.needs_crop_only)
        process_only = sum(1 for d in decisions.values() if d.needs_processing and not d.needs_download)
        full_workflow = sum(1 for d in decisions.values() if d.needs_download and d.needs_processing)

        print(f"\nSUMMARY:")
        print(f"  ✂️  Crop only (fastest):     {crop_only} tracks")
        print(f"  ⚡ Process only (no download): {process_only} tracks")
        print(f"  🔄 Full workflow:             {full_workflow} tracks")
        print()


def main():
    """Ejemplo de uso."""
    import argparse

    parser = argparse.ArgumentParser(description="Smart Workflow Planner")
    parser.add_argument("--aoi-geojson", required=True, help="Path to AOI GeoJSON")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--orbit",
        choices=["ASCENDING", "DESCENDING"],
        default="DESCENDING",
        help="Orbit direction",
    )
    parser.add_argument(
        "--subswaths",
        nargs="+",
        default=["IW1", "IW2"],
        help="Subswaths to process",
    )

    args = parser.parse_args()

    # Parse dates
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d")

    # Plan workflow
    planner = SmartWorkflowPlanner()
    decisions = planner.plan_workflow(
        aoi_geojson=args.aoi_geojson,
        start_date=start_date,
        end_date=end_date,
        orbit_directions=[args.orbit],
        subswaths=args.subswaths,
    )

    # Print plan
    planner.print_workflow_plan(decisions)


if __name__ == "__main__":
    main()
