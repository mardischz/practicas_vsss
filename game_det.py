from dataclasses import dataclass
from typing import List
import numpy as np
from obj_det import ArucoDetector, BallDetector
from board_est import BoardEstimator
import cv2
import board_config
from board_config import global_board_config
from cam_config import global_cam
import math
import time

def extract_euler_zyx(R: np.ndarray) -> tuple[float, float, float]:
    """
    Extrae los ángulos de Euler (alfa, beta, gamma) a partir de una matriz de
    rotación 3x3, asumiendo la convención ZYX intrínseca (yaw-pitch-roll).

    En esta convención la rotación total es:
        R = Rx(α) · Ry(β) · Rz(γ)
    donde:
        α (alpha) = rotación alrededor del eje X (roll)
        β (beta)  = rotación alrededor del eje Y (pitch)
        γ (gamma) = rotación alrededor del eje Z (yaw)

    El ángulo gamma (yaw, rotación en Z) es el que se usa para transformar
    el ángulo del marcador ArUco del espacio de imagen al espacio del tablero.

    Nota sobre Gimbal Lock: cuando beta = ±90° (cos(β) ≈ 0) los ejes X y Z
    se alinean y no se pueden distinguir. En ese caso se fija gamma = 0 y
    toda la rotación se asigna a alpha.

    Args:
        R: Matriz de rotación 3x3 (submatriz superior izquierda de una
           matriz de transformación homogénea 4x4).

    Returns:
        (alpha, beta, gamma) en radianes.

    Raises:
        ValueError: Si R no tiene exactamente forma (3, 3).
    """
    if R.shape != (3, 3):
        raise ValueError(f"extract_euler_zyx: expected a 3×3 matrix, got shape {R.shape}")

    # beta = asin(R[0,2]) porque en la descomposición ZYX R[0,2] = sin(β)
    # np.clip evita errores numéricos por valores ligeramente fuera de [-1, 1]
    beta = math.asin(np.clip(R[0, 2], -1.0, 1.0))
    cb = math.cos(beta)  # Coseno de beta; si ≈0 hay gimbal lock

    if abs(cb) > 1e-6:
        # Caso normal: extraer alpha y gamma usando atan2 para obtener el cuadrante correcto
        alpha = math.atan2(-R[1, 2] / cb, R[2, 2] / cb)  # Roll  (rotación en X)
        gamma = math.atan2(-R[0, 1] / cb, R[0, 0] / cb)  # Yaw   (rotación en Z)
    else:
        # Gimbal lock: beta = ±90°, los ejes X y Z se fusionan
        # Se asigna toda la rotación a alpha y se fija gamma = 0
        alpha = math.atan2(-R[1, 2], R[1, 1])
        gamma = 0.0

    return alpha, beta, gamma


@dataclass
class GameState:
    """
    Representa el estado completo del juego en un instante de tiempo.

    Contiene la lista de pelotas y jugadores detectados, la pose del tablero
    respecto a la cámara y metadatos de la detección. Es el objeto que
    devuelve GameDetector.detect() en cada frame.

    Campos:
        balls:           Lista de BallState con la posición de cada pelota
                         en coordenadas del tablero (metros).
        players:         Lista de PlayerState con posición y orientación de
                         cada jugador en coordenadas del tablero.
        board_transform: Matriz 4x4 de transformación del tablero respecto
                         a la cámara (resultado de solvePnP).
        pnp_result:      Objeto PnpResult con los puntos detectados y la
                         homografía usada para proyección.
        detector:        Referencia al GameDetector que creó este estado.
        timestamp:       Marca de tiempo Unix del momento de la detección.
    """
    balls: List = None             # Pelotas detectadas en el frame
    players: List = None           # Jugadores detectados en el frame
    board_transform: np.ndarray = None  # Pose del tablero (matriz 4x4)
    pnp_result: tuple = None       # Resultado del PnP (esquinas y homografía)
    detector: 'GameDetector' = None  # Referencia al detector padre
    timestamp: float = None        # Momento de la detección (Unix timestamp)

    def __post_init__(self):
        """Inicializa las listas a vacías si no se proporcionaron."""
        if self.balls is None:
            self.balls = []
        if self.players is None:
            self.players = []

    def get_player(self, aruco_id):
        """
        Busca y devuelve el jugador con el ID ArUco especificado.

        Args:
            aruco_id: ID numérico del marcador ArUco del jugador a buscar.

        Returns:
            PlayerState del jugador encontrado, o None si no existe.
        """
        return next((p for p in self.players if p.id == aruco_id), None)


