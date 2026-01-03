> **NOTA (2025)**: A partir de 2025, el pipeline goshawk_ETL trabaja **solo con productos SLC** para coherencia InSAR. Este documento se mantiene como referencia técnica sobre procesamiento GRD, pero **GRD ya no se usa** en el flujo de trabajo activo.

---

Aquí tienes un resumen del flujo de trabajo para procesar imágenes SAR (GRD) e InSAR (SLC) utilizando la herramienta GPT (Graph Processing Tool) de SNAP (versión 13\) orquestada a través de Python.

Aunque SNAP tiene una interfaz de Python llamada snappy, la comunidad y la documentación técnica generalmente recomiendan invocar **GPT** a través del módulo subprocess de Python para flujos de producción. Esto es más estable, gestiona mejor la memoria y es compatible con las últimas versiones como SNAP 13\.

### ---

**1\. Procesamiento de Imágenes SAR (GRD)**

Las imágenes **GRD (Ground Range Detected)** contienen información de amplitud (intensidad) y ya han sido proyectadas a rango terrestre, pero necesitan correcciones radiométricas y geométricas para ser útiles en análisis1.

**Flujo de Trabajo Estándar:**

1. **Aplicar Archivo de Órbita (Apply Orbit File):** Corrige la posición del satélite para mejorar la precisión geométrica2.

2. **Calibración Radiométrica (Calibration):** Convierte la intensidad digital a valores físicos reales (Sigma0 o Gamma0) para que sean comparables entre diferentes fechas o sensores3333.

3. **Filtrado de Speckle (Speckle Filtering):** Reduce el ruido característico "sal y pimienta" del radar para suavizar la imagen (opcional pero recomendado)4444.

4. **Corrección de Terreno (Range Doppler Terrain Correction):** Geocodifica la imagen utilizando un Modelo Digital de Elevación (DEM) para corregir distorsiones como *foreshortening* o *layover* y proyectarla en un mapa (ej. WGS84)555.

5. **Conversión a dB:** Convierte la escala lineal a logarítmica para mejor visualización y análisis6666.

**Ejemplo de código Python (Wrapper para GPT):**

Python

import subprocess  
import os

def procesar\_sar\_grd(input\_file, output\_file):  
    """  
    Procesa una imagen S1 GRD: Orbita \-\> Calibración \-\> Corrección de Terreno  
    """  
    gpt\_command \= \[  
        "gpt", "Graph\_GRD\_Processing.xml", \# Asumimos que creaste un grafo XML con los pasos arriba  
        "-Pinput=" \+ input\_file,  
        "-Poutput=" \+ output\_file  
    \]  
      
    \# Ejecutar comando  
    try:  
        subprocess.run(gpt\_command, check=True)  
        print(f"Procesamiento exitoso: {output\_file}")  
    except subprocess.CalledProcessError as e:  
        print(f"Error en GPT: {e}")

\# Nota: Para SNAP 13, es mejor definir los operadores en un archivo XML (.xml)   
\# usando el Graph Builder de SNAP y llamarlo desde Python.

### ---

**2\. Procesamiento de Imágenes InSAR (SLC)**

Las imágenes **SLC (Single Look Complex)** contienen información de **Fase** y Amplitud en la geometría del sensor (slant range). Son necesarias para interferometría (InSAR) y requieren un tratamiento mucho más riguroso para alinear las fases7777.

**Flujo de Trabajo Estándar:**

1. **TOPSAR Split:** Las imágenes Sentinel-1 IW tienen 3 sub-swaths. Debes separar la sub-swath y las ráfagas (bursts) de interés para reducir el tamaño y carga computacional8888.

2. **Aplicar Archivo de Órbita:** Crítico en InSAR para minimizar errores de fase orbital9999.

3. **Corrección ETAD (Opcional pero recomendada):** En versiones nuevas (SNAP 13+), se puede aplicar la corrección *Extended Timing Annotation Dataset* para mejorar la precisión geodésica a nivel centimétrico10101010.

4. **Back Geocoding (Corregistro):** Alinea la imagen "esclava" (secundaria) con la "maestra" (referencia) con precisión de sub-píxel utilizando un DEM. Esto crea un "Stack"111111.

5. **Formación del Interferograma (Interferogram Formation):** Calcula la diferencia de fase entre las dos imágenes y estima la coherencia. Se debe restar la fase de tierra plana (flat-earth phase)12121212.

6. **TOPSAR Deburst:** Elimina las líneas de separación negras entre las ráfagas para crear una imagen continua13131313.

7. **Filtrado de Fase (Goldstein):** Reduce el ruido en el interferograma para facilitar el desempaquetado (unwrapping) posterior14141414.

**Ejemplo de código Python para InSAR:**

Para InSAR, el comando de GPT suele ser largo. Se recomienda construir la cadena de operadores. A continuación, un ejemplo conceptual de cómo encadenar el proceso de alineación y creación del interferograma:

Python

import subprocess

def procesar\_insar\_slc(master\_prod, slave\_prod, output\_target):  
    """  
    Genera un interferograma a partir de dos productos SLC.  
    Pasos: Split \-\> Orbit \-\> BackGeocoding \-\> Interferogram \-\> Deburst  
    """  
      
    \# Definición del Grafo (simplificado para el ejemplo)  
    \# En producción, guarda esto como un archivo .xml usando el Graph Builder de SNAP  
    graph\_xml \= "InSAR\_Graph\_Standard.xml"   
      
    cmd \= \[  
        "gpt", graph\_xml,  
        "-Pmaster=" \+ master\_prod,  
        "-Pslave=" \+ slave\_prod,  
        "-Ptarget=" \+ output\_target,  
        "-c", "20G" \# Asignar memoria RAM (ej. 20GB)  
    \]

    print("Ejecutando generación de Interferograma...")  
    subprocess.run(cmd, check=True)

\# El grafo XML interno debería conectar:  
\# 1\. Read (Master) \-\> TOPSAR-Split \-\> Apply-Orbit  
\# 2\. Read (Slave)  \-\> TOPSAR-Split \-\> Apply-Orbit  
\# 3\. (1) \+ (2) \-\> Back-Geocoding  
\# 4\. Back-Geocoding \-\> Interferogram  
\# 5\. Interferogram \-\> TOPSAR-Deburst \-\> Write

### **Diferencias Clave en el Tratamiento**

* **Geometría:** Para **GRD**, el paso final es llevar la imagen al suelo (Terrain Correction) inmediatamente15. Para **SLC/InSAR**, se debe mantener la geometría *Slant Range* (rango inclinado) durante todo el proceso de interferometría. Solo se geocodifica (Terrain Correction) al final, una vez generado el producto de fase o desplazamiento16161616.

* **Archivos Auxiliares:** InSAR requiere obligatoriamente un DEM preciso para el paso de *Back Geocoding* y *Enhanced Spectral Diversity* (si se usa) para alinear las fases17.

* **Correcciones Atmosféricas/Timing:** Para análisis avanzados en SLC, se pueden aplicar correcciones ETAD para corregir efectos troposféricos y de tiempo sólido de la tierra, capas que se guardan como *Tie Point Grids*18.

### **¿Te gustaría que genere el archivo XML (Graph\_GRD\_Processing.xml o InSAR\_Graph\_Standard.xml) que el código de Python necesita ejecutar?**