# Comparación de Workflows

**run_complete_workflow.py vs run_smart_workflow.py**

---

## 🎯 Resumen Ejecutivo

| Aspecto | `run_complete_workflow.py` | `run_smart_workflow.py` |
|---------|---------------------------|------------------------|
| **Tipo** | Interactivo (wizard) | CLI con parámetros |
| **Consulta BD** | ❌ No | ✅ Sí |
| **Optimización** | ❌ Siempre procesa todo | ✅ Solo lo necesario |
| **Líneas de código** | ~1,542 | ~471 |
| **Uso** | Guiado paso a paso | Comando directo |
| **Ahorro de tiempo** | 0% | Hasta 99% |

---

## 📋 Diferencias Detalladas

### 1. Modo de Ejecución

#### `run_complete_workflow.py` - INTERACTIVO
```bash
python run_complete_workflow.py
# → Muestra menú interactivo
# → Usuario selecciona AOI de lista
# → Usuario ingresa fechas manualmente
# → Usuario confirma cada paso
```

#### `run_smart_workflow.py` - CLI
```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
# → Ejecución directa con parámetros
```

---

### 2. Funcionalidades

#### `run_complete_workflow.py`

**Características:**
- ✅ Selección interactiva de AOI (lista con búsqueda)
- ✅ Selección interactiva de fechas
- ✅ Descarga automática de productos Sentinel-1
- ✅ Descarga de órbitas
- ✅ Creación de proyecto AOI
- ✅ Selección de series por subswath
- ✅ Procesamiento InSAR completo
- ✅ Procesamiento polarimetría
- ✅ Repositorio compartido automático
- ✅ Confirmaciones paso a paso
- ❌ **NO consulta base de datos**
- ❌ **Siempre procesa todo desde cero**

**Flujo:**
```
1. Mostrar lista de AOIs
2. Usuario selecciona AOI
3. Usuario ingresa fechas
4. SIEMPRE descarga productos
5. SIEMPRE procesa InSAR
6. SIEMPRE procesa polarimetría
7. Crop a AOI
```

---

#### `run_smart_workflow.py`

**Características:**
- ✅ Ejecución por CLI (no interactivo)
- ✅ **Consulta base de datos primero**
- ✅ **Decisión inteligente**: CROP_ONLY, PROCESS_ONLY, FULL_WORKFLOW
- ✅ Modo dry-run (ver plan sin ejecutar)
- ✅ Forzar workflow completo si necesario
- ✅ Confirmación antes de ejecutar
- ✅ **Ahorra hasta 99% de tiempo**
- ❌ No tiene wizard interactivo

**Flujo:**
```
1. Consultar BD: ¿Qué existe?
2. DECIDIR estrategia óptima:

   SI todo procesado:
     → CROP_ONLY (15 min) ⚡

   SI SLCs descargados pero no procesados:
     → PROCESS_ONLY (2-3h)

   SI faltan productos:
     → FULL_WORKFLOW (6-8h)
```

---

### 3. Casos de Uso

#### `run_complete_workflow.py` - Mejor para:

✅ **Primera vez usando el sistema**
- Wizard guiado es más fácil para nuevos usuarios
- No necesitas recordar parámetros

✅ **Exploración de AOIs disponibles**
- Muestra lista completa con búsqueda
- Ver info de cada AOI antes de seleccionar

✅ **Workflow tradicional garantizado**
- Siempre procesa todo
- Útil si quieres reprocesar con nuevos parámetros

---

#### `run_smart_workflow.py` - Mejor para:

✅ **Reutilización de productos**
- Nuevo AOI en mismo track → Solo crop (15 min vs 6-8h)
- Ampliar período temporal → Solo nuevos productos

✅ **Automatización**
- Scripts automáticos
- Integración con otros sistemas
- No requiere interacción humana

✅ **Ahorro de tiempo y recursos**
- Consulta BD antes de procesar
- Solo hace lo estrictamente necesario

✅ **Planificación**
- Modo dry-run para ver qué se hará
- Estimar tiempos antes de ejecutar

---

### 4. Ejemplo Comparativo

**Escenario:** Quieres procesar Vilademuls (track 88), habiendo ya procesado Arenys de Munt (mismo track)