@dataclass
class BallState:
    """
    Estado de una pelota detectada en el tablero.

    Las coordenadas (x, y) están en el sistema de coordenadas del tablero
    (centrado en el tablero, en metros), no en píxeles de imagen.

    Campos:
        x:         Posición horizontal en el tablero (metros).
        y:         Posición vertical en el tablero (metros).
        detection: Objeto DetectedObject original con el contorno, centroide
                   en píxeles y demás metadatos de la detección de imagen.
    """
    x: float                       # Posición X en el tablero (metros)
    y: float                       # Posición Y en el tablero (metros)
    detection: 'DetectedObject' = None  # Detección original en imagen


@dataclass
class PlayerState:
    """
    Estado de un jugador (robot) detectado en el tablero mediante marcador ArUco.

    Las coordenadas (x, y) y el ángulo están en el sistema de coordenadas del
    tablero (metros y radianes), ya transformados desde el espacio de imagen.

    Campos:
        id:        ID numérico del marcador ArUco del jugador.
        x:         Posición horizontal en el tablero (metros).
        y:         Posición vertical en el tablero (metros).
        angle:     Orientación del jugador en el tablero (radianes).
                   0 = apuntando en dirección +Y del tablero.
        detection: Objeto DetectedAruco original con esquinas, ángulo de
                   imagen y demás metadatos de la detección.
    """
    id: int                        # ID del marcador ArUco
    x: float                       # Posición X en el tablero (metros)
    y: float                       # Posición Y en el tablero (metros)
    angle: float                   # Orientación en el tablero (radianes)
    detection: 'DetectedAruco' = None  # Detección original en imagen

