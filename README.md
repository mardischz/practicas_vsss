# VSSS Kids Kit — Setup de Cámara y Calibración

Este README guía el proceso para preparar la cámara del sistema VSSS. La cámara es una parte crítica porque funciona como los “ojos” del sistema: permite detectar la cancha, la pelota y los ArUcos de los carritos.

El objetivo de este documento es que cada equipo pueda:

1. Configurar una cámara física o DroidCam.
2. Ver la imagen desde Python.
3. Calibrar la cámara usando un chessboard.
4. Entender por qué la calibración es necesaria para calcular posiciones reales.
5. Dejar listo el sistema para detectar pelota, ArUcos y jugadores.

---

## 0. ¿Qué papel tiene la cámara en VSSS?

En nuestro sistema, la cámara se coloca viendo la cancha desde arriba. Con esa imagen, el código detecta:

* **La cancha / board**, usando ArUcos fijos.
* **Los jugadores**, usando ArUcos sobre los carritos.
* **La pelota**, usando color HSV y forma circular.

El flujo general es:

```text
Cámara
  ↓
Imagen del campo
  ↓
Detección de ArUcos y pelota
  ↓
Conversión a coordenadas x, y
  ↓
Estrategia del jugador
  ↓
Control del carrito
```

Sin cámara no hay visión. Sin visión, el carrito no sabe dónde está ni hacia dónde debe moverse.

---

# Paso 1. Usar DroidCam si no tienes webcam

DroidCam permite usar la cámara del celular como cámara para la computadora.

Esto aplica para equipos que:

* No tienen webcam externa.
* Tienen una cámara de laptop con mala posición.
* Quieren colocar el celular arriba de la cancha para tener mejor vista.

## 1.1 Descargar DroidCam

Instala DroidCam en ambos dispositivos:

1. **En la computadora:** descargar e instalar el cliente / servidor de DroidCam.
2. **En el celular:** descargar la app de DroidCam.

Link de referencia:

```text
https://droidcam-app.translate.goog/windows/?_x_tr_sl=en&_x_tr_tl=es&_x_tr_hl=es&_x_tr_pto=tc
```

También pueden buscarlo como:

```text
DroidCam Windows Client
DroidCam app
```

---

## 1.2 ¿Qué es un personal hotspot?

Un **personal hotspot** es una red Wi-Fi creada por un dispositivo, normalmente un celular o una computadora, para que otros dispositivos se conecten a ella.

En palabras simples:

```text
Un hotspot convierte un dispositivo en un mini-router Wi-Fi.
```

Para usar DroidCam por Wi-Fi, el celular y la computadora deben estar conectados a la **misma red**.

Hay dos opciones comunes:

### Opción A — Usar el mismo Wi-Fi

Si hay una red Wi-Fi estable:

```text
Celular → conectado al Wi-Fi
Computadora → conectada al mismo Wi-Fi
```

### Opción B — Usar personal hotspot

Si no hay Wi-Fi estable, alguien puede crear un hotspot:

```text
Dispositivo con hotspot
  ├── Celular conectado
  └── Computadora conectada
```

Así ambos quedan dentro de la misma red local.

---

## 1.3 Cómo crear un hotspot

### En Windows

1. Abrir **Settings / Configuración**.
2. Ir a **Network & Internet / Red e Internet**.
3. Entrar a **Mobile hotspot / Zona con cobertura inalámbrica móvil**.
4. Activar el hotspot.
5. Revisar el nombre y contraseña de la red.
6. Conectar el celular a esa red.

### Desde un celular

1. Abrir **Configuración**.
2. Buscar **Hotspot personal**, **Zona Wi-Fi** o **Compartir internet**.
3. Activarlo.
4. Conectar la computadora a esa red.
5. Abrir DroidCam en el celular.

> Nota: la computadora y el celular deben estar en la misma red para que la conexión por IP funcione.

---

## 1.4 Identificar el WiFi IP y Port

Cuando abran DroidCam en el celular, la app mostrará información similar a:

```text
WiFi IP: 192.168.###.###
Port: 4747
```

También puede aparecer como:

```text
WiFi IP: 10.###.###.###
Port: ####
```

