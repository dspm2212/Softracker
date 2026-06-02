# Especificación: API del Servidor de Monitoreo

## Contexto del proyecto

Estoy construyendo un sistema para monitorear qué aplicaciones se usan en las
salas de cómputo de una universidad (500 equipos, 20 salas). El agente de
Windows que corre en cada equipo **ya está implementado** y vive en el
sub-proyecto hermano `monitoreo-agente/`.

Este documento especifica **únicamente el servidor**: una API REST en Python que
recibe archivos Parquet desde los agentes y los almacena en disco de forma
organizada. También expone endpoints mínimos de monitoreo para verificar que
el sistema está sano.

**NO incluye la app de análisis** (consultas con DuckDB, dashboards, reportes).
Eso es una fase futura.

## Decisiones de arquitectura ya tomadas (no cambiar)

- **Lenguaje**: Python 3.13+ (servidor Linux).
- **Framework**: FastAPI + Uvicorn.
- **Almacenamiento**: sistema de archivos (sin base de datos en esta fase).
- **Formato de archivo recibido**: Parquet, compresión snappy.
- **Autenticación**: header `X-Auth-Token`, un token por sala.
- **Almacenamiento de tokens**: hashes SHA-256 en un archivo YAML, nunca texto plano.
- **Supervisión de proceso**: systemd en producción.
- **Transporte**: HTTP (sin HTTPS en esta fase, red interna).
- **Puerto por defecto**: 8080.

## Contrato agente ↔ servidor (inmutable)

El agente (en `monitoreo-agente/`) ya hace:

```
POST /v1/upload HTTP/1.1
Host: servidor.universidad.edu:8080
X-Auth-Token: <token-de-sala>
Content-Type: multipart/form-data; boundary=...

--boundary
Content-Disposition: form-data; name="sala_codigo"

SALA-01
--boundary
Content-Disposition: form-data; name="archivo"; filename="2026-05-26_SALA01-PC05.parquet"
Content-Type: application/octet-stream

<bytes binarios del parquet>
--boundary--
```

El servidor DEBE:
- Aceptar exactamente este request.
- Validar el token contra el token activo de `sala_codigo`.
- Validar que el archivo sea un Parquet válido (parseable con pyarrow).
- Almacenarlo en disco.
- Responder HTTP 201 en éxito, con un pequeño body JSON.

## Estructura del proyecto

```
Softracker/
├── monitoreo-agente/           # sub-proyecto existente
└── monitoreo-servidor/         # ESTE sub-proyecto
    ├── app/
    │   ├── __init__.py
    │   ├── main.py             # Punto de entrada de FastAPI
    │   ├── config.py           # Carga y validación de configuración
    │   ├── auth.py             # Validación de tokens
    │   ├── storage.py          # Guardado de archivos (rutas, escrituras atómicas)
    │   ├── validation.py       # Validación de Parquet
    │   ├── audit.py            # Log de auditoría de ingesta (JSONL)
    │   ├── logger.py           # Setup de logging
    │   └── routers/
    │       ├── __init__.py
    │       ├── upload.py       # POST /v1/upload
    │       ├── health.py       # GET /v1/health
    │       └── status.py       # GET /v1/status, /v1/rooms
    ├── scripts/
    │   ├── generar_tokens.py   # Generar / rotar tokens de salas
    │   └── inspeccionar_log.py # Leer log de auditoría ingest.log
    ├── tests/
    │   ├── conftest.py         # Fixtures comunes (TestClient, dirs temporales)
    │   ├── test_auth.py
    │   ├── test_storage.py
    │   ├── test_upload.py
    │   ├── test_health.py
    │   └── test_status.py
    ├── deploy/
    │   ├── monitoreo-api.service       # unit de systemd
    │   ├── instalar.sh                 # script de instalación
    │   ├── config.example.yaml
    │   └── tokens.example.yaml
    ├── config.yaml                     # configuración local para desarrollo
    ├── tokens.yaml                     # tokens locales para desarrollo
    ├── requirements.txt
    └── README.md
```

## Especificación funcional

### Módulo: `config.py`

**`load_config(path: Path | None = None) -> AppConfig`**

- Por defecto busca `config.yaml` en la raíz del proyecto o en
  `/etc/monitoreo-salas/config.yaml`.
- Valida las keys obligatorias.
- Devuelve un objeto tipado (pydantic `BaseModel` llamado `AppConfig`).