class GameDetector:
    """
    Orquestador principal de detección del juego.

    Combina tres detectores para producir un GameState completo en cada frame:
      1. BoardEstimator: detecta la pose del tablero (posición y orientación
         de la cámara respecto al tablero) usando marcadores ChArUco/ArUco.
      2. BallDetector: detecta la pelota por color HSV y la proyecta al
         espacio del tablero con corrección de paralaje por altura.
      3. ArucoDetector: detecta los marcadores ArUco de los robots, filtra
         los marcadores que son parte del tablero, y transforma la posición
         y ángulo al sistema de coordenadas del tablero.

    El flujo de detect() es:
        frame → BoardEstimator → BallDetector + ArucoDetector → GameState
    """

    def __init__(self, board_estimator, ball_detector=None, ball_height=0.0,
                 aruco_detector=None, player_height=0.0):
        """
        Inicializa el detector de juego con sus componentes.

        Args:
            board_estimator: Instancia de BoardEstimator configurada con la
                             cámara y el tablero. Es el componente central
                             sin el cual no se puede hacer ninguna detección.
            ball_detector:   Detector de pelota (ObjectDetector). Si es None
                             se crea un BallDetector con configuración por defecto.
            ball_height:     Altura de la pelota sobre el tablero en metros.
                             Se usa para corregir el error de paralaje al
                             proyectar el centroide de imagen al tablero.
                             Ejemplo: 0.02 m = pelota de 4 cm de diámetro.
            aruco_detector:  Detector de marcadores ArUco para los jugadores
                             (ArucoDetector). Si es None no se detectan jugadores.
            player_height:   Altura del marcador ArUco del jugador sobre el
                             tablero en metros. Corrige el paralaje igual que
                             ball_height. Ejemplo: 0.04 m en setup pequeño.
        """
        self.board_estimator = board_estimator  # Estima la pose del tablero

        self.ball_detector = ball_detector or BallDetector()  # Detector de pelota
        self.ball_height = ball_height    # Altura de la pelota sobre el tablero (m)

        self.player_detector = aruco_detector  # Detector de jugadores (ArUco)
        self.player_height = player_height     # Altura del marcador del jugador (m)
    
    @staticmethod
    def _same_marker_size(dict1, dict2):
        """
        Verifica si dos diccionarios ArUco tienen el mismo tamaño de marcador.

        Esto se usa para decidir si un marcador detectado en el frame pertenece
        al tablero (y debe ser ignorado como jugador) o es un jugador real.
        Solo se comparan marcadores del mismo tipo (ej. ambos 4x4), porque un
        tablero 4x4 y un jugador 5x5 no pueden confundirse aunque compartan ID.

        Args:
            dict1: Primer cv2.aruco.Dictionary.
            dict2: Segundo cv2.aruco.Dictionary.

        Returns:
            True si ambos diccionarios tienen el mismo markerSize (ej. 4 para 4x4).
            False si alguno es None o tienen tamaños distintos.
        """
        if dict1 is None or dict2 is None:
            return False
        return dict1.markerSize == dict2.markerSize  # markerSize = bits por lado (ej. 4 para DICT_4X4)

    def _localize(self, frame, centroid, pnp_result, height):
        """
        Proyecta el centroide de un objeto desde coordenadas de imagen (píxeles)
        al espacio 2D del tablero (metros) con corrección de paralaje.

        Es un wrapper delgado sobre BoardEstimator.project_point_to_board() que
        maneja el caso de centroide None y empaqueta el resultado en una tupla
        (x, y, z) donde z = height.

        El error de paralaje ocurre porque la homografía asume que todos los
        objetos están sobre el plano del tablero (Z=0), pero si el objeto está
        elevado (pelota o marcador del robot), su proyección en Z=0 está
        desplazada respecto a su posición real. El parámetro height corrige eso.

        Args:
            frame:      Frame original para obtener las dimensiones de imagen.
            centroid:   Tupla (x, y) con el centroide en píxeles, o None.
            pnp_result: Objeto PnpResult con la homografía del tablero.
            height:     Altura del objeto sobre el tablero en metros (z real).

        Returns:
            Tupla (x, y, height) en metros en el espacio del tablero,
            o None si el centroide es None.
        """
        if centroid is None:
            return None

        # Proyectar el punto de imagen al plano del tablero con corrección de altura
        x, y = self.board_estimator.project_point_to_board(
            pnp_result, centroid, frame.shape, z=height
        )

        return (x, y, height)  # (X_tablero, Y_tablero, Z_real)
    
    def detect(self, frame, drawing_frame=None, include_balls=True):
        """
        Detecta todos los objetos del juego en el frame y devuelve el estado.

        Pasos internos:
          1. Estimar la pose del tablero con BoardEstimator. Si falla (tablero
             no visible), devuelve un GameState vacío.
          2. (Opcional) Detectar la pelota con BallDetector, proyectarla al
             tablero y agregarla a la lista de balls.
          3. Detectar jugadores con ArucoDetector:
             a. Ignorar marcadores del tablero (mismo tipo e ID que los del board).
             b. Proyectar el centroide al tablero con corrección de paralaje.
             c. Transformar el ángulo de imagen al espacio del tablero usando
                la rotación Z de la cámara (extraida con extract_euler_zyx).
             d. Dibujar un triángulo apuntando en la dirección del jugador.
          4. Devolver el GameState con toda la información recopilada.

        Args:
            frame:         Frame BGR de entrada (de la cámara).
            drawing_frame: Frame opcional para dibujar las detecciones.
                           Se dibujan contornos de pelota y triángulos de jugadores.
            include_balls: Si False, omite la detección de pelota (más rápido).

        Returns:
            GameState con balls, players, board_transform, pnp_result y timestamp.
            Si el tablero no se detectó, devuelve un GameState con listas vacías.
        """
        # Paso 1: Estimar la pose del tablero (imprescindible para proyectar)
        result = self.board_estimator.get_board_transform(frame)

        if result is None:
            return GameState()  # Sin tablero no hay sistema de referencia, devolver vacío

        board_T, pnp_result = result  # board_T: matriz 4x4 tablero→cámara
        timestamp = time.time()       # Registrar el momento de la detección

        balls = []    # Acumulará las pelotas detectadas
        players = []  # Acumulará los jugadores detectados

        # Paso 2: Detectar la pelota si el detector está activo
        if self.ball_detector is not None and include_balls:
            ball_detections = self.ball_detector.detect(frame)
            for ball in ball_detections:
                # Proyectar el centroide de imagen al tablero con corrección de altura
                xyz = self._localize(frame, ball.centroid, pnp_result, self.ball_height)
                if xyz is not None:
                    balls.append(BallState(
                        x=xyz[0],   # Posición X en el tablero (metros)
                        y=xyz[1],   # Posición Y en el tablero (metros)
                        detection=ball
                    ))

                    # Dibujar el contorno de la pelota en amarillo
                    if drawing_frame is not None and ball.contour is not None:
                        cv2.drawContours(drawing_frame, [ball.contour], -1, (0, 255, 255), 2)

        # Paso 3: Detectar jugadores (marcadores ArUco) si el detector está activo
        if self.player_detector is not None:
            player_detections = self.player_detector.detect(frame)
            for player in player_detections:
                # Paso 3a: Filtrar marcadores que pertenecen al tablero
                # Si el marcador tiene el mismo tipo e ID que los del board, ignorarlo
                if (self.board_estimator.config.board_marker_ids is not None and
                        player.id in self.board_estimator.config.board_marker_ids and
                        self._same_marker_size(player.dict, self.board_estimator.config.dictionary)):
                    continue  # Este marcador es parte del tablero, no un jugador

                # Paso 3b: Proyectar el centroide al espacio del tablero
                xyz = self._localize(frame, player.centroid, pnp_result, self.player_height)
                if xyz is not None and player.angle is not None:

                    # Paso 3c: Transformar el ángulo del espacio de imagen al tablero
                    # La cámara puede estar rotada respecto al tablero, por lo que
                    # el ángulo medido en imagen no coincide con el del tablero.
                    # Se corrige restando la rotación Z (yaw) de la cámara.

                    # Inversa de board_T da la pose de la cámara en coords del tablero
                    cam_T_in_board = np.linalg.inv(board_T)
                    cam_R = cam_T_in_board[:3, :3]  # Submatriz de rotación (3x3)

                    # Extraer el ángulo de yaw (gamma) que representa la rotación
                    # de la cámara alrededor del eje Z del tablero
                    alpha, beta, gamma = extract_euler_zyx(cam_R)

                    # Transformar el ángulo del jugador del espacio de imagen al tablero:
                    # Se resta gamma (yaw de cámara) y se le suma π para alinear convenciones.
                    # El módulo 2π mantiene el resultado en [0, 2π).
                    angle_board = (player.angle - gamma + np.pi) % (2 * np.pi)

                    players.append(PlayerState(
                        id=player.id,
                        x=xyz[0],          # Posición X en el tablero (metros)
                        y=xyz[1],          # Posición Y en el tablero (metros)
                        angle=angle_board, # Orientación en el tablero (radianes)
                        detection=player
                    ))

                    # Paso 3d: Dibujar un triángulo sobre el jugador en el frame de dibujo
                    if drawing_frame is not None and player.centroid is not None:
                        cx, cy = int(player.centroid[0]), int(player.centroid[1])  # Centro en píxeles

                        # Parámetros del triángulo en píxeles
                        length = 20  # Largo total del triángulo (px)
                        width  = 12  # Ancho de la base del triángulo (px)

                        # Vector unitario en la dirección del jugador (en imagen)
                        # Se niega el ángulo porque en imagen Y crece hacia abajo
                        cos_a = np.cos(-player.angle)
                        sin_a = np.sin(-player.angle)

                        # Centro de la base: 1/3 del largo hacia atrás del centroide
                        base_cx = cx - (length / 3) * cos_a
                        base_cy = cy - (length / 3) * sin_a
                        # Punta: 2/3 del largo hacia adelante del centroide
                        tip_x = cx + (2 * length / 3) * cos_a
                        tip_y = cy + (2 * length / 3) * sin_a

                        # Esquinas de la base: perpendiculares a la dirección
                        base_angle = -player.angle + np.pi / 2  # 90° respecto a la dirección
                        base1_x = base_cx + (width / 2) * np.cos(base_angle)
                        base1_y = base_cy + (width / 2) * np.sin(base_angle)
                        base2_x = base_cx - (width / 2) * np.cos(base_angle)
                        base2_y = base_cy - (width / 2) * np.sin(base_angle)

                        # Dibujar el triángulo relleno en color magenta
                        pts = np.array([[tip_x, tip_y], [base1_x, base1_y], [base2_x, base2_y]], np.int32)
                        pts = pts.reshape((-1, 1, 2))
                        cv2.fillPoly(drawing_frame, [pts], (255, 100, 255))

        # Devolver el estado completo del juego
        return GameState(
            balls=balls,
            players=players,
            board_transform=board_T,   # Pose del tablero (matriz 4x4)
            pnp_result=pnp_result,     # Resultado del PnP con puntos y homografía
            detector=self,             # Referencia a este detector
            timestamp=timestamp        # Momento de la detección
        )


