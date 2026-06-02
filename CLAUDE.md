# Proyecto: Agente de Monitoreo de Salas

Este proyecto es un agente Python para Windows que monitorea aplicaciones en uso
en equipos de salas de cómputo universitarias.

## Reglas permanentes para este proyecto

### Idioma
- Mensajes de log, comentarios y docstrings: **ingles**.
- Nombres de variables, funciones, clases: **inglés** (estándar Python).
- Documentación (README, este archivo): ingles y español.

### Stack tecnológico
- Python 3.13+
- Sistema operativo objetivo: Windows 10/11
- Dependencias clave: psutil, pywin32, pyarrow, requests
- Tests: pytest

### Convenciones de código
- Encabezado en cada archvio que se cree con funcionalidad descripcion y autor Daniel Perez segun estandar google
- Type hints obligatorios en funciones públicas.
- `pathlib.Path`, no `os.path`.
- No usar `print()`; usar logger.
- No `except:` desnudo; siempre tipo específico.
- Funciones con responsabilidad única, no megafunciones.
- codigo limpio, simple y funcional

### Decisiones arquitectónicas
- El agente corre como el usuario logueado (NO como SYSTEM).
- Captura solo procesos del usuario actual con ventana asociada.
- Almacena durante el día en JSONL append-only.
- Consolida a Parquet a las 21:50 y envía vía HTTP multipart.
- En desarrollo: usa `MOCK_API=1` para no necesitar servidor real.

### Lo que NO hago en este proyecto
- No accedo a la red salvo al endpoint configurado.
- No leo contraseñas, portapapeles, ni capturas de pantalla.
- No modifico archivos fuera de las carpetas declaradas.
- No requiero privilegios de administrador en runtime.


### Antes de generar código
1. Lee `ESPEC_AGENTE.md` si existe.
2. Lee `ESPEC_SERVIDOR.md` si existe.
2. Pregúntame si hay ambigüedad en lugar de asumir.
3. Propón el plan antes de crear muchos archivos.
4. Prueba cada componente que crees antes de avanzar al siguiente.

### Estructura de sub-proyectos
l repositorio raíz (`Softracker/`) aloja múltiples proyectos. El servidor vive
en `monitoreo-servidor/`. El agente de Windows vive en `monitoreo-agente/`.
Desarrollos futuros (app de análisis, dashboards) van en carpetas hermanas.

### Compatibilidad con el agente
El agente (ya implementado en `monitoreo-agente/`) envía:
- HTTP POST `multipart/form-data` a `POST /v1/upload`.
- Header: `X-Auth-Token: <token-de-sala>`.
- Campos del form:
  - `sala_codigo` (string) — ej: "SALA-01"
  - `archivo` (archivo binario) — Parquet, comprimido con snappy
- Espera HTTP 200/201 en éxito.
El servidor DEBE ser compatible con ese contrato. cambialo unicamente si ves que algo no funciona o va generar incompatibilidad, los headers ni los nombres de los campos sin alinearlo primero con el agente.

### Comandos útiles del proyecto
```powershell
# Moverse al sub-proyecto del agente
cd monitoreo-agente

# Crear venv
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# Probar captura standalone
python -m agente.captura

# Correr una captura completa
python scripts/agente_captura.py

# Correr envío con mock
$env:MOCK_API=1
python scripts/agente_envio.py

# Correr retry con mock
$env:MOCK_API=1
python scripts/agente_retry.py

# Tests
pytest tests/ -v
```