Estos datos son importantes porque Python usará esta dirección para leer el video del celular.

La estructura que usaremos es:

```text
http://WIFI_IP:PORT/video
```

Ejemplo:

```text
http://192.168.1.20:4747/video
```

---

## 1.5 Cambiar la cámara en el código

Abrir el archivo:

```text
core/cam_config.py
```

Buscar la parte donde se selecciona la cámara global.

Comentar las cámaras que no se usarán y dejar activa DroidCam:

```python
# global_cam = sim_cam
global_cam = droidcam
# global_cam = webcam
```

Esto le dice al sistema:

```text
Usa DroidCam como cámara principal.
```

---

## 1.6 Cambiar el IP de DroidCam

En el mismo archivo:

```text
core/cam_config.py
```

Buscar la función donde se define el IP de DroidCam. Debe verse parecido a esto:

```python
def _get_droidcam_image(rotation=None):
    ip = "http://192.168.1.9:4747/video"
```

Reemplazar el IP y el puerto por los datos que aparecen en la app del celular:

```python
def _get_droidcam_image(rotation=None):
    ip = "http://WIFI_IP:PORT/video"
```

Ejemplo:

```python
def _get_droidcam_image(rotation=None):
    ip = "http://192.168.1.20:4747/video"
```

---

## 1.7 Probar que DroidCam funciona

Correr:

```bash
python3 student/00_check_camera.py
```

Si todo está bien, se debe abrir una ventana con la imagen del celular.

### Si no funciona

Revisar:

* Que el celular y la computadora estén en la misma red.
* Que el IP esté bien escrito.
* Que el puerto esté bien escrito.
* Que DroidCam esté abierto en el celular.
* Que el celular no esté bloqueado.
* Que el URL tenga `/video` al final.

Ejemplo correcto:

```text
http://192.168.1.20:4747/video
```

Ejemplo incompleto:

```text
http://192.168.1.20:4747
```

---

# Paso 2. Calibración de la cámara

## 2.1 ¿Por qué calibramos?

Calibrar la cámara permite que el sistema entienda cómo se ve el mundo real desde esa cámara.

La cámara recibe una imagen 2D en pixeles, pero el robot necesita posiciones reales en centímetros o metros.

La calibración ayuda a corregir:

* Distorsión del lente.
* Perspectiva de la cámara.
* Diferencia entre pixeles y medidas reales.
* Error al calcular posición y orientación de marcadores.

Sin calibración, el sistema puede detectar un ArUco, pero la posición calculada puede estar mal.

En VSSS esto afecta directamente:

```text
posición del robot
posición de la pelota
orientación del carrito
cálculo de distancia
targets de movimiento
control del robot
```

---

## 2.2 ¿Qué usamos para calibrar?

Usamos un **chessboard**, que es un patrón de tablero de ajedrez.

Referencia oficial de OpenCV:

```text
https://github.com/opencv/opencv/blob/master/doc/pattern.png
```
---

## 2.3 Datos que necesitamos del chessboard

Despues de imprimir el tablero de ajedrez o usar el que tengan a la mano, Antes de correr la calibración, se deben ajustar unos valores primero en "camera_calibration.py":

### 1. Tamaño de un cuadrado

Medir el lado de un cuadrado del chessboard.

Debe estar en metros.

Ejemplo:

```text
2.5 cm = 0.025 m
```

Entonces:

```python
SQUARE_SIZE = 0.025
```

### 2. Número de esquinas internas

Importante: OpenCV no usa el número de cuadros, usa el número de **esquinas internas**.

Ejemplo:

```text
Si el tablero tiene 10 cuadros en X y 7 cuadros en Y,
las esquinas internas son 9 en X y 6 en Y.
```

Entonces:

```python
CHESSBOARD_SIZE = (9, 6)
```

---

## 2.4 Tomar fotos para calibración

Tomar entre **10 y 30 fotos** del chessboard usando la cámara que se quiere calibrar. Estas fotos se deben de guardar en la misma carpeta en la cual se guardara el archivo "camera_calibration.py".

Las fotos deben incluir:

* Diferentes distancias, inclinaciones, posiciones dentro de la imagen.
* El tablero completo visible.
* Buena iluminación.
* Imagen enfocada.

