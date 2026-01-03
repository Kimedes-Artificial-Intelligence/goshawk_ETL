.PHONY: help setup install test clean status workflow check-deps

# Variables
ENV_NAME := goshawk_etl
CONDA := $(shell command -v mamba 2> /dev/null || command -v conda 2> /dev/null)

help: ## Muestra esta ayuda
	@echo "=========================================="
	@echo "GOSHAWK ETL - Comandos disponibles"
	@echo "=========================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Setup completo (environment + estructura)
	@echo "🚀 Ejecutando setup automático..."
	@bash setup.sh

install: ## Solo instala el environment conda
	@echo "📦 Instalando environment..."
	@$(CONDA) env create -f environment.yml

update: ## Actualiza paquetes del environment
	@echo "🔄 Actualizando environment..."
	@$(CONDA) env update -f environment.yml --prune

activate: ## Muestra comando para activar environment
	@echo "conda activate $(ENV_NAME)"

check-deps: ## Verifica dependencias instaladas
	@echo "🔍 Verificando dependencias..."
	@$(CONDA) run -n $(ENV_NAME) python -c "import geopandas; import rasterio; import snapista; print('✅ Todas las dependencias OK')"

test: ## Ejecuta tests básicos
	@echo "🧪 Ejecutando tests..."
	@$(CONDA) run -n $(ENV_NAME) python -m pytest tests/ -v

workflow: ## Ejecuta workflow completo interactivo
	@echo "▶️  Iniciando workflow..."
	@$(CONDA) run -n $(ENV_NAME) python run_complete_workflow.py

workflow-batch: ## Ejecuta workflow batch (todos los AOIs)
	@echo "▶️  Iniciando workflow batch..."
	@$(CONDA) run -n $(ENV_NAME) python run_batch_aoi_workflow.py

status: ## Muestra estado del proyecto
	@echo "=========================================="
	@echo "Estado del Proyecto"
	@echo "=========================================="
	@echo "Environment: $(ENV_NAME)"
	@$(CONDA) env list | grep $(ENV_NAME) || echo "❌ Environment no instalado"
	@echo ""
	@echo "Datos descargados:"
	@du -sh data/sentinel1_slc 2>/dev/null || echo "  sentinel1_slc: vacío"
	@du -sh data/sentinel1_grd 2>/dev/null || echo "  sentinel1_grd: vacío"
	@du -sh data/orbits 2>/dev/null || echo "  orbits: vacío"
	@echo ""
	@echo "Proyectos procesados:"
	@ls -d processing/*/ 2>/dev/null | wc -l | xargs echo "  Total:"

clean: ## Limpia archivos temporales
	@echo "🧹 Limpiando temporales..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Limpieza completada"

clean-data: ## ⚠️  ELIMINA todos los datos descargados
	@echo "⚠️  ADVERTENCIA: Esto eliminará TODOS los datos descargados"
	@read -p "¿Estás seguro? [y/N]: " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		rm -rf data/sentinel1_slc/* data/sentinel1_grd/* data/orbits/*; \
		echo "✅ Datos eliminados"; \
	else \
		echo "❌ Cancelado"; \
	fi

clean-processing: ## ⚠️  ELIMINA todos los procesamientos
	@echo "⚠️  ADVERTENCIA: Esto eliminará TODOS los procesamientos"
	@read -p "¿Estás seguro? [y/N]: " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		rm -rf processing/*; \
		echo "✅ Procesamientos eliminados"; \
	else \
		echo "❌ Cancelado"; \
	fi

uninstall: ## Desinstala el environment conda
	@echo "🗑️  Eliminando environment..."
	@$(CONDA) env remove -n $(ENV_NAME) -y
	@echo "✅ Environment eliminado"

docs: ## Abre documentación
	@echo "📚 Documentación disponible:"
	@ls -1 docs/*.md

# Atajos rápidos
run: workflow ## Alias para workflow
r: workflow ## Alias corto para workflow
s: status ## Alias corto para status
