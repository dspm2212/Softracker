# Especificación: Agente de Monitoreo de Aplicaciones (Windows)

## Contexto del proyecto

Soy estudiante/desarrollador en una universidad. Estoy construyendo un sistema para monitorear qué aplicaciones se usan en los equipos de las salas de cómputo (500 equipos, 20 salas).

Este documento especifica **únicamente el agente** que corre en cada equipo Windows. En una fase posterior construiré la API REST en un servidor Linux que recibe los datos. **Por ahora, mockea el envío al servidor** con un endpoint local o una función que simule respuestas.

El agente captura periódicamente las aplicaciones en uso por el usuario logueado, las acumula localmente durante el día, y a las 21:45 envía un archivo Parquet consolidado al servidor.

## Decisiones de arquitectura

- **Lenguaje**: Python 3.13+ (Windows).
- **Detección de procesos**: `psutil` + `pywin32` (win32gui, win32process).
- **Formato de archivos locales del día**: JSONL (una muestra por línea), append-only.
- **Formato de envío final al servidor**: Parquet (con `pyarrow`).
- **Transporte**: HTTP POST `multipart/form-data` a una API REST.
- **Autenticación**: header `X-Auth-Token` con un token compartido por sala.
- **Programación**: Tareas Programadas de Windows (Task Scheduler).
- **Cuenta de ejecución**: usuario logueado (`estudiante`), no SYSTEM.
- **Frecuencia de captura**: cada 10 minutos.
- **Hora de envío diario**: 21:45 hora local.

## Estructura de carpetas del proyecto en desarrollo

El repositorio raíz (`Softracker/`) aloja múltiples sub-proyectos. El agente vive en su propia subcarpeta para dejar espacio a desarrollos futuros (servidor, app de análisis, etc.):

```
Softracker/                     # Raíz del repositorio
├── CLAUDE.md
├── ESPEC_AGENTE.md
├── monitoreo-agente/           # Sub-proyecto: agente Windows
│   ├── agente/
│   │   ├── __init__.py
│   │   ├── captura.py          # Detección de apps con ventana
│   │   ├── almacenamiento.py   # Manejo de JSONL diario y Parquet
│   │   ├── envio.py            # Lógica de envío al servidor
│   │   ├── config.py           # Carga y validación de config.json
│   │   └── logger.py           # Setup de logging rotativo
│   ├── scripts/
│   │   ├── agente_captura.py   # Entry point: ejecuta una captura
│   │   ├── agente_envio.py     # Entry point: consolida y envía
│   │   └── agente_retry.py     # Entry point: consolida huérfanos + reintenta
│   ├── tests/
│   │   ├── test_captura.py
│   │   ├── test_almacenamiento.py
│   │   └── test_envio.py
│   ├── deploy/
│   │   ├── instalar.ps1        # Script PowerShell de instalación
│   │   ├── desinstalar.ps1
│   │   └── tareas/
│   │       ├── captura.xml     # Tarea cada 10 min (delay 5 min al login)
│   │       ├── envio.xml       # Tarea diaria 21:45
│   │       └── arranque.xml    # Tarea al iniciar sesión (solo retry)
│   ├── config.example.json
│   ├── requirements.txt
│   └── README.md
└── (futuros sub-proyectos: monitoreo-servidor/, monitoreo-app/, ...)
```

## Especificación funcional

### Módulo: `captura.py`

Función principal: `capturar_apps_en_uso() -> list[dict]`

Detecta procesos del usuario actual que tienen ventana asociada (visible o minimizada). Filtra procesos del sistema y de otros usuarios.

Para cada proceso detectado, devuelve un dict con:
- `nombre_proceso`: nombre sin extensión (ej: "Code")
- `nombre_ejecutable`: con extensión (ej: "Code.exe")
- `ruta_ejecutable`: ruta completa o None si no accesible
- `titulo_ventana`: título de la ventana principal
- `pid`: integer
- `memoria_mb`: float, redondeado a 2 decimales

**Algoritmo**:
1. Enumerar ventanas top-level con `win32gui.EnumWindows`.
2. Para cada ventana con título no vacío, obtener PID con `win32process.GetWindowThreadProcessId`.
3. Con `psutil.Process(pid)`, verificar que `username().split("\\")[-1].lower() == os.environ["USERNAME"].lower()`.
4. Si coincide, extraer info del proceso. Si no, descartar.
5. Deduplicar por PID (un proceso puede tener varias ventanas).
6. Manejar `psutil.NoSuchProcess`, `psutil.AccessDenied` con try/except, sin romper el flujo.

Función auxiliar: `construir_muestra() -> dict` que arma:
```python
{
  "hostname": str,           # COMPUTERNAME
  "usuario": str,            # USERNAME
  "timestamp_utc": str,      # ISO 8601 con timezone UTC
  "cantidad_apps": int,
  "apps": list[dict]         # output de capturar_apps_en_uso
}
```

### Módulo: `almacenamiento.py`

Funciones:

