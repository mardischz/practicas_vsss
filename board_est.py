import threading
import numpy as np
import cv2
import math

def vecs_to_matrix(rvec, tvec):
    """
    Convierte un vector de rotación (rvec) y un vector de traslación (tvec)
    en una matriz de transformación homogénea 4x4.

    Esta matriz se usa para representar la pose completa (posición + orientación)
    de un objeto en el espacio 3D. La submatriz 3x3 superior izquierda contiene
    la rotación (R) y la columna derecha contiene la traslación (tvec).

    Args:
        rvec: Vector de rotación de Rodrigues (3 elementos). Codifica el eje y
              ángulo de rotación en un solo vector.
        tvec: Vector de traslación (3 elementos) que indica posición X, Y, Z.

    Returns:
        T: Matriz 4x4 de transformación homogénea.
    """
    rvec = np.asarray(rvec, dtype=np.float32)
    tvec = np.asarray(tvec, dtype=np.float32)
    # Convierte el vector de Rodrigues a una matriz de rotación 3x3
    R, _ = cv2.Rodrigues(rvec)
    # Crea una matriz identidad 4x4 y rellena con R y tvec
    T = np.eye(4)
    T[:3, :3] = R       # Bloque de rotación (3x3)
    T[:3, 3] = tvec.flatten()  # Columna de traslación
    return T

def matrix_to_vecs(T):
    """
    Operación inversa a vecs_to_matrix: descompone una matriz de transformación
    homogénea 4x4 en su vector de rotación de Rodrigues (rvec) y vector de
    traslación (tvec).

    Útil para mostrar o guardar la pose en formato compacto, o para pasarla
    a funciones de OpenCV que esperan rvec/tvec.

    Args:
        T: Matriz 4x4 de transformación homogénea.

    Returns:
        rvec: Vector de rotación de Rodrigues (3 elementos).
        tvec: Vector de traslación (3 elementos).
    """
    R = T[:3, :3]   # Extrae la submatriz de rotación 3x3
    tvec = T[:3, 3] # Extrae el vector de traslación
    # Convierte la matriz de rotación de vuelta al vector de Rodrigues
    rvec, _ = cv2.Rodrigues(R)
    return rvec.flatten(), tvec.flatten()

