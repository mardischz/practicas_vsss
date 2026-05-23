import cv2
import numpy as np
import time
from dataclasses import dataclass

@dataclass
class DetectedObject:
    """
    Contenedor de datos para un objeto detectado en una imagen.

    Todos los campos son opcionales (None por defecto) y se rellenan
    automáticamente por el método detect() de ObjectDetector después
    de que _detect() devuelva la lista con el contorno poblado.

    Campos:
        contour:       Contorno del objeto como array de OpenCV (N, 1, 2).
                       Es el único campo que DEBE rellenar la subclase.
        centroid:      Centro de masa del contorno en píxeles (x, y).
        norm_centroid: Centroide normalizado entre -1 y 1 en ambos ejes,
                       con origen en el centro de la imagen y Y positivo
                       hacia arriba (invertido respecto a imagen).
        area:          Área del contorno en píxeles cuadrados.
        timestamp:     Marca de tiempo Unix (time.time()) del momento de
                       la detección.
        parent:        Referencia al ObjectDetector que generó esta detección.
        source_image:  Frame original (BGR) en el que se detectó el objeto.
    """
    contour : np.ndarray = None        # Contorno del objeto (array OpenCV)
    centroid : tuple = None            # Centro de masa en píxeles (x, y)
    norm_centroid : tuple = None       # Centroide normalizado [-1, 1] en X e Y
    area : float = None                # Área del contorno en píxeles²
    timestamp : float = None           # Tiempo de detección (Unix timestamp)
    parent : 'ObjectDetector' = None   # Detector que generó esta detección
    source_image = None                # Frame original BGR de la detección