**`guardar_muestra(muestra: dict, datos_dir: Path) -> None`**
- Determina archivo del día: `datos_dir/raw/YYYY-MM-DD.jsonl` (basado en fecha local).
- Crea carpetas si no existen.
- Hace append de la muestra como una línea JSON (`json.dumps(muestra) + "\n"`).
- Usa `flush()` para forzar escritura a disco.
- Atómico a nivel de línea: si la app se corta a mitad de escritura, solo se pierde esa línea (las anteriores ya están persistidas).

**`consolidar_a_parquet(fecha: date, datos_dir: Path) -> Path | None`**
- Función reutilizable: la llaman tanto `agente_envio.py` (para el día actual a las
  21:45) como `agente_retry.py` (para fechas anteriores con JSONL huérfano).
- Lee `datos_dir/raw/YYYY-MM-DD.jsonl`.
- Si no existe o está vacío: devuelve None.
- Convierte a Parquet "aplanado": una fila por cada (muestra, app). Columnas:
  - `hostname` (string)
  - `usuario` (string)
  - `timestamp_utc` (timestamp)
  - `nombre_proceso` (string)
  - `nombre_ejecutable` (string)
  - `ruta_ejecutable` (string)
  - `titulo_ventana` (string)
  - `pid` (int32)
  - `memoria_mb` (float32)
- Si una muestra no tiene apps (cantidad_apps=0), genera **una fila** con campos de app en null pero con hostname/usuario/timestamp poblados (para no perder el dato "el equipo estaba sin uso a esa hora").
- Compresión: `snappy`.
- Guarda en `datos_dir/pendientes/YYYY-MM-DD_<hostname>.parquet`.
- Devuelve la ruta del archivo creado.

**`mover_a_enviados(archivo_parquet: Path, datos_dir: Path) -> Path`**
- Mueve el archivo de `pendientes/` a `enviados/YYYY/MM/`.
- Crea carpetas si no existen.
- Devuelve nueva ruta.

**`limpiar_antiguos(datos_dir: Path, dias_retencion_local: int) -> int`**
- Elimina archivos en `raw/`, `pendientes/`, `enviados/` más antiguos que N días.
- Devuelve cantidad de archivos eliminados.

**`listar_pendientes(datos_dir: Path) -> list[Path]`**
- Devuelve archivos `.parquet` en `pendientes/` ordenados por fecha.

### Módulo: `envio.py`

**`enviar_archivo(archivo: Path, api_url: str, token: str, sala_codigo: str, timeout_seg: int, reintentos: int) -> bool`**
- POST `multipart/form-data` al endpoint `{api_url}`.
- Headers: `X-Auth-Token: {token}`.
- Form fields: `sala_codigo` (string), `archivo` (file binario).
- Reintenta `reintentos` veces con backoff exponencial (2s, 4s, 8s).
- Devuelve True si éxito (HTTP 200/201), False si todos los intentos fallaron.
- Loggea cada intento.
- **Importante para desarrollo**: si la variable de entorno `MOCK_API=1` está activa, simula el envío imprimiendo "MOCK: enviando archivo X" y devuelve True sin hacer red. Esto permite probar sin tener el servidor real.

**`procesar_pendientes(datos_dir: Path, cfg: dict) -> dict`**
- Lista pendientes.
- Para cada uno: intenta enviar; si éxito, mueve a `enviados/`.
- Devuelve dict con stats: `{"enviados": N, "fallidos": N, "total": N}`.

### Módulo: `config.py`

**`cargar_config(ruta: Path | None = None) -> dict`**
- Por defecto busca `config.json` en la raíz del proyecto o en `C:\monitoreo\config.json`.
- Valida que existan las keys obligatorias: `api_url`, `token`, `sala_codigo`, `datos_dir`.
- Aplica defaults para keys opcionales.
- Devuelve dict tipado.

Estructura esperada de `config.json`:
```json
{
  "api_url": "http://servidor.universidad.edu:8080/v1/upload",
  "token": "TOKEN-DE-SALA",
  "sala_codigo": "SALA-01",
  "datos_dir": "C:\\monitoreo\\datos",
  "log_dir": "C:\\monitoreo\\logs",
  "intervalo_captura_min": 10,
  "hora_envio": "21:45",
  "timeout_envio_seg": 60,
  "reintentos_envio": 3,
  "dias_retencion_local": 7
}
```

### Módulo: `logger.py`