class PnpResult:
    """
    Almacena el resultado de una estimación de pose mediante solvePnP.

    Contiene los puntos detectados en espacio de objeto (tablero) e imagen,
    junto con los vectores de rotación y traslación que describen la pose
    del tablero respecto a la cámara. Ofrece métodos para proyectar puntos
    de imagen al espacio del tablero usando homografía con corrección de
    paralaje para objetos a altura Z > 0.
    """

    def __init__(self, obj_pts, img_pts, tvec, rvec):
        """
        Inicializa el resultado de PnP normalizando los puntos de entrada.

        Args:
            obj_pts: Coordenadas 3D de las esquinas detectadas en el espacio del
                     tablero. Shape esperado (N, 1, 3) o (N, 3). Z suele ser 0
                     porque el tablero es plano.
            img_pts: Coordenadas 2D de esas mismas esquinas en la imagen (píxeles).
                     Shape esperado (N, 1, 2) o (N, 2).
            tvec:    Vector de traslación resultado de solvePnP (posición del
                     tablero respecto a la cámara en metros).
            rvec:    Vector de rotación de Rodrigues resultado de solvePnP.
        """
        # --- Normalización de obj_pts ---
        # Se acepta shape (N,1,3) o (N,3); se aplana y se descartan las Z (siempre 0)
        obj = np.asarray(obj_pts, dtype=np.float32)
        if obj.ndim == 3 and obj.shape[1] == 1 and obj.shape[2] == 3:
            obj = obj.reshape(-1, 3)
        elif obj.ndim == 2 and obj.shape[1] == 3:
            pass
        else:
            raise ValueError(f"Unexpected obj_pts shape {obj.shape}, expected (N,1,3) or (N,3)")

        # Solo se guardan las columnas X e Y (Z=0 en un tablero plano)
        self.obj_pts = obj[:, :2].copy()  # shape (N, 2): coordenadas XY en el tablero

        # --- Normalización de img_pts ---
        img = np.asarray(img_pts, dtype=np.float32)
        if img.ndim == 3 and img.shape[1] == 1 and img.shape[2] == 2:
            img = img.reshape(-1, 2)
        elif img.ndim == 2 and img.shape[1] == 2:
            pass
        else:
            raise ValueError(f"Unexpected img_pts shape {img.shape}, expected (N,1,2) or (N,2)")

        self.img_pts = img.copy()  # shape (N, 2): coordenadas UV en la imagen

        # Pose del tablero respecto a la cámara (resultado de solvePnP)
        self.tvec = tvec  # Traslación: posición del tablero en coordenadas de cámara
        self.rvec = rvec  # Rotación: orientación del tablero en coordenadas de cámara

    def get_ref_T(self):
        """
        Obtiene la matriz de transformación 4x4 que describe la pose del tablero
        respecto a la cámara (de coordenadas del tablero a coordenadas de cámara).

        Esta es la transformación directa que produce solvePnP: si tienes un punto
        en coordenadas del tablero, multiplicarlo por esta matriz te da el punto
        en coordenadas de la cámara.

        Returns:
            Matriz 4x4 numpy que representa la pose del tablero.
        """
        return vecs_to_matrix(self.rvec, self.tvec)

    def get_quad_corners(self):
        """
        Selecciona los cuatro puntos de obj_pts/img_pts que mejor representan
        las esquinas exteriores del tablero (cuadrilátero exterior).

        El algoritmo define cuatro posiciones ideales de esquina (top-left,
        top-right, bottom-right, bottom-left) buscando los extremos en X e Y
        del conjunto de puntos detectados. Luego asigna a cada esquina ideal
        el punto detectado más cercano sin repetir ninguno.

        Este cuadrilátero se usa para calcular la homografía en project_point.

        Returns:
            quad_obj: Array (4, 2) con las coordenadas XY de las 4 esquinas en
                      el espacio del tablero.
            quad_img: Array (4, 2) con las coordenadas UV correspondientes en
                      la imagen.
        """
        N = self.obj_pts.shape[0]  # Número total de puntos detectados
        if N < 4:
            raise ValueError("Need at least 4 points to form a quadrilateral")

        # Rango de coordenadas para encontrar las esquinas extremas del tablero
        xs = self.obj_pts[:, 0]
        ys = self.obj_pts[:, 1]
        min_x, max_x = float(xs.min()), float(xs.max())
        min_y, max_y = float(ys.min()), float(ys.max())

        # Posiciones ideales de las 4 esquinas en el espacio del tablero
        targets = [
            (min_x, min_y),  # Esquina superior izquierda
            (max_x, min_y),  # Esquina superior derecha
            (max_x, max_y),  # Esquina inferior derecha
            (min_x, max_y),  # Esquina inferior izquierda
        ]

        quad_obj = []         # Acumulará las 4 esquinas en espacio de tablero
        quad_img = []         # Acumulará las 4 esquinas en espacio de imagen
        used_indices = set()  # Evita asignar el mismo punto a dos esquinas

        for tx, ty in targets:
            # Calcula la distancia al cuadrado de cada punto a la esquina ideal
            diffs = self.obj_pts - np.array([tx, ty], dtype=np.float32)
            d2 = np.sum(diffs**2, axis=1)  # Distancia cuadrada a cada punto
            idx = int(np.argmin(d2))        # Índice del punto más cercano

            if idx in used_indices:
                # Si ese punto ya fue usado, tomar el siguiente más cercano
                sorted_idxs = np.argsort(d2)
                for candidate in sorted_idxs:
                    if candidate not in used_indices:
                        idx = int(candidate)
                        break

            used_indices.add(idx)
            quad_obj.append(self.obj_pts[idx])
            quad_img.append(self.img_pts[idx])

        quad_obj = np.array(quad_obj, dtype=np.float32)  # shape (4,2)
        quad_img = np.array(quad_img, dtype=np.float32)  # shape (4,2)
        return quad_obj, quad_img

    def project_point(self, point, z=0.0):
        """
        Proyecta un punto en coordenadas de imagen (píxeles) al espacio 2D del
        tablero (en las unidades del tablero, por ejemplo metros o cm).

        El proceso tiene dos etapas:
          1. Homografía: mapea el punto de imagen al plano Z=0 del tablero
             usando los 4 puntos de esquina detectados.
          2. Corrección de paralaje: si el objeto real está a una altura Z > 0
             (por encima del tablero), la homografía introduce un error porque
             asume Z=0. Se corrige calculando el ángulo de visión de la cámara
             al punto y desplazando X e Y según Z * tan(ángulo).

        Args:
            point: Tupla (u, v) con las coordenadas del punto en la imagen.
            z:     Altura real del objeto sobre el plano del tablero (en las
                   mismas unidades que el tablero). Por defecto 0.0 (sin corrección).

        Returns:
            (X_corrected, Y_corrected): Posición del punto en el espacio del tablero.
        """
        # Paso 1: Calcular homografía y proyectar el punto al plano del tablero
        quad_obj, quad_img = self.get_quad_corners()
        # H mapea de coordenadas de imagen a coordenadas del tablero
        H = cv2.getPerspectiveTransform(quad_img, quad_obj)
        pts = np.array([[[point[0], point[1]]]], dtype=np.float32)  # shape (1,1,2)
        projected = cv2.perspectiveTransform(pts, H)                # shape (1,1,2)
        # Resultado inicial en el plano Z=0 del tablero (sin corrección de paralaje)
        X = float(projected[0, 0, 0])
        Y = float(projected[0, 0, 1])
        Z = z  # Altura real del objeto (0 si está sobre el tablero)

        # Paso 2: Corrección de paralaje para objetos elevados (Z > 0)
        # Obtener la posición de la cámara en coordenadas del tablero
        board_T = self.get_ref_T()                # Transforma de tablero a cámara
        cam_T_in_board = np.linalg.inv(board_T)   # Transforma de cámara a tablero
        cam_pos = cam_T_in_board[:3, 3]           # Posición de la cámara en coords del tablero

        # Vector desde la cámara hasta el punto proyectado
        delta_x = X - cam_pos[0]
        delta_y = Y - cam_pos[1]
        delta_z = Z - cam_pos[2]  # Diferencia de profundidad (negativa: cámara está arriba)

        # Ángulo horizontal (en el eje X) entre la cámara y el punto
        angle_x = math.atan2(delta_x, delta_z)
        # Ángulo vertical (en el eje Y) entre la cámara y el punto
        angle_y = math.atan2(delta_y, delta_z)

        # Desplazamiento de paralaje: cuánto se desplaza el punto en XY
        # porque la homografía proyectó sobre Z=0 en vez de Z=z
        offset_x = Z * math.tan(angle_x)
        offset_y = Z * math.tan(angle_y)

        # Posición corregida restando el error de paralaje
        X_corrected = X - offset_x
        Y_corrected = Y - offset_y

        return (X_corrected, Y_corrected)