Estructura esperada de `config.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  max_upload_mb: 50

storage:
  base_dir: "/var/lib/monitoreo-salas"   # en dev: ./data
  parquet_subdir: "parquet"               # archivos van bajo base_dir/parquet/YYYY/MM/DD/
  audit_log_file: "log/ingest.log"        # bajo base_dir
  reject_dir: "rejected"                  # archivos malformados para forensics

auth:
  tokens_file: "tokens.yaml"              # ruta al archivo de tokens
  max_clock_skew_min: 30                  # tolerancia entre relojes de agente y servidor

logging:
  level: "INFO"
  file: "/var/log/monitoreo-salas/api.log"   # en dev: ./logs/api.log
  rotate_mb: 50
  backups: 5
```

### Módulo: `auth.py`

Modelo de tokens: cada sala tiene **un token activo a la vez**, almacenado como
hash SHA-256. Una sala puede mantener un token anterior (`previous`) activo
por algunas horas durante rotaciones.

**`load_tokens(path: Path) -> dict[str, RoomTokens]`**

Lee un YAML con la siguiente estructura:

```yaml
rooms:
  SALA-01:
    active_hash: "a1b2c3d4..."
    active_since: "2026-01-15T10:00:00Z"
    previous_hash: null
    previous_until: null
  SALA-02:
    active_hash: "..."
    ...
```

**`validate_token(token: str, room_code: str, tokens: dict) -> bool`**

- Calcula `sha256(token)`.
- Compara contra `active_hash` de la sala.
- Si no coincide y hay `previous_hash` cuyo `previous_until` no ha expirado,
  acepta (período de gracia durante rotaciones).
- Devuelve True/False.

**`hash_token(token: str) -> str`**

- `sha256(token.encode("utf-8")).hexdigest()`.

### Módulo: `validation.py`

**`validate_parquet(file_bytes: bytes) -> tuple[bool, str | None]`**

- Intenta leer los bytes con `pyarrow.parquet.read_metadata(BytesIO(...))`.
- Devuelve `(True, None)` si es Parquet válido.
- Devuelve `(False, error_message)` si no lo es.
- NO carga el archivo completo en memoria; solo metadata.

**`validate_max_size(file_bytes: bytes, max_mb: int) -> bool`**

- True si `len(file_bytes) <= max_mb * 1024 * 1024`.

**`validate_room_code(code: str) -> bool`**

- Acepta el patrón `^[A-Z0-9-]{3,20}$`.
- Devuelve True/False.

### Módulo: `storage.py`

**`save_parquet(file_bytes: bytes, room_code: str, original_filename: str, base_dir: Path) -> Path`**

- Calcula la ruta de destino:
  - `base_dir/parquet/YYYY/MM/DD/{room_code}_{original_filename}`
  - Donde `YYYY/MM/DD` es la fecha actual del servidor (UTC).
- Crea carpetas si no existen.
- Escritura atómica: escribe a `.tmp` y luego `os.replace()` al nombre final.
  Previene que el archivo se lea a medio escribir.
- Si ya existe un archivo con el mismo nombre, **NO sobrescribe**: agrega
  sufijo `_dupN` (`_dup1`, `_dup2`, ...) y registra WARNING en log.
- Devuelve la ruta final.

**`save_rejected(file_bytes: bytes, room_code: str, reason: str, base_dir: Path) -> Path`**

- Para archivos que fallan validación pero queremos guardar para forensics.
- Ruta: `base_dir/rejected/YYYY-MM-DD/{room_code}_{ISO-timestamp}_{reason}.bin`
- Devuelve la ruta.

**`storage_stats(base_dir: Path) -> dict`**

- Cuenta archivos y tamaño total bajo `parquet/`.
- Cuenta archivos en `rejected/`.
- Devuelve: `{"total_files": N, "total_size_mb": X, "rejected_files": N}`.

### Módulo: `audit.py`

Log JSONL (un evento por línea) con **cada** request recibido, aceptado o
rechazado. Crucial para depurar problemas de despliegue.

**`log_event(event: dict, audit_file: Path) -> None`**

- Hace append de una línea JSON.
- Campos del evento:
  ```json
  {
    "ts_utc": "2026-05-26T21:48:12.345Z",
    "client_ip": "10.20.1.5",
    "endpoint": "/v1/upload",
    "room_code": "SALA-01",
    "result": "ok|invalid_token|invalid_parquet|room_unknown|file_too_big|error",
    "file_bytes": 47821,
    "saved_path": "/var/lib/.../2026/05/26/SALA-01_2026-05-26_PC05.parquet",
    "error_detail": null,
    "elapsed_ms": 42
  }
  ```
- Rota diariamente: `ingest.log.2026-05-25` (gestionado con handlers o nombre simple).

**`tail_events(audit_file: Path, n: int = 100) -> list[dict]`**

- Devuelve los últimos N eventos (para `/v1/status` y script de inspección).

### Módulo: `logger.py`

