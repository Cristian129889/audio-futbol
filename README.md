# AudioFútbol

Audiojuego de fútbol accesible para personas ciegas y con baja visión, con salida directa por **NVDA** (mediante `nvdaControllerClient` DLL), respaldo automático por **Windows SAPI5**, ventana accesible sin consola y audio normalizado.

## Características principales

- **Sin dependencia de consola**: se ejecuta con `pythonw` o como ejecutable `--windowed` sin abrir ventanas negras de terminal, mostrando una ventana accesible de alto contraste con el marcador, el último relato hablado y el menú activo.
- **Voz TTS prioritaria con NVDA DLL**:
  - Motor principal: `lib/nvdaControllerClient64.dll` / `lib/nvdaControllerClient32.dll` (habla y soporte opcional de línea Braille).
  - Respaldo automático no bloqueante: **Windows SAPI5** (asíncrono) si NVDA no está abierto.
  - Atajos universales de accesibilidad: `Ctrl` silencia la voz al instante y `F2` repite el último mensaje.
- **Audio normalizado y enriquecido**:
  - Todos los efectos (`sonidos/`), música (`musica/`) y temas (`temas/`) están normalizados bajo estándar **EBU R128** (`-16.5` a `-22.5 LUFS`) con limitación suave para evitar picos aturdidores y no tapar la voz de NVDA.
  - Incluye variaciones de silbatos (inicio, falta, penal, descanso, final triple), balón (pase, tiro, córner, cabezazo, barrera, barrida, regate, chilena), tarjetas amarilla y roja, avisos del VAR (revisión, confirmado, anulado), tablero de cambios/tiempo añadido y ambiente de tribuna.
- **10 competiciones + amistosos y personalización estilo PES/FIFA**:
  - Mundial (oficial de 48 selecciones o personalizado de 8, 16, 32 o 48 selecciones)
  - Liga de Naciones (oficial de 48 selecciones o personalizado de 8, 16, 32 o 48 selecciones)
  - Liga de Campeones de la UEFA 2026-27 (36 clubes, fase de liga, play-off y eliminatorias)
  - Copa Libertadores 2026 (edición sudamericana o con 4 clubes mexicanos)
  - Copa Sudamericana 2026
  - Copa Interamericana 2026 (64 clubes en 16 grupos)
  - Liga MX Apertura 2026 (18 clubes y liguilla de 8 equipos)
  - Premier League 2026-27 (20 clubes, 38 jornadas a ida y vuelta sin playoffs)
  - Liga 1 de Perú 2026 (18 clubes, Apertura + Clausura de 17 fechas, tabla acumulada y playoffs por el título)
  - Copa de clubes personalizada (8, 16, 32 o 64 clubes elegidos libremente de cualquier liga)
  - Partido amistoso o eliminatoria rápida (incluyendo selecciones adicionales en `datos/otras_selecciones/`)
- **Personalizar torneos**: al iniciar cualquier competición de clubes puedes elegir *Personalizar participantes* para reemplazar cualquier club por otro del pool global de 183 clubes con filtro por país conservando el calendario oficial. Para agregar un club propio basta con escribir al menos 11 jugadores en cualquier CSV de `datos/clubes/`. Consulta [COLABORAR.md](COLABORAR.md) para ver la guía completa de creación de plantillas y competiciones.
- **Partidas guardadas seguras en JSON + compatibilidad con `.sav`**: guardadas en `saves/` de forma portable y resistente a actualizaciones, con detección y migración automática de partidas `.sav` de versiones anteriores (v0.2–v0.14) ubicadas en la carpeta raíz o en `saves/`.

## Arquitectura del proyecto

El proyecto separa dominio puro, motor de infraestructura y escenas de presentación:

```text
src/audiofutbol/
├── app.py                     # Raíz de composición y ciclo de vida sin consola
├── domain/                    # Lógica pura de fútbol (sin dependencias de Pygame/OS)
│   ├── models.py              # Player, Team, MatchResult
│   ├── match/                 # MatchEngine, MatchState, SetPieceResolver, VarSystem, SubstitutionManager, MatchIO (puerto hexagonal)
│   └── competitions/          # WorldCup, NationsLeague, ChampionsLeague, GroupCup, LigaMX, scheduling, attendance
├── engine/                    # Infraestructura
│   ├── tts/                   # SpeechService (Chain of Responsibility): NvdaDllBackend -> Sapi5Backend
│   ├── audio.py               # AudioEngine con paneo estéreo, ambiente y música
│   ├── window.py              # Ventana Pygame accesible (HUD alto contraste)
│   ├── config.py              # Persistencia de config.json
│   └── paths.py               # Resolución de rutas en código fuente y .exe portable
├── data/                      # Carga de CSV, JSON de competiciones y SaveManager JSON
├── ui/                        # Widgets accesibles (menú, lector, confirmación, recuento) y reglas
└── scenes/                    # Máquina de escenas (menú principal, hubs, opciones, partido)
```