class ObjectDetector:
    """
    Clase base para detectores de objetos en imágenes.

    Define el patrón de diseño "Template Method": las subclases implementan
    _detect() con su lógica específica (HSV, ArUco, etc.) y el método público
    detect() se encarga automáticamente de calcular el centroide, el área,
    la normalización y los metadatos de cada detección.

    Para crear un nuevo detector:
      1. Heredar de ObjectDetector.
      2. Implementar _detect() que devuelva DetectedObject con contour poblado.
      3. Opcionalmente definir object_class para usar una subclase de DetectedObject.
    """

    # Clase de objeto que instancia este detector (puede sobreescribirse en subclases)
    object_class = DetectedObject

    def _detect(self, frame, drawing_frame=None):
        """
        Método interno de detección. Las subclases DEBEN sobreescribir este método.

        Solo debe encargarse de detectar los objetos y poblar al menos el campo
        'contour' de cada DetectedObject. Los campos genéricos (centroid, area,
        timestamp, etc.) los calcula el método público detect().

        Args:
            frame:         Frame BGR de entrada donde buscar los objetos.
            drawing_frame: Frame opcional donde dibujar los resultados visuales.
                           Puede ser None si no se necesita visualización.

        Returns:
            Lista de DetectedObject (o subclase) con el campo contour poblado,
            o lista vacía si no se detectó nada.
        """
        pass

    def detect(self, frame, drawing_frame=None):
        """
        Método público de detección. Llama a _detect() y completa automáticamente
        todos los campos genéricos de cada detección.

        Flujo:
          1. Llama a _detect() para obtener detecciones con contornos.
          2. Para cada detección válida:
             - Calcula el centroide en píxeles usando momentos de imagen.
             - Normaliza el centroide a [-1, 1] con origen en el centro.
             - Calcula el área del contorno.
             - Registra timestamp, parent y source_image.

        La normalización del centroide usa el centro de la imagen como origen
        y el eje Y está invertido (positivo hacia arriba, como en matemáticas).

        Args:
            frame:         Frame BGR de entrada.
            drawing_frame: Frame opcional para visualización.

        Returns:
            Lista de DetectedObject completamente poblados. Lista vacía si no
            se encontró nada o si los contornos son inválidos.
        """
        detections = self._detect(frame, drawing_frame)

        if detections is None or not detections:
            return []

        # Dimensiones del frame para normalizar el centroide
        height, width = frame.shape[:2]

        results = []
        for detection in detections:
            # El contorno es el único campo obligatorio que debe venir de _detect()
            contour = detection.contour
            if contour is None or len(contour) == 0:
                continue  # Ignorar detecciones sin contorno válido

            # Calcular centroide en coordenadas de píxel
            centroid = self.get_centroid(contour)
            if centroid is None:
                continue  # Contorno degenerado (área cero), ignorar

            # Normalizar centroide: rango [-1, 1] con origen en el centro de imagen
            # norm_x = -1 en borde izquierdo, +1 en borde derecho
            norm_centroid_x = (centroid[0] - width / 2) / (width / 2)
            # norm_y = -1 en borde inferior, +1 en borde superior (Y invertido)
            norm_centroid_y = (centroid[1] - height / 2) / (height / 2)
            norm_centroid = (norm_centroid_x, -norm_centroid_y)  # Negamos Y para invertirlo

            # Área del contorno en píxeles cuadrados
            area = cv2.contourArea(contour)

            # Poblar los campos genéricos de la detección
            detection.centroid = centroid         # Centro en píxeles (x, y)
            detection.norm_centroid = norm_centroid  # Centro normalizado [-1, 1]
            detection.area = area                 # Área en píxeles²
            detection.timestamp = time.time()     # Momento exacto de la detección
            detection.parent = self               # Referencia al detector
            detection.source_image = frame        # Frame original

            results.append(detection)

        return results

    @staticmethod
    def get_centroid(contour):
        """
        Calcula el centroide (centro de masa) de un contorno usando momentos
        de imagen de OpenCV.

        El centroide se calcula como:
            cx = m10 / m00
            cy = m01 / m00
        donde m00 es el área y m10, m01 son los momentos de primer orden.

        Args:
            contour: Contorno de OpenCV (array de forma (N, 1, 2)).

        Returns:
            (cx, cy) en píxeles como enteros, o None si el contorno es
            inválido o tiene área cero.
        """
        if contour is None or len(contour) == 0:
            return None

        M = cv2.moments(contour)  # Diccionario de momentos espaciales del contorno
        if M['m00'] == 0:
            return None  # Área cero: contorno degenerado o un solo punto

        cx = int(M['m10'] / M['m00'])  # Coordenada X del centroide
        cy = int(M['m01'] / M['m00'])  # Coordenada Y del centroide
        return (cx, cy)

