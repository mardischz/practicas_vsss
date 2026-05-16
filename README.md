# practicas_vsss
## PASO 1:
Correr 00_check_camera.py
Confirmar que hay imagen.

## PASO 2:
Correr 01_detect_ball_and_arucos.py
Hacer doble click en la pelota para calibrarla.
Confirmar que se detecta su ArUco.

## PASO 3:
Correr 02_read_board_coordinates.py
Medir los límites de la cancha y de su zona.

## PASO 4:
Editar team_config_student.py
Poner:
- ID de ArUco
- rol
- nombre BLE
- límites de zona

## PASO 5:
Correr 06_test_player_target.py
Validar que el target tenga sentido.

## PASO 6:
Correr 07_ble_manual_test.py
Validar que el carrito se mueve.

## PASO 7:
Correr 08_vsss_main.py con SEND_TO_ROBOT = False.
Revisar comandos.

## PASO 8:
Cambiar SEND_TO_ROBOT = True.
Probar movimiento real.