### Recomendación práctica

Sostener el chessboard con las manos y moverlo frente a la cámara.

Evitar:

* Fotos borrosas.
* Tablero cortado.
* Reflejos fuertes.
* Sombras muy oscuras.
* Tomar todas las fotos desde el mismo ángulo.

---

## 2.5 Archivo de calibración

Correr:

```text
camera_calibration.py
```

Este archivo se encargará de:

1. Leer las imágenes del chessboard.
2. Detectar las esquinas internas.
3. Calcular la matriz de cámara `K`.
4. Calcular los coeficientes de distorsión `D`.
5. Guardar los resultados.

---

## 2.6 Listoo
AL final se generara un archivo.yaml de nombre "calibration_chessboard". Este archvio contendra los parametros obtenidos de la calibracion de la camara.

## ¡TENEMOS CAMARA CALIBRADA!

Al final de la calibración, el programa debe entregar algo parecido a:

```python
K = [[fx, 0, cx],
     [0, fy, cy],
     [0,  0,  1]]

D = [k1, k2, p1, p2, k3]
```

Donde:

* `K` es la matriz intrínseca de la cámara.
* `D` son los coeficientes de distorsión.

Estos valores se usan en "cam_config.py" donde debemos actualizar nuestras funciones con nuestros valores de la camara actualizada:

Ejemplo:

```python
webcam = Camera(
    K=np.array([[735.09, 0.0, 308.18],
                [0.0, 735.62, 242.58],
                [0.0, 0.0, 1.0]], dtype=np.float32),
    D=np.array([0.15, -1.34, 0.004, -0.004, 2.41], dtype=np.float32),
    frame_getter=_get_webcam_image
)
```
o

```python
droidcam = Camera(
    K=np.array([[476.21413568, 0., 324.64535892], [0., 476.57490297, 242.01755433], [0., 0., 1.]], dtype=np.float32),
    # D=np.array([0.37628059, 0.8828322, -4.22102342, 5.72132593], dtype=np.float32),
    D=np.zeros(5),
    frame_getter=_get_droidcam_image,
    rotation=cv2.ROTATE_90_CLOCKWISE,
    image_shape_hw=(480, 640)  # height, width before rotation
)
```

# Paso 3. Verificación después de calibrar

Después de actualizar `K` y `D`, correr:

```bash
python3 student/01_detect_ball_and_arucos.py
```

Validar:

* Que la cámara abre.
* Que la pelota se detecta.
* Que los ArUcos se detectan.

Luego correr:

```bash
python3 student/02_read_board_coordinates.py
```

Validar:

* Que la pelota tiene coordenadas `x, y`.
* Que las coordenadas cambian de forma lógica al mover la pelota.
* Que el centro de la cancha esté cerca de `(0, 0)` si el board está bien configurado.

---

# Paso 4. Checklist rápido

Antes de avanzar a estrategia y control, revisar:

```text
[ ] La cámara abre correctamente.
[ ] Si uso DroidCam, el IP y PORT están actualizados.
[ ] La computadora y el celular están en la misma red.
[ ] El chessboard fue medido correctamente.
[ ] Se tomaron entre 10 y 30 fotos.
[ ] El tablero aparece completo en las fotos.
[ ] La calibración generó K y D.
[ ] K y D fueron pegados en cam_config.py.
[ ] La pelota se detecta.
[ ] Los ArUcos se detectan.
[ ] El board da coordenadas x, y.
```

---

# Conceptos clave

## Pixel

Punto de la imagen. La cámara ve en pixeles.

## Coordenadas reales

Posición en el tablero o cancha, normalmente en metros.

## Calibración

Proceso para que el sistema entienda cómo la cámara transforma el mundo 3D en una imagen 2D.

## Matriz K

Matriz que describe parámetros internos de la cámara.

## Distorsión D

Valores que corrigen deformaciones del lente.

---

# Después de este paso

AAA JUGARRR WUJU 

## PASO 1 IMPRIMAMOS ESTE BOARD EN TAMAÑO HOJA "vsss_field.png"

## PASO 2 CORRER ESTOS CODIGOS 