**`configurar_logger(nombre: str, log_dir: Path) -> Logger`**
- Logger rotativo: `log_dir/{nombre}.log`, max 2MB, 3 backups.
- Formato: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`.
- Nivel: INFO por defecto. DEBUG si `MONITOREO_DEBUG=1` en entorno.
- También a stdout si se ejecuta interactivamente (`sys.stdout.isatty()`).

### Entry points

**`scripts/agente_captura.py`**
- Carga config.
- Configura logger "captura".
- Ejecuta `construir_muestra()`.
- Guarda con `guardar_muestra()`.
- Exit code 0 si éxito, 1 si error.

**`scripts/agente_envio.py`**
- Carga config.
- Configura logger "envio".
- Consolida JSONL del día actual a Parquet.
- Llama a `procesar_pendientes()`.
- Limpia antiguos.
- Exit code 0 si todo enviado, 1 si quedaron pendientes.

**`scripts/agente_retry.py`**
- Carga config.
- Configura logger "retry".
- Busca JSONLs huérfanos en `raw/`: archivos con fecha anterior a hoy que no tengan
  un Parquet correspondiente en `pendientes/` ni en `enviados/`.
- Por cada JSONL huérfano llama a `consolidar_a_parquet(fecha, datos_dir)` — la
  misma función usada en `agente_envio.py` — generando el Parquet en `pendientes/`.
- El JSONL del día actual **no se toca** (sigue acumulando capturas).
- Finalmente llama a `procesar_pendientes()`, que envía todo lo pendiente
  (incluido lo recién consolidado).
- Diseñado para ejecutarse al iniciar sesión (tarea `arranque.xml`).

## Comportamiento esperado en escenarios

| Escenario | Resultado esperado |
|---|---|
| Captura normal | Una línea nueva en `raw/YYYY-MM-DD.jsonl` |
| Equipo apagado entre 21:50 y 21:55 | Archivo queda en `raw/`, cuando se vuelva a preder el computador se consolida y envía |
| Servidor caído a las 21:50 | Archivo queda en `pendientes/`, se reintenta al arranque y en próximas capturas |
| Sin sesión iniciada a las 21:50 | La tarea no se ejecuta (por diseño). Al iniciar sesión, `agente_retry.py` envía lo pendiente |
| Múltiples días de pendientes | Todos se envían en orden cuando hay red |
| Captura sin apps (equipo sin uso) | Igual se registra una muestra con `apps: []` |

## Cosas que NO debe hacer el agente

- No debe usar SYSTEM ni elevación de privilegios.
- No debe modificar archivos fuera de `datos_dir` y `log_dir`.
- No debe enviar datos a la nube de terceros (solo al servidor configurado).
- No debe capturar contraseñas, contenido de pantalla, ni datos del portapapeles.
- No debe capturar procesos de otros usuarios ni del sistema.
- No captura si la cuenta logueada es `Administrator`. Esta restricción se aplica a
  nivel de instalador: `instalar.ps1` configura las tareas programadas únicamente
  bajo la cuenta `estudiante`. Si el admin inicia sesión, las tareas no se disparan
  por diseño. No se requiere validación en el código del agente.

## Pruebas a implementar (pytest)

- `test_captura.py`:
  - `capturar_apps_en_uso()` devuelve lista (puede estar vacía en CI).
  - Mockear `win32gui.EnumWindows` para devolver casos conocidos.
- `test_almacenamiento.py`:
  - Guardar y leer JSONL.
  - Consolidar a Parquet con muestras conocidas y verificar schema.
  - Mover entre carpetas.
  - Limpiar antiguos.
- `test_envio.py`:
  - Mockear `requests.post` con `responses` o `pytest-mock`.
  - Verificar headers, multipart, reintentos, backoff.

## Plan de implementación sugerido

Sugerencia de orden para construir el proyecto, validando cada paso:

1. Estructura de carpetas + `requirements.txt` + `CLAUDE.md`.
2. `config.py` + `logger.py` con tests básicos.
3. `captura.py` + test manual (correr `python -m agente.captura` y ver output).
4. `almacenamiento.py` + tests.
5. `scripts/agente_captura.py` funcional con captura → JSONL.
6. Probar manualmente: ejecutar el script 3 veces y verificar el JSONL.
7. Consolidación a Parquet + tests.
8. `envio.py` con modo MOCK_API.
9. `scripts/agente_envio.py` con mock; verificar Parquet generado.
10. `scripts/agente_retry.py`.
11. XMLs de Tareas Programadas en `deploy/tareas/`:
    - `captura.xml`: trigger cada 10 min, con **delay de 5 minutos al iniciar sesión**
      para evitar capturar el ruido de autostart de Windows.
    - `envio.xml`: trigger diario a las **21:45**.
    - `arranque.xml`: trigger al iniciar sesión, ejecuta **solo `agente_retry.py`**
      (sin captura inmediata; la primera captura llega naturalmente a los ~10 min
      vía `captura.xml`).
    - Todas las tareas se configuran para correr bajo la cuenta `estudiante`.
      Si la sesión activa no es `estudiante`, las tareas no se disparan.
12. `deploy/instalar.ps1` que: crea carpetas, copia archivos, instala dependencias en venv, importa tareas registrando la cuenta `estudiante`.
13. README final con instrucciones.

## Convenciones de código

- Type hints obligatorios en todas las funciones públicas.
- Docstrings al estilo Google para módulos y funciones públicas.
- Manejo explícito de excepciones, no `except:` desnudo.
- Logging en ingles, comentarios en ingles.
- todo el codigo en ingles
- Sin uso de `print()` salvo en CLI explícita; usar logger.
- Usa `pathlib.Path`, no `os.path`.
- No hardcodees rutas; siempre vía config.

## Lo que NO necesito en este momento

- API REST del servidor (eso es la siguiente fase, mockea por ahora).
- Esquemas SQL.
- App de análisis.
- Dashboards.

Concéntrate solo en el agente Windows funcionando end-to-end con mock del servidor.