class BallDetector(ObjectDetector):
    """
    Detector de pelota en imágenes usando umbralización en espacio de color HSV.

    El algoritmo:
      1. Aplica blur gaussiano para reducir ruido de alta frecuencia.
      2. Convierte la imagen a HSV (más robusto que BGR para colores bajo
         distintas iluminaciones).
      3. Aplica una máscara de rango de color (inRange) para aislar píxeles
         del color de la pelota.
      4. Aplica erosión + dilatación para eliminar pequeños artefactos.
      5. Encuentra contornos y selecciona el más circular mediante la métrica
         de circularidad: C = 4π·área / perímetro²  (C=1 = círculo perfecto).

    Los umbrales HSV se pueden ajustar en tiempo real haciendo doble-clic
    sobre la pelota en el script principal, y se guardan en ball_thresholds.txt.
    """

    def __init__(self, hsv_lower=None, hsv_upper=None):
        """
        Inicializa el detector con los umbrales de color HSV.

        Si no se proporcionan umbrales, los carga desde ball_thresholds.txt.
        Si el archivo no existe o es inválido, usa los valores por defecto
        para una pelota amarilla (H: 20-30, S: 100-255, V: 100-255).

        Args:
            hsv_lower: Umbral inferior HSV como tupla (H, S, V). H en [0,179],
                       S y V en [0,255].
            hsv_upper: Umbral superior HSV como tupla (H, S, V).
        """
        if hsv_lower is None or hsv_upper is None:
            hsv_lower, hsv_upper = self._load_thresholds()

        # Convertir a arrays uint8 para ser compatibles con cv2.inRange
        self.hsv_lower = np.array(hsv_lower, dtype=np.uint8)  # Umbral inferior de color
        self.hsv_upper = np.array(hsv_upper, dtype=np.uint8)  # Umbral superior de color

    def _load_thresholds(self):
        """
        Carga los umbrales HSV desde el archivo ball_thresholds.txt.

        El archivo tiene el formato:
            H_min,S_min,V_min
            H_max,S_max,V_max

        Si el archivo no existe o tiene formato incorrecto, devuelve los
        valores por defecto para pelota amarilla.

        Returns:
            (lower, upper): Dos tuplas (H, S, V) con los umbrales.
        """
        try:
            with open('ball_thresholds.txt', 'r') as f:
                lines = f.readlines()
                if len(lines) >= 2:
                    lower = tuple(map(int, lines[0].strip().split(',')))
                    upper = tuple(map(int, lines[1].strip().split(',')))
                    return lower, upper
        except (FileNotFoundError, ValueError, IndexError):
            pass  # Archivo no encontrado o formato inválido, usar defaults

        # Valores por defecto: pelota amarilla en buena iluminación
        return (20, 100, 100), (30, 255, 255)

    def _detect(self, frame, drawing_frame=None):
        """
        Detecta la pelota en el frame usando umbralización HSV y selección
        por circularidad.

        Solo devuelve la detección con mayor circularidad (la más parecida
        a un círculo), ya que se asume que solo hay una pelota en la escena.
        Descarta contornos con área < 100 px² o radio < 10 px para filtrar
        ruido y reflejos pequeños.

        Args:
            frame:         Frame BGR de entrada.
            drawing_frame: Frame opcional donde dibujar el contorno de la pelota.

        Returns:
            Lista con un único DetectedObject (la mejor detección) o lista
            vacía si no se encontró ninguna pelota válida.
        """
        # Paso 1: Suavizar para reducir ruido de alta frecuencia (sal y pimienta, etc.)
        blurred = cv2.GaussianBlur(frame, (11, 11), 0)

        # Paso 2: Convertir a HSV para una segmentación de color más robusta
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

        # Paso 3: Crear máscara binaria con los píxeles dentro del rango de color
        mask = cv2.inRange(hsv, self.hsv_lower, self.hsv_upper)

        # Paso 4: Operaciones morfológicas para limpiar la máscara
        mask = cv2.erode(mask, None, iterations=2)   # Erosión: elimina píxeles aislados
        mask = cv2.dilate(mask, None, iterations=2)  # Dilatación: restaura el tamaño original

        # Paso 5: Encontrar contornos en la máscara binaria
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return []  # No se encontró ningún blob de color

        # Paso 6: Seleccionar el contorno más circular entre los candidatos válidos
        best_contour = None     # Mejor contorno encontrado hasta ahora
        best_circularity = 0    # Circularidad del mejor contorno (0 a 1)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 100:  # Filtrar blobs demasiado pequeños (ruido)
                continue

            perimeter = cv2.arcLength(contour, True)
            if perimeter == 0:
                continue  # Contorno degenerado

            # Métrica de circularidad: 1.0 = círculo perfecto, <1 = más irregular
            # Fórmula: C = 4π·A / P²
            circularity = (4 * np.pi * area) / (perimeter ** 2)

            # Filtrar por radio mínimo del círculo envolvente
            ((x, y), radius) = cv2.minEnclosingCircle(contour)
            if radius < 10:  # Filtrar objetos demasiado pequeños en píxeles
                continue

            # Guardar el contorno con mayor circularidad
            if circularity > best_circularity:
                best_circularity = circularity
                best_contour = contour

        if best_contour is None:
            return []  # Ningún contorno superó los filtros

        # Dibujar el contorno de la pelota en el frame de dibujo si se proporcionó
        if drawing_frame is not None:
            cv2.drawContours(drawing_frame, [best_contour], -1, (0, 255, 0), 2)

        return [DetectedObject(contour=best_contour)]