# ---------------------------------------------------------------------------
# Configuración global del detector (se instancia al importar este módulo)
# ---------------------------------------------------------------------------

# Detectar si se está usando el setup pequeño (tablero tamaño carta) o el grande.
# Esto afecta la altura del jugador: en el setup pequeño los robots son más bajos.
is_small_setup = global_board_config == board_config.board_config_letter

# Instancia global del GameDetector lista para usar desde otros módulos.
# Usa la cámara y el tablero configurados globalmente en cam_config y board_config.
game_detector = GameDetector(
    board_estimator=BoardEstimator(global_board_config, K=global_cam.K, D=global_cam.D, rotate_180=True),
    ball_detector=BallDetector(),
    ball_height=0.02,   # Pelota a 2 cm sobre el tablero
    aruco_detector=ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)),
    player_height=0.04 if is_small_setup else 0.1,  # 4 cm en setup pequeño, 10 cm en grande
)

if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Script de prueba: detecta el estado del juego en tiempo real y lo
    # muestra sobre el feed de la cámara. Presionar ESC para salir.
    # Muestra posición de la pelota y jugadores en metros y grados.
    # -----------------------------------------------------------------------
    from board_config import board_config_letter
    from cam_config import webcam, droidcam, sim_cam

    active_cam = droidcam  # Cámara IP (DroidCam)

    # Crear un GameDetector específico para la prueba (setup pequeño)
    game_detector = GameDetector(
        board_estimator=BoardEstimator(board_config_letter, K=active_cam.K, D=active_cam.D, rotate_180=True),
        ball_detector=BallDetector(),
        ball_height=0.02,   # Pelota a 2 cm sobre el tablero
        aruco_detector=ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)),
        player_height=0.04, # Marcador del robot a 4 cm sobre el tablero
    )

    try:
        while True:
            if cv2.waitKey(1) & 0xFF == 27:  # ESC para salir
                break

            # Capturar frame
            frame = active_cam.get_frame()
            if frame is None:
                continue  # Frame inválido, intentar de nuevo

            drawing_frame = frame.copy()  # Copia para anotar sin modificar el original

            # Detectar el estado completo del juego
            game_state = game_detector.detect(frame, drawing_frame)

            # Mostrar posición de cada pelota detectada (en metros)
            for i, ball in enumerate(game_state.balls):
                cv2.putText(drawing_frame, f"Ball: ({ball.x:.3f}, {ball.y:.3f})m",
                            (10, 30 + i * 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

            # Mostrar posición y ángulo de cada jugador detectado
            for i, player in enumerate(game_state.players):
                angle_deg = np.degrees(player.angle)  # Convertir a grados para display
                cv2.putText(drawing_frame, f"Player {player.id}: ({player.x:.3f}, {player.y:.3f})m, {angle_deg:.1f}deg",
                            (10, 60 + i * 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 100, 255), 2)

            # Mostrar el frame anotado y mantener la ventana al frente
            cv2.imshow("Game Detection", drawing_frame)
            cv2.setWindowProperty("Game Detection", cv2.WND_PROP_TOPMOST, 1)
    finally:
        cv2.destroyAllWindows()  # Cerrar todas las ventanas al salir

