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
- **7 competiciones + amistosos**:
  - Mundial (48 selecciones, 12 grupos y 8 mejores terceros)
  - Liga de Naciones (48 selecciones, calendario local/visitante optimizado geográficamente)
  - Liga de Campeones de la UEFA 2026-27 (36 clubes, fase de liga, play-off y eliminatorias)
  - Copa Libertadores 2026 (edición sudamericana o con 4 clubes mexicanos)
  - Copa Sudamericana 2026
  - Copa Interamericana 2026 (64 clubes en 16 grupos)
  - Liga MX Apertura 2026 (18 clubes y liguilla de 8 equipos)
  - Partido amistoso o eliminatoria rápida
- **Partidas guardadas seguras en JSON**: guardadas en `saves/` de forma portable y resistente a actualizaciones.

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

## Instalación y ejecución (entorno Conda `fútbol`)

### 1. Crear o activar el entorno Conda

Si ya tienes el entorno creado:

```powershell
conda activate fútbol
```

Si clonas el repositorio desde cero:

```powershell
conda env create -f environment.yml
conda activate fútbol
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
- `Flecha izquierda / derecha`: cambiar valores en Opciones
- `Inicio / Fin / RePág / AvPág`: saltar al inicio, final o de 10 en 10
- `Letras (A-Z)`: saltar por inicial en listas largas (selecciones, clubes)
- `Enter`: confirmar
- `Escape`: volver o pausar
- `Control`: silenciar voz al instante
- `F2`: repetir el último mensaje

### Durante un partido
- `F`: pase filtrado | `R`: regate | `D`: disparo | `B`: conducir el balón
- `M`: marcador (y global en vueltas) | `T`: minuto | `P`: posesión y zona
- `G`: goles | `A`: tarjetas | `E`: tiros y tiros a puerta | `S`: estadísticas completas
- `C`: cambios de jugadores | `L`: alineación en campo
- `H`: hacer tiempo | `V`: historial del VAR | `F1`: ayuda de atajos

## Pruebas automatizadas

```powershell
conda activate fútbol
pytest -v
```

## Compilar ejecutable portable sin consola (`.exe`)

```powershell
conda activate fútbol
python scripts/build_exe.py
```

Genera `AudioFutbol_Portable/AudioFutbol.exe` (sin ventana de consola) y `AudioFutbol_Portable.zip` listo para publicar como release en GitHub.

## Licencias de terceros

Las DLL del cliente controlador de NVDA incluidas en `lib/` se distribuyen bajo la licencia oficial de NVDA (`lib/LICENCIA_NVDA_controllerClient.txt`).