## Instalación y ejecución (entorno Conda `futbol`)

### 1. Crear o activar el entorno Conda

Si ya tienes el entorno creado:

```powershell
conda activate futbol
```

Si clonas el repositorio desde cero:

```powershell
conda env create -f environment.yml
conda activate futbol
```

Instalar el paquete en modo editable (si creaste el entorno manualmente):

```powershell
pip install -e ".[dev]"
```

### 2. Jugar sin consola (recomendado)

```powershell
pythonw jugar.pyw
```

O haciendo doble clic sobre `jugar.pyw` (con Python asociado a `.pyw`), o desde el módulo:

```powershell
pythonw -m audiofutbol
```

También puedes iniciar con `python -m audiofutbol` si deseas lanzarlo desde una terminal.

## Controles principales

### Menús y lectores
- `Flecha arriba / abajo`: moverse por las opciones o líneas
- `Flecha izquierda / derecha`: cambiar valores en Opciones (de 5% en 5% con curva perceptual suave)
- `Inicio / Fin / RePág / AvPág`: saltar al inicio, final o de 10 en 10
- `Letras (A-Z)`: saltar por inicial en listas largas (selecciones, clubes)
- `Enter`: confirmar
- `Escape`: volver o pausar
- `Control`: silenciar voz al instante
- `F2`: repetir el último mensaje
- `,` / `.`: leer mensaje anterior / siguiente del historial de voz (en cualquier pantalla)
- `Shift + ,` / `Shift + .`: saltar al primer / último mensaje del historial de voz

### Durante un partido
- **Ataque**: `Q` cambio de frente | `F` pase filtrado | `R` regate | `D` disparo | `B` conducir el balón
- **Defensa**: `1` presionar arriba | `2` defender en bloque | `3` salir a marcar | `4` preparar contraataque | `5` barrida | `6` falta táctica | `7` trampa del fuera de juego
- **Táctica y contexto**: `X` cambiar postura táctica en vivo (Equilibrado / Ofensivo / Defensivo) | `N` clima, táctica, carril e impulso del partido
- **Información**: `M` marcador (y global en vueltas) | `T` minuto | `P` posesión, portador y carril | `G` goles | `A` tarjetas | `E` tiros y tiros a puerta | `S` estadísticas completas (con atajadas, intercepciones y bloqueos)
- **Gestión**: `C` cambios de jugadores | `L` alineación en campo | `H` hacer tiempo | `V` historial del VAR | `F1` ayuda de atajos

## Pruebas automatizadas

```powershell
conda activate futbol
pytest -v
```

## Compilar ejecutable portable sin consola (`.exe`)

Forma automática (prepara entorno Conda `futbol` o `.venv`, instala dependencias, corre pruebas y compila):

```powershell
.\scripts\setup_and_build.ps1
```

Opciones útiles:
- `.\scripts\setup_and_build.ps1 -UseVenv` usa un entorno virtual local `.venv` en lugar de Conda.
- `.\scripts\setup_and_build.ps1 -CleanAfter` borra `build/`, `dist/` y `.spec` al terminar.
- `.\scripts\setup_and_build.ps1 -SkipTests` omite `pytest` antes de compilar.

También puedes compilar manualmente desde el entorno activo:

```powershell
conda activate futbol
python scripts/build_exe.py
```

Genera `AudioFutbol_Portable/AudioFutbol.exe` (sin ventana de consola) y `AudioFutbol_Portable.zip` listo para publicar como release en GitHub.

## Limpiar archivos de compilación

Para borrar `build/`, `dist/`, `AudioFutbol_Portable/`, `AudioFutbol_Portable.zip`, `.spec` y cachés sin tocar tus partidas guardadas ni tu configuración:

```powershell
.\scripts\clean.ps1
```

## Licencias de terceros

Las DLL del cliente controlador de NVDA incluidas en `lib/` se distribuyen bajo la licencia oficial de NVDA (`lib/LICENCIA_NVDA_controllerClient.txt`).