@dataclass
class DetectedAruco(DetectedObject):
    """
    Extiende DetectedObject con campos específicos de marcadores ArUco.

    Hereda todos los campos de DetectedObject (contour, centroid, etc.) y
    agrega información propia del marcador: su ID numérico, el diccionario
    al que pertenece y su ángulo de orientación en la imagen.

    Campos adicionales:
        id:    ID numérico del marcador ArUco (entero definido en el diccionario).
        dict:  Diccionario ArUco usado para la detección (ej. DICT_4X4_100).
               Determina el conjunto de IDs válidos y el patrón de los marcadores.
        angle: Orientación del marcador en radianes, calculada como el ángulo
               del vector de la esquina superior-izquierda a la superior-derecha,
               con un offset de π/2 para que 0 rad = marcador apuntando hacia arriba.
    """
    id : int = None                       # ID numérico del marcador ArUco
    dict : cv2.aruco.Dictionary = None    # Diccionario ArUco de referencia
    angle : float = None                  # Orientación del marcador en radianes

class ArucoDetector(ObjectDetector):
    """
    Detector de marcadores ArUco en imágenes usando el módulo cv2.aruco.

    Detecta TODOS los marcadores visibles en el frame y devuelve una lista
    de DetectedAruco, uno por marcador. Cada detección incluye el contorno
    (las 4 esquinas del marcador), el ID, el diccionario y el ángulo de
    orientación.

    Los marcadores ArUco son cuadrados con un patrón binario único que permite
    identificarlos por ID y estimar su pose. Este detector se usa para rastrear
    los robots en el campo.
    """

    # Clase de objeto que devuelve este detector
    object_class = DetectedAruco

    def __init__(self, aruco_dict):
        """
        Inicializa el detector con el diccionario ArUco a usar.

        Args:
            aruco_dict: Diccionario ArUco de OpenCV (cv2.aruco.Dictionary).
                        Determina qué marcadores se pueden detectar. Ejemplos:
                        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)
        """
        self.aruco_dict = aruco_dict                         # Diccionario de marcadores
        self.detector = cv2.aruco.ArucoDetector(aruco_dict)  # Detector interno de OpenCV

    @staticmethod
    def get_angle(contour):
        """
        Calcula el ángulo de orientación de un marcador ArUco a partir de
        sus esquinas almacenadas en el contorno.

        El ángulo se define como la dirección del vector desde la esquina
        superior-izquierda hacia la esquina superior-derecha del marcador,
        con un offset de π/2 y negación para que:
          - 0 rad  → marcador apuntando hacia arriba (eje Y positivo de imagen)
          - π/2    → marcador apuntando hacia la derecha
          - π/-π   → marcador apuntando hacia abajo

        Args:
            contour: Contorno del marcador en formato OpenCV (N, 1, 2), donde
                     los 4 puntos son [top-left, top-right, bottom-right, bottom-left]
                     en ese orden (convención de ArUco).

        Returns:
            Ángulo en radianes (float), o None si el contorno es inválido.
        """
        if contour is None:
            return None

        # Reinterpretar el contorno como 4 esquinas: shape (1, 4, 2)
        corners = contour.reshape(1, 4, 2)
        top_left  = corners[0][0]  # Esquina superior izquierda del marcador
        top_right = corners[0][1]  # Esquina superior derecha del marcador

        # Vector horizontal del marcador: de top-left a top-right
        dx = top_right[0] - top_left[0]  # Componente X del vector de orientación
        dy = top_right[1] - top_left[1]  # Componente Y del vector (positivo hacia abajo)

        # Ángulo del vector respecto al eje X de la imagen (en radianes)
        angle = np.arctan2(dy, dx)

        # Transformar para que 0 = apuntando arriba:  -(angle - π/2)
        angle = -(angle - np.pi/2)
        return angle

    def _detect(self, frame, drawing_frame=None):
        """
        Detecta todos los marcadores ArUco visibles en el frame.

        Usa el detector interno de OpenCV que devuelve las 4 esquinas de cada
        marcador y sus IDs. Para cada marcador encontrado:
          - Convierte las esquinas al formato de contorno de OpenCV.
          - Calcula el ángulo de orientación.
          - Opcionalmente dibuja el contorno en drawing_frame.

        Args:
            frame:         Frame BGR de entrada.
            drawing_frame: Frame opcional donde dibujar los contornos detectados.

        Returns:
            Lista de DetectedAruco con campos contour, id, dict y angle poblados.
            Lista vacía si no se detectó ningún marcador.
        """
        # Detectar marcadores: corners = lista de arrays (1,4,2), ids = array (N,1)
        corners, ids, _ = self.detector.detectMarkers(frame)

        if ids is None:
            return []  # No se detectó ningún marcador en este frame

        detections = []
        for i, marker_id in enumerate(ids.flatten()):
            # Convertir las 4 esquinas del marcador al formato de contorno de OpenCV
            # corners[i] tiene shape (1, 4, 2) → reshape a (4, 1, 2) para drawContours
            contour = corners[i].reshape(-1, 1, 2).astype(np.int32)

            # Calcular el ángulo de orientación del marcador
            angle = self.get_angle(contour)

            # Dibujar el borde del marcador si se proporcionó un frame de dibujo
            if drawing_frame is not None:
                cv2.drawContours(drawing_frame, [contour], -1, (0, 255, 0), 2)

            detections.append(DetectedAruco(
                contour=contour,
                id=int(marker_id),         # ID numérico del marcador
                dict=self.aruco_dict,      # Diccionario usado
                angle=angle                # Orientación en radianes
            ))

        return detections


