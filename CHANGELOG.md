# Changelog

## v0.15.0

- **Arquitectura modular**: refactorización completa del monolito original (`audiofutbol.py`) en capas separadas (`domain/`, `engine/`, `data/`, `ui/`, `scenes/`) usando puertos hexagonales (`MatchIO`), estrategia/cadena de responsabilidad para TTS (`SpeechService`), subsistemas de partido y máquina de escenas.
- **Sin dependencia de consola**: eliminado todo uso de `print()` en el flujo del juego; nueva ventana Pygame accesible de alto contraste con HUD en vivo y soporte nativo para `pythonw` (`jugar.pyw`) y compilación `--windowed`.
- **TTS robusto con NVDA DLL + respaldo SAPI5**: detección dinámica de `nvdaControllerClient64.dll` / `32.dll`, soporte opcional de línea Braille, respaldo automático asíncrono con Windows SAPI5, atajo `Ctrl` para silenciar y `F2` para repetir el último mensaje, y diagnóstico de voz dentro de Opciones.
- **Audio normalizado y enriquecido**: normalización EBU R128 de todos los efectos, ambiente y temas musicales (convertidos a `.ogg` optimizado) para evitar sonidos aturdidores, más 17 nuevos efectos normalizados (silbatos de falta, penal, descanso y final triple; cabezazo, barrera, chilena, tarjetas amarilla/roja, avisos del VAR, tablero de cambios y abucheo real).
- **Guardado seguro en JSON**: migración de `pickle` a archivos JSON versionados en `saves/`.
- **Suite de pruebas automatizadas**: pruebas unitarias y de integración con `pytest` para dominio, competiciones, TTS, audio, guardado y UI.

## v0.14 beta (base original)

- Menú principal simplificado, tiros libres con distancia al área, aforo del estadio, conducción del balón (`B`), chilenas para cualquier jugador de campo, pase largo al delantero solo frente al portero y globales de llave.
- Competiciones previas (v0.2–v0.13): Mundial de 48 selecciones, Liga de Naciones, Liga de Campeones 2026-27, Copa Libertadores (con o sin clubes mexicanos), Copa Sudamericana, Copa Interamericana, Liga MX Apertura 2026, VAR, hacer tiempo, resumen del primer tiempo, cambios en el descanso, fair play y base de datos CSV.
