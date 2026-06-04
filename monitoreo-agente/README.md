# Agente de Monitoreo de Aplicaciones — Windows

[Español](#español) · [English](#english)

---

<a name="español"></a>

# Español

## ¿Qué es este proyecto?

Agente Python para Windows que monitorea qué aplicaciones utiliza el usuario logueado
en equipos de salas de cómputo universitarias. Cada 10 minutos registra los procesos
activos con ventana visible, los acumula localmente durante el día en formato JSONL y a
las 09:45 consolida y envía un archivo Parquet al servidor de monitoreo vía HTTP.

Diseñado para **500 equipos distribuidos en 20 salas**. No requiere privilegios de
administrador en tiempo de ejecución, no captura contraseñas ni contenido de pantalla, y
solo transmite datos al endpoint configurado.

---

## Arquitectura general

```
PC de sala (Windows 10/11)
─────────────────────────────────────────────────────────────
  [Task Scheduler]
       │
       ├── captura.xml   ──► agente_captura.py  ──► raw/YYYY-MM-DD.jsonl
       │   (cada 10 min)                             (append por línea)
       │
       ├── envio.xml     ──► agente_envio.py    ──► pendientes/FECHA_HOST.parquet
       │   (09:45 diario)                        └──► enviados/YYYY/MM/   (si OK)
       │                                         └──► pendientes/          (si falla)
       │
       └── arranque.xml  ──► agente_retry.py   ──► consolida JSONLs huérfanos
           (al iniciar                           └──► reintenta todo lo pendiente
            sesión)

                              HTTP multipart POST
                              X-Auth-Token: <token-de-sala>
                                     │
                                     ▼
                              Servidor Linux (fase siguiente)
```

---

## Estructura del repositorio

```
Softracker/
|
├── ESPEC_AGENTE.md                   Especificación funcional completa
└── monitoreo-agente/                 Sub-proyecto: agente Windows
    ├── agente/
    │   ├── __init__.py
    │   ├── captura.py                Detección de apps por ventana (win32gui + psutil)
    │   ├── almacenamiento.py         JSONL diario, Parquet, rotación de archivos
    │   ├── envio.py                  HTTP upload con reintentos y backoff
    │   ├── config.py                 Carga y validación de config.json
    │   └── logger.py                 Logger rotativo (2 MB, 3 backups)
    ├── scripts/
    │   ├── agente_captura.py         Entry point: una captura → JSONL
    │   ├── agente_envio.py           Entry point: consolida + envía + limpia
    │   └── agente_retry.py           Entry point: huérfanos + reintento al arranque
    ├── tests/
    │   ├── test_captura.py           24 tests (win32gui mockeado)
    │   ├── test_almacenamiento.py    33 tests (JSONL, Parquet, rotación)
    │   ├── test_envio.py             21 tests (HTTP, reintentos, backoff)
    │   ├── test_config.py            7 tests
    │   ├── test_logger.py            8 tests
    │   └── test_integracion.py       46 tests de integración end-to-end
    ├── deploy/
    │   ├── instalar.ps1              Instalador (requiere admin)
    │   └── tareas/
    │       ├── captura.xml           Tarea: captura cada 10 min
    │       ├── envio.xml             Tarea: envío diario 09:45
    │       └── arranque.xml          Tarea: retry al iniciar sesión
    ├── config.example.json           Plantilla de configuración
    ├── requirements.txt
    └── conftest.py
```

---

## Prerrequisitos

### En el equipo de desarrollo

| Requisito | Versión mínima | Notas |
|-----------|----------------|-------|
| Python    | 3.13+          | Descargar de python.org; marcar "Add to PATH" |
| Git       | cualquiera     | Para clonar el repositorio |
| Windows   | 10 / 11        | Necesario para win32gui (pywin32) |

### En cada PC de sala (producción)

| Requisito | Versión mínima | Notas |
|-----------|----------------|-------|
| Python    | 3.13+          | Instalar antes de correr el instalador |
| Windows   | 10 / 11        | |
| Cuenta `estudiante` | — | Debe existir como usuario local o de dominio |
| Acceso admin (solo instalación) | — | Solo para registrar las tareas; el agente corre como `estudiante` |

---

## Configuración del entorno de desarrollo

```powershell
# 1. Clonar el repositorio
git clone <url-del-repo>
cd Softracker/monitoreo-agente

# 2. Crear y activar el entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Copiar y ajustar la configuración
Copy-Item config.example.json config.json
# Editar config.json con tu editor (ver sección "Referencia de config.json")
```

> **Nota:** `config.json` está en `.gitignore`. Nunca se sube al repositorio porque
> contiene tokens de autenticación.

---

## Tokens de autenticación — generación y gestión

### ¿Qué es el token?

Cada sala tiene un **token compartido** que el agente incluye en el header
`X-Auth-Token` de cada solicitud HTTP. El servidor (fase siguiente) valida este token
para identificar de qué sala proviene el dato y rechazar subidas no autorizadas.

**Una sala = un token.** Los n equipos de la misma sala usan el mismo token; el
campo `sala_codigo` en el Parquet ya identifica la sala individualmente.

### Cuándo se generan los tokens

**Si el servidor aún no existe** (estado actual del proyecto):
El servidor de recepción aún está en construcción. Por ahora, el agente usa
`MOCK_API=1` y el token puede ser cualquier cadena (`"DEV-TOKEN"`). No hace falta
generar tokens reales hasta que el servidor esté listo.

**Cuando el servidor esté disponible:**
Generar un token criptográficamente seguro por sala antes de instalar el agente en
producción. Métodos recomendados:

```python
# Opción A: Python (recomendado, 32 bytes = 64 caracteres hex)
import secrets
print(secrets.token_hex(32))
# Ejemplo de salida: a3f8d2c1b0e9f7a6d5c4b3a2e1f0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2
```

```powershell
# Opción B: PowerShell
[System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
```

```bash
# Opción C: OpenSSL (desde Linux/Mac)
openssl rand -hex 32
```

### Tabla de tokens por sala

Llevar un registro como este (almacenar de forma segura, nunca en el repositorio):

| Sala       | Código    | Token                        | Equipos |
|------------|-----------|------------------------------|---------|
| Sala 01    | SALA-01   | `a3f8d2c1...` (32 bytes hex) | 25      |
| Sala 02    | SALA-02   | `f9e8d7c6...`                | 25      |
| ...        | ...       | ...                          | ...     |
| Sala 20    | SALA-20   | `b5a4f3e2...`                | 25      |

### Reemplazar un token comprometido

1. Generar un nuevo token con cualquiera de los métodos anteriores.
2. Actualizar `config.json` en todos los equipos de esa sala (`instalar.ps1` con
   el nuevo token sobreescribe el archivo).
3. Actualizar el token en la base de datos del servidor.

---

## Referencia de `config.json`

```json
{
  "api_url":               "http://192.168.1.100:8080/v1/upload",
  "token":                 "TOKEN-DE-SALA-01",
  "sala_codigo":           "SALA-01",
  "datos_dir":             "C:/monitoreo/data",
  "log_dir":               "C:/monitoreo/logs",
  "intervalo_captura_min": 10,
  "hora_envio":            "09:45",
  "timeout_envio_seg":     60,
  "reintentos_envio":      3,
  "dias_retencion_local":  7
}
```

| Campo | Obligatorio | Descripción |
|-------|-------------|-------------|
| `api_url` | ✅ | URL completa del endpoint de subida del servidor |
| `token` | ✅ | Token de autenticación de la sala (header `X-Auth-Token`) |
| `sala_codigo` | ✅ | Identificador de la sala (se guarda en cada Parquet) |
| `datos_dir` | ✅ | Directorio raíz para `raw/`, `pendientes/`, `enviados/` |
| `log_dir` | — | Directorio de logs. Default: `C:\monitoreo\logs` |
| `intervalo_captura_min` | — | Minutos entre capturas. Default: `10` |
| `hora_envio` | — | Hora del envío diario. Default: `"09:45"` |
| `timeout_envio_seg` | — | Timeout HTTP por intento. Default: `60` |
| `reintentos_envio` | — | Reintentos con backoff exponencial. Default: `3` |
| `dias_retencion_local` | — | Días antes de borrar archivos locales. Default: `7` |

> **Rutas:** Usar `/` o `\\` como separador. Python maneja ambos en Windows.
>
> **Variable de entorno `MONITOREO_CONFIG`:** Sobreescribe la búsqueda de rutas por
> defecto. Útil para tests y despliegues no estándar:
> `$env:MONITOREO_CONFIG = "D:\otro\config.json"`

---

## Flujo de datos

```
Ciclo diario en un equipo:

  09:00  [Login de estudiante]
           │
           ├── arranque.xml dispara agente_retry.py
           │     • Busca JSONLs de días anteriores sin Parquet → consolida
           │     • Reintenta todos los Parquets pendientes
           │
  09:05  [Primera captura — delay de 5 min para evitar ruido de autostart]
           │
           ├── captura.xml dispara agente_captura.py
           │     • raw/2026-05-27.jsonl ← {"hostname":"PC-01", "apps":[...]}
           │
  09:15, 09:25, ...  [Capturas cada 10 min]
           │
           │     raw/2026-05-27.jsonl va creciendo durante el día
           │
  09:45  [Tarea de envío]
           │
           └── envio.xml dispara agente_envio.py
                 • Consolida raw/2026-05-27.jsonl → pendientes/2026-05-27_PC-01.parquet
                 • POST multipart → servidor (con reintentos si falla)
                 • Si OK: mueve a enviados/2026/05/2026-05-27_PC-01.parquet
                 • Elimina archivos con más de 7 días
```

---

## Estructura de datos locales

```
C:\monitoreo\
├── config.json
├── .venv\
├── agente\          (módulos Python)
├── scripts\         (entry points)
├── data\
│   ├── raw\
│   │   └── 2026-05-27.jsonl        ← append-only durante el día
│   ├── pendientes\
│   │   └── 2026-05-27_PC-01.parquet  ← esperando ser enviado
│   └── enviados\
│       └── 2026\05\
│           └── 2026-05-26_PC-01.parquet  ← ya enviado
└── logs\
    ├── captura.log
    ├── envio.log
    └── retry.log
```

El archivo JSONL es una línea por captura:
```json
{"hostname":"PC-01","usuario":"estudiante","timestamp_utc":"2026-05-27T14:30:00+00:00","cantidad_apps":3,"apps":[{"nombre_proceso":"chrome","nombre_ejecutable":"chrome.exe","ruta_ejecutable":"C:/Program Files/Google/Chrome/Application/chrome.exe","titulo_ventana":"YouTube","pid":4512,"memoria_mb":215.4}]}
```

El Parquet tiene una **fila por (captura, app)**. Si la captura no tenía apps, se
genera una fila con los campos de app en `null` para no perder la marca temporal.

---

## Despliegue en producción — paso a paso

### Paso 1: Preparar el paquete de instalación

En el equipo de desarrollo:

```powershell
cd Softracker/monitoreo-agente
# Verificar que los tests pasan antes de desplegar
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

Comprimir para llevar a los equipos de sala:
```powershell
Compress-Archive -Path "." -DestinationPath "monitoreo-agente.zip"
```

### Paso 2: Generar los tokens (si el servidor ya está disponible)

```python
import secrets
salas = [f"SALA-{i:02d}" for i in range(1, 21)]
tokens = {sala: secrets.token_hex(32) for sala in salas}
for sala, token in tokens.items():
    print(f"{sala}: {token}")
```

Guardar en un archivo seguro (fuera del repositorio). Si el servidor aún no existe,
usar `"DEV-TOKEN"` y habilitar `MOCK_API=1` (ver sección de desarrollo).

### Paso 3: Copiar archivos al equipo de sala

```powershell
# En el equipo de sala (como administrador):
# Descomprimir en una carpeta temporal
Expand-Archive -Path "monitoreo-agente.zip" -DestinationPath "C:\Temp\monitoreo-agente"
```

### Paso 4: Verificar que Python 3.13+ esté instalado

```powershell
python --version   # debe mostrar Python 3.13.x o superior
```

Si no está instalado: descargar de [python.org](https://www.python.org/downloads/),
seleccionar "Add Python to PATH" durante la instalación y marcar "Install for all users".

### Paso 5: Ejecutar el instalador como administrador

Abrir PowerShell como **Administrador** y ejecutar:

```powershell
cd C:\Temp\monitoreo-agente\deploy

# Forma básica (pide sala, URL y token de forma interactiva):
.\instalar.ps1

# Forma completa con parámetros (ideal para scripting masivo):
.\instalar.ps1 `
  -StudentUser "estudiante" `
  -SalaCode    "SALA-01" `
  -ApiUrl      "http://192.168.1.100:8080/v1/upload" `
  -Token       "a3f8d2c1b0e9f7a6d5c4b3a2e1f0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2"
```

> **Si la política de ejecución bloquea el script:**
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

El instalador realiza automáticamente:
1. Verifica que la cuenta `estudiante` existe
2. Crea `C:\monitoreo\` con todas las subcarpetas
3. Copia el paquete `agente\` y los `scripts\`
4. Crea `.venv` e instala dependencias
5. Escribe `config.json` **sin BOM** (para que Python lo lea sin error)
6. Importa las 3 tareas en el Programador de Tareas con `estudiante` como principal

### Paso 6: Verificar la instalación

```powershell
# Ver las tareas creadas
Get-ScheduledTask -TaskPath "\Monitoreo\" | Select-Object TaskName, State

# Probar una captura manualmente
C:\monitoreo\.venv\Scripts\python.exe C:\monitoreo\scripts\agente_captura.py

# Ver el JSONL generado
Get-Content "C:\monitoreo\data\raw\$(Get-Date -Format 'yyyy-MM-dd').jsonl"
```

### Paso 7 (opcional): Probar el envío con mock

```powershell
$env:MOCK_API = "1"
C:\monitoreo\.venv\Scripts\python.exe C:\monitoreo\scripts\agente_envio.py
# Verificar que aparece un .parquet en enviados/
Get-ChildItem "C:\monitoreo\data\enviados" -Recurse
```

### Despliegue masivo (múltiples equipos)

Para instalar en los 500 equipos se puede usar:

- **PDQ Deploy / SCCM**: copiar el ZIP y ejecutar `instalar.ps1` con los parámetros
  correspondientes a cada sala.
- **Script de dominio (GPO)**: script de inicio de sesión de computadora que detecta
  si el agente ya está instalado (`Test-Path C:\monitoreo\config.json`) y lo instala
  si no.
- **PsExec**: `psexec \\PC-SALA01-01 -u admin powershell -File \\servidor\share\instalar.ps1 ...`

Ejemplo para instalar la misma sala en múltiples equipos:

```powershell
$equipos  = "PC-SALA01-01","PC-SALA01-02","PC-SALA01-03"
$token    = "a3f8d2c1..."
$sala     = "SALA-01"
$apiUrl   = "http://192.168.1.100:8080/v1/upload"

foreach ($pc in $equipos) {
    Invoke-Command -ComputerName $pc -ScriptBlock {
        param($t, $s, $u)
        & "C:\Temp\deploy\instalar.ps1" -Token $t -SalaCode $s -ApiUrl $u
    } -ArgumentList $token, $sala, $apiUrl
}
```

---

## Tareas programadas — referencia

| Tarea | Trigger | Script | Propósito |
|-------|---------|--------|-----------|
| `\Monitoreo\Captura` | Al iniciar sesión + 5 min de retraso, repite cada 10 min | `agente_captura.py` | Captura aplicaciones activas |
| `\Monitoreo\Envio` | Diariamente a las 09:45 | `agente_envio.py` | Consolida y envía el día |
| `\Monitoreo\Arranque` | Al iniciar sesión (sin retraso, sin repetición) | `agente_retry.py` | Envía datos pendientes de sesiones anteriores |

**Notas importantes:**
- Las tres tareas usan `InteractiveToken` con principal `estudiante`. Si el administrador
  inicia sesión, **ninguna tarea se dispara** — por diseño.
- `captura.xml` tiene un retraso de 5 minutos para que OneDrive, Teams y el antivirus
  terminen su arranque antes de que se capture, evitando ruido en los datos.
- Si el equipo estaba apagado a las 09:45 (`envio.xml` no se ejecutó), `arranque.xml`
  consolida los datos huérfanos y los envía la próxima vez que `estudiante` inicia sesión.
- `StartWhenAvailable=false` en `envio.xml` y `arranque.xml`: un trigger perdido no
  se re-ejecuta automáticamente — el retry lo maneja `arranque.xml`.

---

## Escenarios de recuperación

| Escenario | Qué ocurre |
|-----------|------------|
| Equipo apagado a las 09:45 | JSONL queda en `raw/`. Al próximo login `arranque.xml` consolida y envía. |
| Servidor caído a las 09:45 | Parquet queda en `pendientes/`. Próximo login reintenta. |
| Sin sesión a las 09:45 | La tarea no se ejecuta (sin sesión interactiva). Próximo login resuelve. |
| Múltiples días sin enviar | Todos los JSONLs huérfanos se consolidan y envían en orden al próximo login. |
| Captura sin apps (equipo ocioso) | Se registra una muestra con `apps: []` → una fila null en el Parquet para preservar la marca temporal. |
| Corte de luz durante escritura | Solo se pierde la línea JSONL en curso; las anteriores ya estaban en disco (`flush()` por línea). |

---

## Variables de entorno útiles

| Variable | Valor | Efecto |
|----------|-------|--------|
| `MOCK_API` | `1` | Simula el envío HTTP; devuelve éxito sin hacer red. Ideal para desarrollo. |
| `MONITOREO_CONFIG` | ruta absoluta | Ruta explícita al `config.json`. Sobreescribe la búsqueda automática. |
| `MONITOREO_DEBUG` | `1` | Activa nivel DEBUG en los logs (muy verboso). |

---

## Ejecutar los tests

```powershell
cd monitoreo-agente
.\.venv\Scripts\Activate.ps1

# Todos los tests (131 en total)
pytest tests/ -v

# Solo tests de integración
pytest tests/test_integracion.py -v

# Solo un módulo
pytest tests/test_almacenamiento.py -v

# Con cobertura (requiere pytest-cov)
pip install pytest-cov
pytest tests/ --cov=agente --cov-report=term-missing
```

Los tests de integración en `TestScriptCaptura` ejecutan `agente_captura.py` como
subprocess real. Requieren una sesión interactiva de Windows activa (no CI headless).
El resto de los tests funcionan en cualquier entorno.

---

## Verificar los logs en producción

```powershell
# Log de capturas (actualizado cada 10 min)
Get-Content "C:\monitoreo\logs\captura.log" -Tail 20

# Log del envío diario
Get-Content "C:\monitoreo\logs\envio.log" -Tail 30

# Log de retry (al iniciar sesión)
Get-Content "C:\monitoreo\logs\retry.log" -Tail 20

# Ver Parquets pendientes de envío
Get-ChildItem "C:\monitoreo\data\pendientes"

# Ver Parquets ya enviados
Get-ChildItem "C:\monitoreo\data\enviados" -Recurse
```

---

## Solución de problemas

### Las tareas no aparecen en el Programador de Tareas

```powershell
# Verificar que el instalador se ejecutó como admin
Get-ScheduledTask -TaskPath "\Monitoreo\" -ErrorAction SilentlyContinue
# Si no devuelve nada, re-ejecutar instalar.ps1 como Administrador
```

### Error `FileNotFoundError: config.json not found`

El agente busca `config.json` en este orden:
1. Variable de entorno `MONITOREO_CONFIG`
2. `C:\monitoreo\config.json`
3. Directorio del paquete (`monitoreo-agente/config.json` en desarrollo)

```powershell
Test-Path "C:\monitoreo\config.json"   # debe devolver True en producción
```

### El JSONL tiene caracteres extraños al inicio

Si el archivo JSONL muestra `ï»¿` al inicio, fue escrito con BOM. Esto ocurre si
se creó manualmente con PowerShell. Los archivos escritos por el agente (Python)
siempre usan UTF-8 sin BOM. Eliminar el archivo y dejar que el agente lo recree.

### `python.exe` no encontrado durante la instalación

```powershell
# Verificar instalación
where.exe python

# Si no aparece: reinstalar Python con "Add to PATH" marcado,
# o agregar manualmente al PATH del sistema:
[Environment]::SetEnvironmentVariable(
  "Path",
  $env:Path + ";C:\Python313",
  [EnvironmentVariableTarget]::Machine
)
```

### El agente captura aplicaciones del administrador

Esto no puede ocurrir por diseño: las tareas se registran con `InteractiveToken` bajo
`estudiante`. Solo se activan cuando `estudiante` es el usuario interactivo.

### El Parquet lleva varios días en `pendientes/` sin enviarse

El servidor no está disponible o la URL/token son incorrectos.

```powershell
# Probar conectividad manual
$env:MOCK_API = "0"
C:\monitoreo\.venv\Scripts\python.exe C:\monitoreo\scripts\agente_envio.py
Get-Content "C:\monitoreo\logs\envio.log" -Tail 30
```

---

## Seguridad

- **Sin privilegios en runtime**: el agente corre como `estudiante` (`LeastPrivilege`).
  Solo el instalador requiere admin y solo se ejecuta una vez.
- **Sin captura de datos sensibles**: solo se registra nombre del proceso, ejecutable,
  título de ventana, PID y memoria. No se accede a portapapeles, contraseñas ni capturas de pantalla.
- **Solo procesos del usuario actual**: `_extract_process_info` filtra por `username()`
  antes de incluir cualquier proceso.
- **Token por sala**: si una sala se ve comprometida, solo ese token se regenera; las
  demás salas no se ven afectadas.
- **Sin escritura fuera de `datos_dir` y `log_dir`**: todas las rutas se derivan de
  `config.json`; el agente no escribe en ningún otro directorio.
- **Sin envío a terceros**: el único destino de red es `api_url` en `config.json`.

---

## Roadmap

- **Fase actual**: Agente Windows — completado ✅
- **Fase 2**: API REST en servidor Linux (`monitoreo-servidor/`)
  - Endpoint `POST /v1/upload` — recibe Parquets, valida token, almacena en BD
  - Gestión de tokens por sala
- **Fase 3**: Aplicación de análisis (`monitoreo-app/`)
  - Dashboard de uso por sala, equipo y período
  - Exportación de reportes

---
---

<a name="english"></a>

# English

## What is this project?

A Python agent for Windows that monitors which applications are being used by the
logged-in user on university computer lab machines. Every 10 minutes it records
active processes with visible windows, accumulates them locally during the day in
JSONL format, and at 09:45 consolidates and sends a Parquet file to the monitoring
server via HTTP.

Designed for **500 machines across 20 rooms**. Requires no admin privileges at
runtime, does not capture passwords or screen content, and only transmits data to
the configured endpoint.

---

## Architecture overview

```
Lab PC (Windows 10/11)
─────────────────────────────────────────────────────────────
  [Task Scheduler]
       │
       ├── captura.xml   ──► agente_captura.py  ──► raw/YYYY-MM-DD.jsonl
       │   (every 10 min)                            (line-by-line append)
       │
       ├── envio.xml     ──► agente_envio.py    ──► pendientes/DATE_HOST.parquet
       │   (daily 09:45)                         └──► enviados/YYYY/MM/  (on success)
       │                                         └──► pendientes/         (on failure)
       │
       └── arranque.xml  ──► agente_retry.py   ──► consolidates orphan JSONLs
           (at login)                           └──► retries all pending files

                              HTTP multipart POST
                              X-Auth-Token: <room-token>
                                     │
                                     ▼
                              Linux server (next phase)
```

---

## Repository structure

```
Softracker/
├── CLAUDE.md                         Permanent project rules
├── ESPEC_AGENTE.md                   Full functional specification
└── monitoreo-agente/                 Sub-project: Windows agent
    ├── agente/
    │   ├── __init__.py
    │   ├── captura.py                App detection by window (win32gui + psutil)
    │   ├── almacenamiento.py         Daily JSONL, Parquet, file rotation
    │   ├── envio.py                  HTTP upload with retries and backoff
    │   ├── config.py                 config.json loader and validator
    │   └── logger.py                 Rotating logger (2 MB, 3 backups)
    ├── scripts/
    │   ├── agente_captura.py         Entry point: one capture → JSONL
    │   ├── agente_envio.py           Entry point: consolidate + send + clean
    │   └── agente_retry.py           Entry point: orphans + retry at login
    ├── tests/
    │   ├── test_captura.py           24 tests (win32gui mocked)
    │   ├── test_almacenamiento.py    33 tests (JSONL, Parquet, rotation)
    │   ├── test_envio.py             21 tests (HTTP, retries, backoff)
    │   ├── test_config.py            7 tests
    │   ├── test_logger.py            8 tests
    │   └── test_integracion.py       46 end-to-end integration tests
    ├── deploy/
    │   ├── instalar.ps1              Installer (requires admin)
    │   └── tareas/
    │       ├── captura.xml           Task: capture every 10 min
    │       ├── envio.xml             Task: daily send at 09:45
    │       └── arranque.xml          Task: retry at login
    ├── config.example.json           Configuration template
    ├── requirements.txt
    └── conftest.py
```

---

## Prerequisites

### Development machine

| Requirement | Min version | Notes |
|-------------|-------------|-------|
| Python      | 3.13+       | Download from python.org; check "Add to PATH" |
| Git         | any         | To clone the repository |
| Windows     | 10 / 11     | Required for win32gui (pywin32) |

### Each lab PC (production)

| Requirement | Min version | Notes |
|-------------|-------------|-------|
| Python      | 3.13+       | Must be installed before running the installer |
| Windows     | 10 / 11     | |
| `estudiante` account | — | Must exist as a local or domain user |
| Admin access (install only) | — | Only to register scheduled tasks; agent runs as `estudiante` |

---

## Development environment setup

```powershell
# 1. Clone the repository
git clone <repo-url>
cd Softracker/monitoreo-agente

# 2. Create and activate the virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and adjust the configuration
Copy-Item config.example.json config.json
# Edit config.json with your editor (see "config.json reference" section)
```

> **Note:** `config.json` is in `.gitignore`. Never commit it — it contains
> authentication tokens.

---

## Authentication tokens — generation and management

### What is the token?

Each room has a **shared token** that the agent includes in the `X-Auth-Token` header
of every HTTP request. The server (next phase) validates this token to identify which
room the data comes from and reject unauthorized uploads.

**One room = one token.** All 500 machines in the same room use the same token; the
`sala_codigo` field in the Parquet already identifies the room. Individual machines
are identified by their `hostname`.

### When to generate tokens

**If the server does not yet exist** (current project state):
The receiving server is still under construction. For now the agent uses `MOCK_API=1`
and the token can be any string (`"DEV-TOKEN"`). No need to generate real tokens until
the server is ready.

**When the server is available:**
Generate one cryptographically secure token per room before installing the agent in
production. Recommended methods:

```python
# Option A: Python (recommended — 32 bytes = 64 hex characters)
import secrets
print(secrets.token_hex(32))
# Sample output: a3f8d2c1b0e9f7a6d5c4b3a2e1f0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2
```

```powershell
# Option B: PowerShell
[System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
```

```bash
# Option C: OpenSSL (from Linux/Mac)
openssl rand -hex 32
```

### Token registry

Keep a secure record like this (never in the repository):

| Room    | Code    | Token                          | Machines |
|---------|---------|--------------------------------|----------|
| Room 01 | SALA-01 | `a3f8d2c1...` (32-byte hex)    | 25       |
| Room 02 | SALA-02 | `f9e8d7c6...`                  | 25       |
| ...     | ...     | ...                            | ...      |
| Room 20 | SALA-20 | `b5a4f3e2...`                  | 25       |

### Rotating a compromised token

1. Generate a new token using any of the methods above.
2. Re-run `instalar.ps1` with the new token on all machines in that room
   (it overwrites `config.json`).
3. Update the token in the server's database.
Only the affected room needs to be updated — other rooms are unaffected.

---

## `config.json` reference

```json
{
  "api_url":               "http://192.168.1.100:8080/v1/upload",
  "token":                 "ROOM-01-TOKEN",
  "sala_codigo":           "SALA-01",
  "datos_dir":             "C:/monitoreo/data",
  "log_dir":               "C:/monitoreo/logs",
  "intervalo_captura_min": 10,
  "hora_envio":            "09:45",
  "timeout_envio_seg":     60,
  "reintentos_envio":      3,
  "dias_retencion_local":  7
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `api_url` | ✅ | Full URL of the server upload endpoint |
| `token` | ✅ | Room authentication token (`X-Auth-Token` header) |
| `sala_codigo` | ✅ | Room identifier (stored in every Parquet file) |
| `datos_dir` | ✅ | Root directory for `raw/`, `pendientes/`, `enviados/` |
| `log_dir` | — | Log directory. Default: `C:\monitoreo\logs` |
| `intervalo_captura_min` | — | Minutes between captures. Default: `10` |
| `hora_envio` | — | Daily send time. Default: `"09:45"` |
| `timeout_envio_seg` | — | HTTP timeout per attempt in seconds. Default: `60` |
| `reintentos_envio` | — | Retry attempts with exponential backoff. Default: `3` |
| `dias_retencion_local` | — | Days before deleting local files. Default: `7` |

> **Paths:** Use `/` or `\\` as separator. Python handles both on Windows.
>
> **`MONITOREO_CONFIG` environment variable:** Overrides the default config search.
> Useful for tests and non-standard deployments:
> `$env:MONITOREO_CONFIG = "D:\alternate\config.json"`

---

## Data flow

```
Daily cycle on one machine:

  09:00  [Student login]
           │
           ├── arranque.xml fires agente_retry.py
           │     • Finds previous-day JSONLs without a matching Parquet → consolidates
           │     • Retries all files in pendientes/
           │
  09:05  [First capture — 5-min delay to skip Windows autostart noise]
           │
           ├── captura.xml fires agente_captura.py
           │     • raw/2026-05-27.jsonl ← {"hostname":"PC-01","apps":[...]}
           │
  09:15, 09:25, ...  [Captures every 10 min]
           │
           │     raw/2026-05-27.jsonl grows throughout the day
           │
  09:45  [Send task]
           │
           └── envio.xml fires agente_envio.py
                 • Consolidates raw/2026-05-27.jsonl
                     → pendientes/2026-05-27_PC-01.parquet
                 • POST multipart → server (with retries on failure)
                 • On success: moves to enviados/2026/05/2026-05-27_PC-01.parquet
                 • Deletes files older than 7 days
```

---

## Local data structure

```
C:\monitoreo\
├── config.json
├── .venv\
├── agente\           (Python modules)
├── scripts\          (entry points)
├── data\
│   ├── raw\
│   │   └── 2026-05-27.jsonl          ← append-only during the day
│   ├── pendientes\
│   │   └── 2026-05-27_PC-01.parquet  ← waiting to be sent
│   └── enviados\
│       └── 2026\05\
│           └── 2026-05-26_PC-01.parquet  ← already sent
└── logs\
    ├── captura.log
    ├── envio.log
    └── retry.log
```

Each JSONL line is one snapshot:
```json
{"hostname":"PC-01","usuario":"estudiante","timestamp_utc":"2026-05-27T14:30:00+00:00","cantidad_apps":3,"apps":[{"nombre_proceso":"chrome","nombre_ejecutable":"chrome.exe","ruta_ejecutable":"C:/Program Files/Google/Chrome/Application/chrome.exe","titulo_ventana":"YouTube","pid":4512,"memoria_mb":215.4}]}
```

The Parquet has **one row per (snapshot, app)**. If a snapshot had no apps, one row
with null app fields is generated to preserve the timestamp (idle period tracking).

---

## Production deployment — step by step

### Step 1: Prepare the installation package

On the development machine:

```powershell
cd Softracker/monitoreo-agente
# Verify all tests pass before deploying
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

Package for distribution:
```powershell
Compress-Archive -Path "." -DestinationPath "monitoreo-agente.zip"
```

### Step 2: Generate tokens (if the server is already available)

```python
import secrets
rooms = [f"SALA-{i:02d}" for i in range(1, 21)]
tokens = {room: secrets.token_hex(32) for room in rooms}
for room, token in tokens.items():
    print(f"{room}: {token}")
```

Save in a secure file outside the repository. If the server is not ready yet,
use `"DEV-TOKEN"` and enable `MOCK_API=1` (see development section).

### Step 3: Copy files to the lab machine

```powershell
# On the lab machine (as administrator):
Expand-Archive -Path "monitoreo-agente.zip" -DestinationPath "C:\Temp\monitoreo-agente"
```

### Step 4: Verify Python 3.13+ is installed

```powershell
python --version   # must show Python 3.13.x or higher
```

If not installed: download from [python.org](https://www.python.org/downloads/),
select "Add Python to PATH" and "Install for all users".

### Step 5: Run the installer as Administrator

Open PowerShell as **Administrator** and run:

```powershell
cd C:\Temp\monitoreo-agente\deploy

# Basic form (prompts for room, URL and token interactively):
.\instalar.ps1

# Full form with parameters (ideal for mass scripting):
.\instalar.ps1 `
  -StudentUser "estudiante" `
  -SalaCode    "SALA-01" `
  -ApiUrl      "http://192.168.1.100:8080/v1/upload" `
  -Token       "a3f8d2c1b0e9f7a6d5c4b3a2e1f0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2"
```

> **If the execution policy blocks the script:**
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

The installer automatically:
1. Verifies the `estudiante` account exists
2. Creates `C:\monitoreo\` with all subdirectories
3. Copies the `agente\` package and `scripts\`
4. Creates `.venv` and installs dependencies
5. Writes `config.json` **without BOM** (so Python can parse it)
6. Imports the 3 tasks into Task Scheduler with `estudiante` as the principal

### Step 6: Verify the installation

```powershell
# Check that the tasks were created
Get-ScheduledTask -TaskPath "\Monitoreo\" | Select-Object TaskName, State

# Run a manual capture test
C:\monitoreo\.venv\Scripts\python.exe C:\monitoreo\scripts\agente_captura.py

# Read the generated JSONL
Get-Content "C:\monitoreo\data\raw\$(Get-Date -Format 'yyyy-MM-dd').jsonl"
```

### Step 7 (optional): Test the send pipeline with mock

```powershell
$env:MOCK_API = "1"
C:\monitoreo\.venv\Scripts\python.exe C:\monitoreo\scripts\agente_envio.py
# Verify a .parquet appeared in enviados/
Get-ChildItem "C:\monitoreo\data\enviados" -Recurse
```

### Mass deployment (multiple machines)

To install on all 500 machines use:

- **PDQ Deploy / SCCM**: copy the ZIP and execute `instalar.ps1` with the parameters
  for each room.
- **GPO startup script**: computer startup script that checks
  `Test-Path C:\monitoreo\config.json` and runs the installer if not found.
- **PsExec**: `psexec \\PC-ROOM01-01 -u admin powershell -File \\server\share\instalar.ps1 ...`

Example for installing one room across multiple machines:

```powershell
$machines = "PC-SALA01-01","PC-SALA01-02","PC-SALA01-03"
$token    = "a3f8d2c1..."
$room     = "SALA-01"
$apiUrl   = "http://192.168.1.100:8080/v1/upload"

foreach ($pc in $machines) {
    Invoke-Command -ComputerName $pc -ScriptBlock {
        param($t, $r, $u)
        & "C:\Temp\deploy\instalar.ps1" -Token $t -SalaCode $r -ApiUrl $u
    } -ArgumentList $token, $room, $apiUrl
}
```

---

## Scheduled tasks — reference

| Task | Trigger | Script | Purpose |
|------|---------|--------|---------|
| `\Monitoreo\Captura` | At login + 5 min delay, repeats every 10 min | `agente_captura.py` | Capture active applications |
| `\Monitoreo\Envio` | Daily at 09:45 | `agente_envio.py` | Consolidate and send the day's data |
| `\Monitoreo\Arranque` | At login (no delay, no repeat) | `agente_retry.py` | Send pending data from previous sessions |

**Important notes:**
- All three tasks use `InteractiveToken` with `estudiante` as principal. If an
  administrator logs in, **no task fires** — by design.
- `captura.xml` has a 5-minute startup delay so OneDrive, Teams, and antivirus
  finish their autostart sequence before the first capture, avoiding noise in data.
- If the machine was off at 09:45 (`envio.xml` missed), `arranque.xml` consolidates
  orphan data and sends it on the next `estudiante` login.
- `StartWhenAvailable=false` in `envio.xml` and `arranque.xml`: a missed trigger is
  not automatically re-run — `arranque.xml` handles recovery.

---

## Recovery scenarios

| Scenario | What happens |
|----------|-------------|
| Machine off at 09:45 | JSONL stays in `raw/`. On next login `arranque.xml` consolidates and sends. |
| Server down at 09:45 | Parquet stays in `pendientes/`. Next login retries. |
| No session at 09:45 | Task doesn't fire (no interactive session). Next login resolves it. |
| Multiple days without sending | All orphan JSONLs are consolidated and sent in order on next login. |
| Capture with no apps (idle machine) | A snapshot with `apps: []` is recorded → one null-field row in Parquet to preserve the timestamp. |
| Power cut during JSONL write | Only the in-progress line is lost; prior lines are already on disk (`flush()` per line). |

---

## Useful environment variables

| Variable | Value | Effect |
|----------|-------|--------|
| `MOCK_API` | `1` | Simulates HTTP send; returns success without network. Ideal for development. |
| `MONITOREO_CONFIG` | absolute path | Explicit path to `config.json`. Overrides automatic search. |
| `MONITOREO_DEBUG` | `1` | Enables DEBUG level logging (very verbose). |

---

## Running the tests

```powershell
cd monitoreo-agente
.\.venv\Scripts\Activate.ps1

# All tests (131 total)
pytest tests/ -v

# Integration tests only
pytest tests/test_integracion.py -v

# Single module
pytest tests/test_almacenamiento.py -v

# With coverage (requires pytest-cov)
pip install pytest-cov
pytest tests/ --cov=agente --cov-report=term-missing
```

The integration tests in `TestScriptCaptura` run `agente_captura.py` as a real
subprocess. They require an active Windows interactive session (will not work
in headless CI). All other tests work in any environment.

---

## Checking logs in production

```powershell
# Capture log (updated every 10 min)
Get-Content "C:\monitoreo\logs\captura.log" -Tail 20

# Daily send log
Get-Content "C:\monitoreo\logs\envio.log" -Tail 30

# Login retry log
Get-Content "C:\monitoreo\logs\retry.log" -Tail 20

# View files waiting to be sent
Get-ChildItem "C:\monitoreo\data\pendientes"

# View already-sent files
Get-ChildItem "C:\monitoreo\data\enviados" -Recurse
```

---

## Troubleshooting

### Tasks don't appear in Task Scheduler

```powershell
Get-ScheduledTask -TaskPath "\Monitoreo\" -ErrorAction SilentlyContinue
# If nothing returned, re-run instalar.ps1 as Administrator
```

### `FileNotFoundError: config.json not found`

The agent searches in this order:
1. `MONITOREO_CONFIG` environment variable
2. `C:\monitoreo\config.json`
3. Package directory (`monitoreo-agente/config.json` in development)

```powershell
Test-Path "C:\monitoreo\config.json"   # must return True in production
```

### JSONL file shows `ï»¿` at the beginning

The file was written with a UTF-8 BOM. This only happens if the file was manually
created with PowerShell. Files written by the agent (Python) are always UTF-8
without BOM. Delete the file and let the agent recreate it.

### `python.exe` not found during installation

```powershell
where.exe python
# If not found: reinstall Python with "Add to PATH" checked,
# or manually add to the system PATH:
[Environment]::SetEnvironmentVariable(
  "Path",
  $env:Path + ";C:\Python313",
  [EnvironmentVariableTarget]::Machine
)
```

### Agent captures administrator applications

Cannot happen by design: tasks are registered with `InteractiveToken` under
`estudiante`. They only fire when `estudiante` is the interactive user.

### Parquet has been in `pendientes/` for several days without being sent

The server is unavailable or the URL/token are incorrect.

```powershell
$env:MOCK_API = "0"
C:\monitoreo\.venv\Scripts\python.exe C:\monitoreo\scripts\agente_envio.py
Get-Content "C:\monitoreo\logs\envio.log" -Tail 30
```

---

## Security

- **No runtime privileges**: the agent runs as `estudiante` (`LeastPrivilege`).
  Only the installer requires admin, and only runs once per machine.
- **No sensitive data captured**: only process name, executable, window title,
  PID, and memory. No clipboard, passwords, or screenshots.
- **Only current user's processes**: `_extract_process_info` filters by `username()`
  before including any process.
- **Per-room tokens**: if one room is compromised, only that token is rotated;
  other rooms are unaffected.
- **No writes outside `datos_dir` and `log_dir`**: all paths are derived from
  `config.json`; the agent writes nowhere else.
- **No third-party network**: the only network destination is `api_url` in `config.json`.

---

## Roadmap

- **Current phase**: Windows agent — complete ✅
- **Phase 2**: REST API on Linux server (`monitoreo-servidor/`)
  - `POST /v1/upload` endpoint — receives Parquets, validates token, stores in DB
  - Per-room token management
- **Phase 3**: Analytics application (`monitoreo-app/`)
  - Usage dashboard by room, machine, and period
  - Report export