class BoardEstimator:
    """
    Estimador de pose del tablero a partir de fotogramas de cámara.

    Usa marcadores ArUco/ChArUco detectados en la imagen para calcular la
    posición y orientación del tablero respecto a la cámara mediante solvePnP.
    Opcionalmente rota el frame 180° antes de procesar (útil cuando la cámara
    está montada al revés) y ofrece un método para proyectar puntos de imagen
    al espacio del tablero.
    """

    def __init__(self, board_config, K, D=None, rotate_180=True):
        """
        Inicializa el estimador de pose.

        Args:
            board_config: Objeto de configuración del tablero (BoardConfig) que
                          contiene el tablero ArUco/ChArUco y el detector.
            K:            Matriz intrínseca de la cámara (3x3). Contiene la
                          distancia focal (fx, fy) y el punto principal (cx, cy).
            D:            Coeficientes de distorsión de la cámara (por defecto
                          todos ceros, sin distorsión).
            rotate_180:   Si es True, rota el frame 180° antes de estimar la
                          pose. Necesario cuando la cámara está montada invertida.
        """
        self.config = board_config          # Configuración del tablero (board + detector)
        self.board = board_config.board     # Objeto Board de OpenCV
        self.detector = board_config.detector  # Detector de esquinas/marcadores
        self.K = K                          # Matriz intrínseca de cámara (3x3)
        self.D = D if D is not None else np.zeros(5)  # Coeficientes de distorsión
        self.rotate_180 = rotate_180        # Flag para rotar 180° antes de procesar

    def get_board_transform(self, frame, drawing_frame=None):
        """
        Procesa un fotograma y estima la pose del tablero respecto a la cámara.

        Pasos internos:
          1. Detecta esquinas/marcadores en el frame.
          2. Si rotate_180=True, refleja las coordenadas de esquinas 180° alrededor
             del centro de la imagen (equivale a procesar el frame girado).
          3. Resuelve PnP para obtener rvec/tvec.
          4. Si rotate_180=True, corrige la convención de coordenadas aplicando
             una rotación de 180° en el eje X a la matriz resultante.
          5. Opcionalmente dibuja rvec y tvec en drawing_frame.

        Args:
            frame:         Frame de la cámara en el que se detectarán marcadores.
            drawing_frame: Frame auxiliar sobre el que se dibujan los resultados
                           (esquinas detectadas, ejes, texto). Puede ser None.

        Returns:
            (board_T, pnp_result) si se detectó el tablero, None en caso contrario.
            board_T: Matriz 4x4 de transformación del tablero respecto a la cámara.
            pnp_result: Objeto PnpResult con los puntos y la pose.
        """
        # Detectar esquinas/marcadores en el frame original (para dibujar)
        corners, ids = self.config.detect_corners(frame, drawing_frame=drawing_frame)
        if ids is None:
            return None  # No se detectó el tablero en este frame

        # Si la cámara está montada al revés, reflejar las esquinas 180°
        # para que solvePnP reciba coordenadas como si el frame estuviera derecho
        if self.rotate_180:
            h, w = frame.shape[:2]
            cx, cy = w / 2, h / 2  # Centro de la imagen
            rotated_corners = []
            for corner_set in corners:
                # Cada corner_set tiene shape (1, N, 2): N esquinas del marcador
                rotated_set = corner_set.copy()
                for i in range(rotated_set.shape[1]):
                    x, y = rotated_set[0, i]
                    # Reflexión 180° respecto al centro: (x', y') = (2cx - x, 2cy - y)
                    rotated_set[0, i, 0] = 2 * cx - x
                    rotated_set[0, i, 1] = 2 * cy - y
                rotated_corners.append(rotated_set)
            corners_for_pnp = rotated_corners  # Esquinas corregidas para PnP
        else:
            corners_for_pnp = corners

        # Estimar la pose del tablero usando las esquinas (con offset de centrado)
        res = get_board_pose(self.board, self.K, self.D, corners_for_pnp, ids, offset=self.config.center)
        if res is None:
            return None  # PnP no convergió (pocas esquinas o mala geometría)

        # Convertir rvec/tvec a matriz 4x4 para facilitar transformaciones
        board_T = vecs_to_matrix(res.rvec, res.tvec)

        # Corregir la convención de coordenadas cuando se procesó rotado 180°.
        # Una rotación de 180° en X invierte los ejes Y y Z, compensando el efecto
        # de haber reflejado la imagen.
        if self.rotate_180:
            # Matriz de rotación 180° alrededor del eje X
            R_x_180 = np.array([
                [1,  0,  0,  0],
                [0, -1,  0,  0],
                [0,  0, -1,  0],
                [0,  0,  0,  1]
            ], dtype=np.float64)
            board_T = board_T @ R_x_180  # Aplica corrección post-PnP

        # Mostrar rotación y traslación sobre el frame de dibujo
        if drawing_frame is not None:
            rvec, tvec = matrix_to_vecs(board_T)
            # Convertir rvec a grados para que sea legible
            rvec_string = ', '.join([str(round(math.degrees(x), 3)) for x in rvec])
            tvec_string = ', '.join([str(round(float(x), 3)) for x in tvec])
            cv2.putText(drawing_frame, f"R: {rvec_string}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
            cv2.putText(drawing_frame, f"T: {tvec_string}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

        return board_T, res
    
    def project_point_to_board(self, pnp_result, image_point, frame_shape, z=0.0):
        """
        Proyecta un punto dado en coordenadas de imagen original al espacio 2D
        del tablero, teniendo en cuenta si el frame se procesó rotado 180°.

        Si rotate_180=True:
          - Primero refleja el punto de imagen 180° (para que coincida con el
            sistema de coordenadas usado en el PnP).
          - Luego proyecta con la homografía.
          - Finalmente invierte el eje Y porque en imagen Y crece hacia abajo
            pero en el tablero Y crece hacia arriba.

        Args:
            pnp_result:  Objeto PnpResult devuelto por get_board_transform.
            image_point: Tupla (x, y) en coordenadas del frame original (píxeles).
            frame_shape: Tupla (alto, ancho) o (alto, ancho, canales) del frame.
            z:           Altura del objeto sobre el tablero para corrección de
                         paralaje (por defecto 0.0).

        Returns:
            (X, Y): Coordenadas del punto en el espacio del tablero.
        """
        if self.rotate_180:
            h, w = frame_shape[:2]
            cx, cy = w / 2, h / 2  # Centro del frame
            x, y = image_point
            # Reflejar el punto 180° para que coincida con el sistema del PnP
            rotated_point = (2 * cx - x, 2 * cy - y)
            board_x, board_y = pnp_result.project_point(rotated_point, z=z)
            # Invertir Y: en imagen Y↓, en tablero Y↑
            return (board_x, -board_y)
        else:
            return pnp_result.project_point(image_point, z=z)

def get_board_pose(
    board: cv2.aruco.Board,
    K: np.ndarray,
    D: np.ndarray,
    detected_corners: np.ndarray,
    detected_ids: np.ndarray,
    offset: np.ndarray = None
) -> PnpResult:
    """
    Estima la pose de un tablero ArUco (CharucoBoard o GridBoard) a partir de
    las esquinas detectadas en la imagen.

    Internamente usa board.matchImagePoints para emparejar esquinas detectadas
    con sus coordenadas 3D conocidas en el tablero, y luego llama a solvePnP
    para calcular rvec y tvec.

    Args:
        board:             Objeto cv2.aruco.Board que define la geometría del tablero.
        K:                 Matriz intrínseca de la cámara (3x3).
        D:                 Coeficientes de distorsión de la cámara.
        detected_corners:  Lista de arrays con las esquinas detectadas por el detector.
        detected_ids:      IDs de los marcadores correspondientes a cada esquina.
        offset:            Offset 3D opcional que se resta a los puntos 3D del tablero
                           antes de resolver PnP. Sirve para cambiar el origen de
                           coordenadas al centro del tablero en vez de su esquina.

    Returns:
        PnpResult con la pose estimada, o None si falla la detección o PnP.
    """
    # Emparejar las esquinas detectadas con sus posiciones 3D reales en el tablero
    obj_pts, img_pts = board.matchImagePoints(detected_corners, detected_ids)
    if obj_pts is None or obj_pts.shape[0] < 6:
        # Se necesitan al menos 6 puntos para una solución robusta de PnP
        return None

    # Recentrar el sistema de coordenadas restando el offset (ej: centro del tablero)
    if offset is not None:
        obj_pts = obj_pts - offset

    # Resolver PnP: encuentra rvec y tvec que minimizan el error de reproyección
    # SOLVEPNP_ITERATIVE: método iterativo de Levenberg-Marquardt, preciso y estable
    success, rvec, tvec = cv2.solvePnP(
        obj_pts,   # Puntos 3D del tablero (en metros)
        img_pts,   # Puntos 2D en la imagen (en píxeles)
        K,         # Matriz intrínseca
        D,         # Distorsión
        flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not success:
        return None  # PnP no convergió

    return PnpResult(obj_pts=obj_pts, img_pts=img_pts, tvec=tvec.flatten(), rvec=rvec.flatten())

def get_cam_T(ref_T: np.ndarray) -> np.ndarray:
    """
    Calcula la posición y orientación de la cámara en el sistema de coordenadas
    del tablero (referencia), invirtiendo la transformación tablero→cámara.

    solvePnP devuelve la pose del tablero vista desde la cámara (ref_T).
    Invertir esa matriz da la pose de la cámara vista desde el tablero,
    es decir, dónde y cómo apunta la cámara en el espacio del tablero.

    Args:
        ref_T: Matriz 4x4 de transformación tablero→cámara (salida de solvePnP).

    Returns:
        cam_T: Matriz 4x4 de transformación cámara→tablero.
    """
    # Invertir la transformación para obtener cámara en coordenadas del tablero
    cam_T = np.linalg.inv(ref_T)
    return cam_T

class BoardPlotter3D:
    """
    Visualización 3D en tiempo real de la pose del tablero respecto a la cámara
    usando Matplotlib (backend TkAgg).

    Soporta dos modos de visualización:
      - camera_at_origin=True:  La cámara está fija en el origen y el tablero
        se mueve. Útil para ver cómo se mueve el tablero en el espacio.
      - camera_at_origin=False: El tablero está fijo en el origen y la cámara
        se mueve. Útil para ver la trayectoria de la cámara sobre el tablero.

    Cada modo dibuja un marco de referencia fijo (ejes XYZ) para el objeto en
    el origen, y actualiza el objeto móvil en cada llamada a update().
    """

    def __init__(self, board_config, axis_limit=1.0, update_interval=10, camera_at_origin=True):
        """
        Inicializa el visualizador 3D y crea la ventana de Matplotlib.

        Args:
            board_config:      Objeto BoardConfig para obtener las dimensiones
                               físicas del tablero (ancho y alto en metros).
            axis_limit:        Límite de los ejes en metros. Define el cubo
                               visible [-axis_limit, axis_limit] en X e Y,
                               y [0, 2*axis_limit] en Z. Por defecto 1.0 m.
            update_interval:   Actualiza la visualización cada N frames para
                               reducir la carga computacional. Por defecto 10.
            camera_at_origin:  True → cámara en el origen, tablero móvil.
                               False → tablero en el origen, cámara móvil.
        """
        # Importar matplotlib aquí (importación tardía) para no cargar la GUI
        # hasta que realmente se necesite el visualizador
        import matplotlib
        matplotlib.use('TkAgg')  # Backend no-threaded compatible con OpenCV
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        self.plt = plt                         # Referencia al módulo pyplot
        self.Poly3DCollection = Poly3DCollection  # Clase para polígonos 3D

        self.board_config = board_config        # Configuración del tablero
        self.axis_limit = axis_limit            # Rango visible de los ejes (m)
        self.update_interval = update_interval  # Frecuencia de actualización
        self.camera_at_origin = camera_at_origin  # Modo de visualización
        self.frame_count = 0                    # Contador de frames procesados

        # Dimensiones físicas del tablero (ancho y alto en metros)
        self.board_width, self.board_height = board_config.get_board_dimensions()

        # Crear la figura y el subplot 3D con modo interactivo activado
        plt.ion()  # Modo interactivo: permite actualizar sin bloquear el hilo
        self.fig = plt.figure(figsize=(8, 6))
        self.ax = self.fig.add_subplot(111, projection='3d')

        # Artistas de Matplotlib que se eliminarán y redibujaran en cada update
        self.board_poly = None   # Polígono del tablero (superficie plana)
        self.board_quivers = []  # Flechas de los ejes del objeto móvil
        self.camera_artists = [] # Artistas del marco de referencia fijo

        self._setup_plot()  # Configurar límites, etiquetas y ángulo de vista

        # Dibujar el marco de referencia fijo según el modo elegido
        if self.camera_at_origin:
            self._draw_camera_frame()  # Cámara fija en el origen
        else:
            self._draw_board_frame()   # Tablero fijo en el origen

        # Re-inicializar artistas móviles (se sobreescriben al actualizar)
        self.board_poly = None
        self.board_quivers = []

        # Mostrar la ventana sin bloquear el bucle principal
        self.plt.show(block=False)
        self.plt.pause(0.001)
        
    def _setup_plot(self):
        """
        Configura los límites, etiquetas y ángulo de vista inicial del gráfico 3D.
        Se llama una sola vez durante la inicialización.
        """
        # Definir el cubo visible: eje Z empieza en 0 porque la cámara está encima
        self.ax.set_xlim([-self.axis_limit, self.axis_limit])
        self.ax.set_ylim([-self.axis_limit, self.axis_limit])
        self.ax.set_zlim([0, 2 * self.axis_limit])

        self.ax.set_xlabel('X (m)')
        self.ax.set_ylabel('Y (m)')
        self.ax.set_zlabel('Z (m)')
        self.ax.set_title('Board Pose Estimation')

        # Ángulo de vista: elevación 20° y azimut 45° para una perspectiva clara
        self.ax.view_init(elev=20, azim=45)
        
    def _draw_camera_frame(self):
        """
        Dibuja el sistema de referencia de la cámara en el origen (solo una vez).
        Muestra tres flechas: roja=X, verde=Y, azul=Z.
        Se llama al inicio cuando camera_at_origin=True.
        """
        axis_length = 0.2  # Longitud de las flechas de los ejes (en metros)

        # Eje X (rojo), Y (verde), Z (azul) de la cámara en el origen
        self.camera_artists.append(
            self.ax.quiver(0, 0, 0, axis_length, 0, 0, color='r', arrow_length_ratio=0.3, linewidth=2)
        )
        self.camera_artists.append(
            self.ax.quiver(0, 0, 0, 0, axis_length, 0, color='g', arrow_length_ratio=0.3, linewidth=2)
        )
        self.camera_artists.append(
            self.ax.quiver(0, 0, 0, 0, 0, axis_length, color='b', arrow_length_ratio=0.3, linewidth=2)
        )
    
    def _draw_board_frame(self):
        """
        Dibuja el sistema de referencia del tablero y su superficie plana en el
        origen (solo una vez). Se llama al inicio cuando camera_at_origin=False.
        Incluye las tres flechas de ejes y un polígono gris semitransparente
        que representa la superficie del tablero.
        """
        axis_length = 0.2  # Longitud de las flechas de los ejes (en metros)

        # Ejes del tablero en el origen: X (rojo), Y (verde), Z (azul)
        self.camera_artists.append(
            self.ax.quiver(0, 0, 0, axis_length, 0, 0, color='r', arrow_length_ratio=0.3, linewidth=2)
        )
        self.camera_artists.append(
            self.ax.quiver(0, 0, 0, 0, axis_length, 0, color='g', arrow_length_ratio=0.3, linewidth=2)
        )
        self.camera_artists.append(
            self.ax.quiver(0, 0, 0, 0, 0, axis_length, color='b', arrow_length_ratio=0.3, linewidth=2)
        )

        # Dibujar la superficie del tablero como polígono gris en Z=0
        corners = self._get_board_corners()  # Esquinas en coordenadas locales
        verts = [corners]
        board_poly = self.Poly3DCollection(verts, alpha=0.3, facecolor='gray', edgecolor='black', linewidth=2)
        self.ax.add_collection3d(board_poly)
        self.camera_artists.append(board_poly)
        
    def _get_board_corners(self):
        """
        Devuelve las cuatro esquinas del tablero en su sistema de coordenadas
        local, centradas en el origen.

        El tablero se define en el plano XY (Z=0), con el centro en (0,0,0).
        Las dimensiones vienen de board_config.get_board_dimensions().

        Returns:
            corners: Array (4, 3) con las posiciones 3D de las esquinas.
        """
        w, h = self.board_width, self.board_height  # Ancho y alto del tablero

        # Esquinas centradas en el origen, en el plano Z=0
        corners = np.array([
            [-w/2, -h/2, 0],  # Inferior izquierda
            [ w/2, -h/2, 0],  # Inferior derecha
            [ w/2,  h/2, 0],  # Superior derecha
            [-w/2,  h/2, 0],  # Superior izquierda
        ])

        return corners
    
    def _transform_points(self, points, T):
        """
        Aplica una transformación homogénea 4x4 a un conjunto de puntos 3D.

        Convierte los puntos a coordenadas homogéneas (agrega columna de 1s),
        multiplica por T y devuelve los puntos transformados en 3D.

        Usado para transformar las esquinas del tablero al sistema de coordenadas
        de la cámara (o viceversa) antes de dibujarlas en el gráfico.

        Args:
            points: Array (N, 3) con N puntos en 3D.
            T:      Matriz (4, 4) de transformación homogénea.

        Returns:
            Array (N, 3) con los puntos transformados.
        """
        # Convertir a coordenadas homogéneas: agregar columna de 1s → shape (N, 4)
        points_h = np.hstack([points, np.ones((points.shape[0], 1))])

        # Aplicar la transformación: T @ points_h.T → shape (4, N), luego trasponer
        points_transformed = (T @ points_h.T).T

        # Descartar la coordenada homogénea (w) y devolver solo X, Y, Z
        return points_transformed[:, :3]
    
    def update(self, board_T):
        """
        Actualiza la visualización con la nueva pose del tablero.

        Solo redibuja cada update_interval frames para evitar que el plot
        ralentice el bucle principal de captura de video.

        En modo camera_at_origin=True dibuja el tablero moviéndose.
        En modo camera_at_origin=False invierte la transformación para mostrar
        la cámara moviéndose sobre un tablero fijo.

        Args:
            board_T: Matriz 4x4 de transformación tablero→cámara (de solvePnP).
        """
        # Contar frames y saltarse actualizaciones para reducir la carga
        self.frame_count += 1
        if self.frame_count % self.update_interval != 0:
            return  # Aún no toca actualizar

        # En modo tablero-fijo, invertir la transformación para obtener
        # la pose de la cámara en coordenadas del tablero
        if not self.camera_at_origin:
            board_T = np.linalg.inv(board_T)

        # Eliminar los artistas del frame anterior antes de redibujar
        if self.board_poly is not None:
            self.board_poly.remove()
        for quiver in self.board_quivers:
            quiver.remove()
        self.board_quivers.clear()

        # Redibujar según el modo de visualización
        if self.camera_at_origin:
            self._draw_moving_board(board_T)   # Tablero móvil, cámara fija
        else:
            self._draw_moving_camera(board_T)  # Cámara móvil, tablero fijo

        # Refrescar la ventana sin bloquear el hilo principal
        self.fig.canvas.flush_events()
    
    def _draw_moving_board(self, board_T):
        """
        Dibuja el tablero (superficie + ejes) en su posición transformada.
        Usado cuando camera_at_origin=True (tablero se mueve).

        Args:
            board_T: Matriz 4x4 de pose del tablero en coordenadas de cámara.
        """
        # Transformar las esquinas locales del tablero al sistema de la cámara
        corners_local = self._get_board_corners()             # Esquinas en coords locales
        corners_camera = self._transform_points(corners_local, board_T)  # En coords de cámara

        # Dibujar el tablero como polígono relleno semitransparente (cian)
        verts = [corners_camera]
        self.board_poly = self.Poly3DCollection(verts, alpha=0.5, facecolor='cyan', edgecolor='darkblue', linewidth=2)
        self.ax.add_collection3d(self.board_poly)

        # Dibujar los ejes de coordenadas del tablero en su posición actual
        board_origin = board_T[:3, 3]  # Posición del centro del tablero (columna de traslación)
        axis_length = 0.15             # Longitud de las flechas de ejes (metros)

        # Las columnas de la matriz de rotación son los vectores de los ejes locales
        x_axis = board_T[:3, 0] * axis_length  # Dirección X del tablero
        y_axis = board_T[:3, 1] * axis_length  # Dirección Y del tablero
        z_axis = board_T[:3, 2] * axis_length  # Dirección Z del tablero (normal)

        # Flecha roja = eje X, verde = eje Y, azul = eje Z
        self.board_quivers.append(
            self.ax.quiver(board_origin[0], board_origin[1], board_origin[2],
                          x_axis[0], x_axis[1], x_axis[2],
                          color='r', arrow_length_ratio=0.3, linewidth=1.5, alpha=0.7)
        )
        self.board_quivers.append(
            self.ax.quiver(board_origin[0], board_origin[1], board_origin[2],
                          y_axis[0], y_axis[1], y_axis[2],
                          color='g', arrow_length_ratio=0.3, linewidth=1.5, alpha=0.7)
        )
        self.board_quivers.append(
            self.ax.quiver(board_origin[0], board_origin[1], board_origin[2],
                          z_axis[0], z_axis[1], z_axis[2],
                          color='b', arrow_length_ratio=0.3, linewidth=1.5, alpha=0.7)
        )
    
    def _draw_moving_camera(self, cam_T):
        """
        Dibuja solo los ejes de la cámara en su posición transformada (sin superficie).
        Usado cuando camera_at_origin=False (cámara se mueve sobre el tablero fijo).

        Args:
            cam_T: Matriz 4x4 de pose de la cámara en coordenadas del tablero
                   (resultado de invertir board_T en update()).
        """
        camera_origin = cam_T[:3, 3]  # Posición de la cámara en el espacio del tablero
        axis_length = 0.15            # Longitud de las flechas de ejes (metros)

        # Extraer los vectores de los ejes de la cámara desde la matriz de rotación
        x_axis = cam_T[:3, 0] * axis_length  # Dirección X de la cámara
        y_axis = cam_T[:3, 1] * axis_length  # Dirección Y de la cámara
        z_axis = cam_T[:3, 2] * axis_length  # Dirección de apunte (eje óptico)

        # Flecha roja = eje X, verde = eje Y, azul = eje óptico Z
        self.board_quivers.append(
            self.ax.quiver(camera_origin[0], camera_origin[1], camera_origin[2],
                          x_axis[0], x_axis[1], x_axis[2],
                          color='r', arrow_length_ratio=0.3, linewidth=1.5, alpha=0.7)
        )
        self.board_quivers.append(
            self.ax.quiver(camera_origin[0], camera_origin[1], camera_origin[2],
                          y_axis[0], y_axis[1], y_axis[2],
                          color='g', arrow_length_ratio=0.3, linewidth=1.5, alpha=0.7)
        )
        self.board_quivers.append(
            self.ax.quiver(camera_origin[0], camera_origin[1], camera_origin[2],
                          z_axis[0], z_axis[1], z_axis[2],
                          color='b', arrow_length_ratio=0.3, linewidth=1.5, alpha=0.7)
        )
    
    def close(self):
        """
        Cierra la ventana del gráfico 3D y libera los recursos de Matplotlib.
        Llamar este método al terminar para evitar ventanas huérfanas.
        """
        self.plt.close(self.fig)

if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Script de prueba: estima la pose del tablero en tiempo real y la muestra
    # en una ventana 3D con Matplotlib y en el feed de la cámara con OpenCV.
    # Presionar ESC para salir.
    # -----------------------------------------------------------------------
    import cv2
    from board_config import global_board_config, board_config_letter
    from cam_config import global_cam, webcam, droidcam, sim_cam

    # Seleccionar la configuración del tablero a detectar
    # board_config = global_board_config  # Tablero global (por defecto)
    board_config = board_config_letter    # Tablero tamaño carta

    # Seleccionar la fuente de video
    active_cam = droidcam  # Cámara IP (DroidCam)

    # Crear el estimador de pose con los parámetros de la cámara seleccionada
    be = BoardEstimator(
        board_config=board_config,
        K=active_cam.K,  # Matriz intrínseca calibrada de la cámara
        D=active_cam.D,  # Coeficientes de distorsión calibrados
        # rotate_180=False,  # Descomentar si la cámara no está montada al revés
    )

    # Crear el visualizador 3D (tablero en el origen, cámara moviéndose)
    plotter = BoardPlotter3D(
        board_config,
        axis_limit=0.5,         # Rango visible: cubo de 0.5 m
        # camera_at_origin=True,  # Modo alternativo: cámara fija, tablero móvil
        camera_at_origin=False, # Modo actual: tablero fijo, cámara móvil
    )

    # Bucle principal de captura y estimación
    while True:
        if cv2.waitKey(1) & 0xFF == 27:  # ESC para salir
            break

        # Capturar un frame de la cámara
        frame = active_cam.get_frame()
        drawing_frame = frame.copy()  # Copia para dibujar sin modificar el original

        # Estimar la pose del tablero en el frame actual
        res = be.get_board_transform(frame, drawing_frame=drawing_frame)

        if res is not None:
            board_T, _ = res       # board_T: pose del tablero respecto a la cámara
            plotter.update(board_T)  # Actualizar visualización 3D

        # Mostrar el frame con las detecciones y el texto de pose
        cv2.imshow("Camera", drawing_frame)
        cv2.setWindowProperty("Camera", cv2.WND_PROP_TOPMOST, 1)  # Mantener la ventana al frente