**`configure_logger(name: str, log_file: Path, level: str, rotate_mb: int, backups: int) -> Logger`**

- Logger rotativo (`RotatingFileHandler`).
- Formato: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`.
- También a stdout si `sys.stdout.isatty()` (desarrollo).

### Endpoints

#### `POST /v1/upload`

**Recibe archivos Parquet desde los agentes.**

Request:
- Header: `X-Auth-Token: <token>` (obligatorio)
- multipart/form-data:
  - `sala_codigo`: string
  - `archivo`: UploadFile (binario)

Flujo:
1. Leer header `X-Auth-Token`. Si falta → 401.
2. Validar formato de `sala_codigo`. Si inválido → 400.
3. Verificar que `sala_codigo` exista en la configuración de tokens. Si no → 401
   (no revelar si la sala existe).
4. Leer los bytes del archivo (con límite `max_upload_mb`).
5. Validar `len(bytes) <= max_upload_mb`. Si excede → 413.
6. Validar el token contra el token de la sala. Si inválido → 401.
7. Validar que sea un Parquet válido. Si no → guardar en `rejected/`, log de
   auditoría → 400.
8. Guardar con `storage.save_parquet()`.
9. Registrar evento en log de auditoría (result=ok).
10. Devolver:
    ```json
    HTTP 201 Created
    {
      "status": "ok",
      "saved_as": "2026/05/26/SALA-01_2026-05-26_SALA01-PC05.parquet",
      "size_bytes": 47821
    }
    ```

Casos de error:
- 401: `{"detail": "invalid token"}` (genérico, sin filtraciones)
- 400: `{"detail": "invalid room code"}` / `{"detail": "invalid parquet file"}`
- 413: `{"detail": "file too large"}`
- 500: `{"detail": "internal error"}` (registrar traceback completo, respuesta
  genérica)

#### `GET /v1/health`

Liveness check simple.

Response:
```json
HTTP 200
{
  "status": "ok",
  "version": "1.0.0",
  "uptime_seconds": 12345
}
```

NO valida token. Usado por balanceadores de carga y monitoreo básico.

#### `GET /v1/status`

Estado agregado. **Requiere** header `X-Admin-Token` (un token distinto y más
restringido, configurado en `config.yaml` bajo `auth.admin_token_hash`).

Response:
```json
HTTP 200
{
  "uptime_seconds": 12345,
  "rooms_configured": 20,
  "storage": {
    "total_files": 1543,
    "total_size_mb": 78.5,
    "rejected_files": 2
  },
  "last_24h": {
    "uploads_ok": 487,
    "uploads_rejected": 3,
    "uploads_by_room": {
      "SALA-01": 24,
      "SALA-02": 23,
      ...
    }
  }
}
```

Calculado leyendo `audit.log` (sin BD).

#### `GET /v1/rooms`

Lista de salas configuradas (sin exponer tokens). Requiere `X-Admin-Token`.

```json
HTTP 200
{
  "rooms": [
    {"code": "SALA-01", "active_since": "2026-01-15T10:00:00Z", "in_rotation": false},
    {"code": "SALA-02", "active_since": "2026-01-15T10:00:00Z", "in_rotation": false}
  ]
}
```

### Script de tokens: `scripts/generar_tokens.py`

CLI standalone para generar tokens de salas.

```bash
# Generar token para una o varias salas
python scripts/generar_tokens.py --salas SALA-01 SALA-02 SALA-03

# Rotar el token de una sala existente (mantiene el viejo por 24h)
python scripts/generar_tokens.py --rotar SALA-01 --gracia-horas 24