#### Con `run_complete_workflow.py`:

```bash
python run_complete_workflow.py

# Pasos:
# 1. Seleccionar "Vilademuls" de lista → 1 min
# 2. Ingresar fechas → 1 min
# 3. Descargar SLCs → 1-2 horas (INNECESARIO, ya descargados)
# 4. Procesar InSAR → 3-4 horas (INNECESARIO, ya procesado)
# 5. Procesar polarimetría → 1-2 horas (INNECESARIO, ya procesado)
# 6. Crop a AOI → 10-15 min
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL: ~6-8 horas
```

#### Con `run_smart_workflow.py`:

```bash
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/vilademuls.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31

# Pasos:
# 1. Consulta BD: Track 88 ya procesado → 2 segundos
# 2. Decisión: CROP_ONLY
# 3. Crop a nuevo AOI → 10-15 min
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL: ~15 minutos ⚡

AHORRO: 99% de tiempo
```

---

## 🔄 ¿Cuándo Usar Cada Uno?

### Usar `run_complete_workflow.py` cuando:

1. **Primera vez con el sistema** → Wizard es más amigable
2. **No sabes qué AOI procesar** → Exploración interactiva
3. **Quieres reprocesar con nuevos parámetros** → Garantiza todo desde cero
4. **Prefieres confirmación paso a paso** → Más control manual
5. **Base de datos no disponible** → Funciona sin BD

### Usar `run_smart_workflow.py` cuando:

1. **Reutilizar productos existentes** → Ahorra horas
2. **Múltiples AOIs en mismo track** → Crop instantáneo
3. **Ampliar período temporal** → Solo nuevos productos
4. **Automatización** → Scripts, cron jobs, etc.
5. **Quieres ver plan primero** → Modo dry-run
6. **Optimizar recursos** → Solo procesa lo necesario

---

## 💡 Recomendación de Uso

### Workflow Recomendado:

**Primera vez (AOI nuevo, track nuevo):**
```bash
# Opción 1: Workflow completo interactivo
python run_complete_workflow.py

# Opción 2: Smart workflow (también funciona)
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/nuevo_aoi.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

**Siguientes veces (mismo track):**
```bash
# SIEMPRE usa Smart Workflow para ahorrar tiempo
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/otro_aoi_mismo_track.geojson \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
# → Solo 15 minutos en lugar de 6-8 horas ⚡
```

**Ampliar fechas:**
```bash
# Smart workflow detecta qué ya existe
python scripts/run_smart_workflow.py \
  --aoi-geojson aoi/mi_aoi.geojson \
  --start-date 2023-07-01 \
  --end-date 2023-12-31
# → Solo procesa nuevos productos
```

---

## 📊 Matriz de Decisión

| Necesito... | Script a Usar |
|-------------|---------------|
| Ver qué AOIs hay disponibles | `run_complete_workflow.py` |
| Procesar por primera vez | Cualquiera (complete es más fácil) |
| Nuevo AOI, mismo track | `run_smart_workflow.py` ⚡ |
| Ampliar período temporal | `run_smart_workflow.py` ⚡ |
| Ver plan antes de ejecutar | `run_smart_workflow.py --dry-run` |
| Reprocesar con nuevos parámetros | `run_complete_workflow.py` o `--force-full` |
| Script automático | `run_smart_workflow.py` |

---

## 🔮 Futuro

**Posible evolución:**

1. **Integrar Smart Workflow en run_complete_workflow.py**
   - Mantener wizard interactivo
   - Añadir consulta BD antes de procesar
   - Mostrar estimación de tiempo según BD

2. **Modo híbrido**
   - `run_complete_workflow.py --smart`
   - Interactivo pero con optimización BD

---

## 📝 Resumen

**TL;DR:**

- **`run_complete_workflow.py`** = Wizard interactivo completo, siempre procesa todo
- **`run_smart_workflow.py`** = CLI optimizado, consulta BD, ahorra hasta 99% de tiempo

**Ambos son útiles según el contexto:**
- Complete → Primera vez, exploración, control manual
- Smart → Reutilización, automatización, ahorro de tiempo

---

**Versión**: 1.0
**Fecha**: 2025-01-21