if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Script de prueba interactivo para BallDetector y ArucoDetector.
    #
    # Controles:
    #   - Doble-clic sobre la pelota → calibra los umbrales HSV automáticamente
    #   - R                          → reinicia umbrales al amarillo por defecto
    #   - ESC                        → salir
    # -----------------------------------------------------------------------
    from cam_config import global_cam

    # Crear los detectores con configuración por defecto
    ball_detector = BallDetector()  # Carga umbrales desde archivo o usa amarillo
    aruco_detector = ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100))

    # Diccionario compartido entre el hilo principal y el callback del mouse.
    # Almacena el último frame capturado para poder samplear el color al hacer clic.
    click_state = {'frame': None}

    def mouse_callback(event, x, y, flags, param):
        """
        Callback del ratón para calibrar los umbrales de color de la pelota.

        Al hacer doble-clic izquierdo sobre la pelota:
          1. Convierte el frame actual a HSV.
          2. Toma una región de 5x5 px alrededor del clic.
          3. Encuentra el píxel más brillante (mayor V) en esa región.
          4. Crea umbrales con tolerancias centradas en ese color:
               H ± 10, S ± 50, V ± 100
          5. Actualiza el detector en memoria y guarda en ball_thresholds.txt.

        Args:
            event:  Tipo de evento de ratón (cv2.EVENT_*).
            x, y:   Coordenadas del clic en píxeles.
            flags:  Flags adicionales de OpenCV (no usados).
            param:  Parámetro extra (no usado).
        """
        if event == cv2.EVENT_LBUTTONDBLCLK and click_state['frame'] is not None:
            frame = click_state['frame']
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

            # Definir la región 5x5 alrededor del clic (con clamp a bordes de imagen)
            region_size = 5
            y1 = max(0, y - region_size // 2)
            y2 = min(hsv.shape[0], y + region_size // 2 + 1)
            x1 = max(0, x - region_size // 2)
            x2 = min(hsv.shape[1], x + region_size // 2 + 1)

            region = hsv[y1:y2, x1:x2]  # Subregión HSV de 5x5 px

            # Encontrar el píxel con mayor V (brillo) en la región
            # unravel_index convierte el índice lineal del máximo a (fila, columna)
            brightest_idx = np.unravel_index(region[:, :, 2].argmax(), region[:, :, 2].shape)
            h, s, v = region[brightest_idx]  # Valores HSV del píxel más brillante

            # Crear umbrales con tolerancias (usando int para evitar overflow de uint8)
            # Tolerancia amplia en S y V para cubrir zonas con sombra o reflexión
            h_tol, s_tol, v_tol = 10, 50, 100
            lower = (max(0,   int(h) - h_tol), max(0,   int(s) - s_tol), max(0,   int(v) - v_tol))
            upper = (min(179, int(h) + h_tol), min(255, int(s) + s_tol), min(255, int(v) + v_tol))

            # Actualizar el detector con los nuevos umbrales
            ball_detector.hsv_lower = np.array(lower, dtype=np.uint8)
            ball_detector.hsv_upper = np.array(upper, dtype=np.uint8)

            # Guardar los nuevos umbrales en archivo para que persistan entre ejecuciones
            with open('ball_thresholds.txt', 'w') as f:
                f.write(f"{lower[0]},{lower[1]},{lower[2]}\n")
                f.write(f"{upper[0]},{upper[1]},{upper[2]}\n")

            print(f"Updated thresholds: Lower={lower}, Upper={upper}")

    # Registrar el callback del ratón en la ventana de OpenCV
    cv2.namedWindow("Object Detection")
    cv2.setMouseCallback("Object Detection", mouse_callback)

    # Bucle principal de captura y detección
    while True:
        key = cv2.waitKey(1) & 0xFF
        if key == 27:           # ESC: salir
            break
        elif key == ord('r'):   # R: reiniciar umbrales al amarillo por defecto
            ball_detector.hsv_lower = np.array([20, 100, 100], dtype=np.uint8)
            ball_detector.hsv_upper = np.array([30, 255, 255], dtype=np.uint8)
            with open('ball_thresholds.txt', 'w') as f:
                f.write("20,100,100\n")
                f.write("30,255,255\n")
            print("Reset to default yellow thresholds: Lower=(20,100,100), Upper=(30,255,255)")

        # Capturar frame y preparar copia para dibujar
        frame = global_cam.get_frame()
        drawing_frame = frame.copy()   # Copia donde se dibujan anotaciones
        click_state['frame'] = frame   # Compartir frame con el callback del mouse

        # Detectar pelota y mostrar resultado
        ball_detections = ball_detector.detect(frame, drawing_frame=drawing_frame)
        if ball_detections:
            ball = ball_detections[0]  # Solo se espera una pelota
            # Marcar el centroide de la pelota con un punto rojo
            cv2.circle(drawing_frame, ball.centroid, 5, (0, 0, 255), -1)
            print(f"Ball: centroid={ball.centroid}, norm_centroid=({ball.norm_centroid[0]:.3f}, {ball.norm_centroid[1]:.3f}), area={ball.area:.1f}")

        # Detectar marcadores ArUco y mostrar el primero encontrado
        aruco_detections = aruco_detector.detect(frame, drawing_frame=drawing_frame)
        if aruco_detections:
            aruco = aruco_detections[0]  # Mostrar info del primer marcador
            cv2.putText(drawing_frame, f"Aruco pos: ({aruco.norm_centroid[0]:.3f}, {aruco.norm_centroid[1]:.3f})",
                       (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            cv2.putText(drawing_frame, f"Aruco angle: {np.degrees(aruco.angle):.1f} deg",
                       (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # Mostrar instrucciones en el frame
        cv2.putText(drawing_frame, "Double-click ball to configure | R to reset | ESC to exit", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

        # Mostrar el frame anotado
        cv2.imshow("Object Detection", drawing_frame)
        cv2.setWindowProperty("Object Detection", cv2.WND_PROP_TOPMOST, 1)  # Mantener ventana al frente