# Generar token de admin
python scripts/generar_tokens.py --admin
```

Comportamiento:
- Genera tokens aleatorios de 36 bytes (base64-url, ~48 chars).
- Actualiza `tokens.yaml` con el nuevo hash.
- Imprime el token en texto plano **solo una vez** en consola.
- Advierte al usuario que lo guarde en un lugar seguro.

### Script de inspección: `scripts/inspeccionar_log.py`

Lee `ingest.log` y muestra los eventos recientes con filtros.

```bash
python scripts/inspeccionar_log.py --last 50
python scripts/inspeccionar_log.py --room SALA-01 --since "2026-05-26"
python scripts/inspeccionar_log.py --errors-only
```

## Tests a implementar (pytest)

### `test_auth.py`
- `hash_token` es determinístico.
- `validate_token` acepta token válido.
- `validate_token` rechaza token inválido.
- `validate_token` acepta `previous_hash` dentro de la ventana de gracia.
- `validate_token` rechaza `previous_hash` después de que expira la gracia.

### `test_storage.py`
- `save_parquet` crea la ruta correcta con YYYY/MM/DD.
- `save_parquet` NO sobrescribe si el nombre existe; agrega `_dup1`.
- `save_parquet` es atómico (escribe `.tmp` y luego renombra).
- `save_rejected` pone el archivo en `rejected/`.

### `test_upload.py` (con `TestClient`)
- Upload OK con token válido y Parquet válido → 201.
- Sin `X-Auth-Token` → 401.
- Token de otra sala → 401.
- Archivo > `max_upload_mb` → 413.
- Archivo que no es Parquet → 400 y va a `rejected/`.
- Formato inválido de `sala_codigo` → 400.
- Sala desconocida → 401 (no revelar que la sala no existe).

### `test_health.py`
- `GET /v1/health` siempre devuelve 200 sin token.

### `test_status.py`
- Sin `X-Admin-Token` → 401.
- Con `X-Admin-Token` válido → 200 con keys correctas.

## Comportamiento esperado en escenarios operacionales

| Escenario | Resultado esperado |
|---|---|
| Agente envía archivo a las 21:50 | HTTP 201, archivo guardado en `parquet/YYYY/MM/DD/` |
| Agente envía archivo a las 02:00 | Igual (no hay restricción horaria en el servidor) |
| Agente reintenta enviar el mismo archivo | Nuevo archivo con sufijo `_dup1`, auditoría loggea WARNING |
| Agente envía con token rotado (dentro de gracia) | Aceptado, auditoría loggea `result=ok_previous_token` |
| Agente envía con token expirado | 401, auditoría loggea `result=invalid_token` |
| Agente envía un archivo corrupto | 400, guardado en `rejected/`, auditoría loggea `result=invalid_parquet` |
| 100 agentes envían simultáneamente | Todos procesados (uvicorn con 2 workers los maneja) |
| Falta el archivo de tokens | La API NO arranca, error en logs al iniciar |
| Disco lleno | 500, auditoría loggea `result=error` con `error_detail` |

## Criterios de hardening (no opcionales)

- **Respuestas genéricas**: no filtrar si la sala existe, si el token está bien
  formado, ni otra información que ayude a un atacante a enumerar.
- **Rate limiting**: no implementado en esta fase, pero dejar TODO; si se
  vuelve necesario, documentar dónde se agregaría (middleware).
- **Limitar tamaño de body**: `max_upload_mb` enforced antes de leer el
  archivo completo a memoria.
- **Parquet auténtico**: validar con pyarrow, no por extensión.
- **Log de auditoría**: cada request loggeado, incluyendo rechazos.
- **Permisos**: el usuario de producción es `monitoreo`, con lectura sobre
  tokens y escritura sobre `base_dir`. Nada más.

## Lo que NO necesito en este momento

- Endpoints para consultar datos almacenados (eso es la app de análisis).
- HTTPS / TLS (la red es interna en esta fase).
- PostgreSQL u otra BD.
- UI HTML/web.
- Alertas por email/Slack (un script aparte fuera de la API puede hacerlo
  leyendo el log de auditoría).
- Docker / Kubernetes (systemd alcanza para 4GB).

Concéntrate solo en la API de `/v1/upload` + endpoints mínimos de monitoreo,
con tests robustos y despliegue systemd listo para usar.

## Plan de implementación sugerido

Sugerencia de orden para construir el proyecto, validando cada paso:

1. Estructura de carpetas + `requirements.txt` + `CLAUDE.md`.
2. `config.py` + `logger.py` con tests básicos.
3. `auth.py` (hash y validación de tokens) + tests.
4. `scripts/generar_tokens.py` funcionando de forma independiente.
5. `validation.py` (Parquet, tamaños, códigos de sala) + tests.
6. `storage.py` + tests (escritura atómica, no-sobrescritura).
7. `audit.py` + tests.
8. `app/main.py` solo con `health`, verificar que arranca con uvicorn.
9. `routers/upload.py` + tests de integración con `TestClient`.
10. `routers/status.py`, `routers/rooms.py` + tests.
11. `scripts/inspeccionar_log.py`.
12. `deploy/monitoreo-api.service` (systemd) + `deploy/instalar.sh`.
13. `README.md` final con instrucciones de instalación y operación.

## Convenciones de código

- Type hints obligatorios en todas las funciones públicas.
- Docstrings al estilo Google para módulos y funciones públicas.
- Manejo explícito de excepciones, no `except:` desnudo.
- Logging en inglés, comentarios en inglés.
- Todo el código en inglés.
- Sin uso de `print()` salvo en CLI explícita; usar logger.
- Usar `pathlib.Path`, no `os.path`.
- No hardcodear rutas; siempre vía config.
- Valores sensibles (tokens, rutas) solo vía config, nunca en código.
