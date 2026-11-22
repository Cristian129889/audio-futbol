# Changelog

## v0.15.1

- **Corrección crítica en semifinales del Mundial**: al ganar la semifinal del Mundial, el juego pasaba a la fase de *Partido por el tercer lugar* y marcaba por error al ganador como eliminado (`user_eliminated = user_team not in semi_losers`), impidiéndole jugar *La gran final*. Ahora el finalista permanece clasificado, el hub anuncia que ya está en la gran final mientras se disputa el tercer puesto y, tras simularse ese partido, juega la final normalmente.
- **Corrección de jugador sustituido que seguía participando en la jugada (`C`)**: cuando se realizaba un cambio con la tecla `C` durante una jugada de ataque o a balón parado, la variable local del portador del balón (`carrier`) o del cobrador (`taker`) en el turno activo conservaba la referencia al jugador que acababa de salir, provocando que el jugador saliente y el entrante aparecieran jugando juntos en esa acción. Ahora `apply_substitution()` transfiere de inmediato la posesión al jugador entrante y todos los turnos ofensivos y jugadas a balón parado re-sincronizan el portador/cobrador activo tras abrir el menú.
- **Compatibilidad y migración automática de partidas guardadas `.sav` (v0.2–v0.14)**: `SaveManager` ahora detecta y carga automáticamente archivos clásicos `partida_<torneo>.sav` (pickle de versiones `0.2` a `0.14`) tanto en la carpeta raíz del juego como en `saves/`, utilizando un deserializador seguro restringido a las clases del dominio original (`Mundial`, `LigaNaciones`, `LigaDeCampeones`, `CopaLibertadores`, `CopaSudamericana`, `CopaInteramericana`, `LigaMX`, `Equipo`, `Jugador`) y migrando la partida automáticamente al nuevo formato JSON en `saves/`.
- **Historial global de voz navegable**: atajos `,` (mensaje anterior), `.` (siguiente), `Shift + ,` (primero) y `Shift + .` (último) disponibles en cualquier pantalla sin contaminar el historial al navegar.
- **Ajuste de audio en goles y ambiente**: evitada la triple superposición de efectos de tribuna en goles y enrutados los efectos de hinchada (`ovacion`, `abucheo`, `casi`) a través del control de volumen de ambiente de tribuna.

## v0.15.0

- **Arquitectura modular**: refactorización completa del monolito original (`audiofutbol.py`) en capas separadas (`domain/`, `engine/`, `data/`, `ui/`, `scenes/`) usando puertos hexagonales (`MatchIO`), estrategia/cadena de responsabilidad para TTS (`SpeechService`), subsistemas de partido y máquina de escenas.
- **Motor de partido mejorado**:
  - Campo espacial 2D (zona + carril izquierdo/centro/derecho) con paneo estéreo real y seguimiento del jugador portador del balón.
  - Defensa en bloque rediseñada con 6 desenlaces dinámicos (intercepción entre líneas, empuje hacia atrás de zona, encierro a la banda, disparo desesperado bloqueado, acumulación de solidez defensiva y quiebre rival).
  - Nuevas acciones: *Cambio de frente* (`Q`, otorga ventaja de espacio), *Trampa del fuera de juego* (`7`), atajos rápidos defensivos `1-7`, postura táctica en vivo (`X`: Equilibrado / Ofensivo / Defensivo) y consulta de contexto (`N`).
  - Cambios tácticos inteligentes: no permite meter un portero suplente en lugar de un jugador de campo, y al entrar un jugador de otra posición te permite elegir entre colocarlo en su posición natural (cambiando el dibujo táctico real y los ratings de ataque/defensa) o cubrir el puesto del que sale (manteniendo el dibujo con penalización fuera de puesto).
  - Física, estadísticas e IA rival proactiva: la calidad individual y el rol del tirador/portero tienen impacto real en la definición (curva no lineal xG), y el rival intenta gambetas, pases filtrados, paredes, centros y disparos de media distancia en lugar de dar pases interminables en el área; clima dinámico, *momentum*, choques en mediocampo y narrador anti-repetición.
- **Sin dependencia de consola**: eliminado todo uso de `print()` en el flujo del juego; nueva ventana Pygame accesible de alto contraste con HUD en vivo y soporte nativo para `pythonw` (`jugar.pyw`) y compilación `--windowed`.
- **TTS robusto con NVDA DLL + respaldo SAPI5**: detección dinámica de `nvdaControllerClient64.dll` / `32.dll`, soporte opcional de línea Braille, respaldo automático asíncrono con Windows SAPI5, atajo `Ctrl` para silenciar y `F2` para repetir el último mensaje, y diagnóstico de voz dentro de Opciones.
- **Audio normalizado con curva perceptual suave**: normalización EBU R128 + tope de pico `<= -3.0 dBFS`, curva logarítmica/exponencial de volumen con *Volumen maestro* y ajuste fino de 5% en 5% para que el nivel bajo sea realmente suave bajo NVDA, más 17 efectos nuevos.
- **Entorno Conda ASCII (`futbol`)**: nombre sin acentos para compatibilidad total con `conda activate futbol` en Windows.
- **Guardado seguro en JSON**: migración de `pickle` a archivos JSON versionados en `saves/`.
- **Suite de pruebas automatizadas**: pruebas unitarias y de integración con `pytest` para dominio, competiciones, TTS, audio, guardado y UI.

## v0.14 beta (base original)

- Menú principal simplificado, tiros libres con distancia al área, aforo del estadio, conducción del balón (`B`), chilenas para cualquier jugador de campo, pase largo al delantero solo frente al portero y globales de llave.
- Competiciones previas (v0.2–v0.13): Mundial de 48 selecciones, Liga de Naciones, Liga de Campeones 2026-27, Copa Libertadores (con o sin clubes mexicanos), Copa Sudamericana, Copa Interamericana, Liga MX Apertura 2026, VAR, hacer tiempo, resumen del primer tiempo, cambios en el descanso, fair play y base de datos CSV.
