# monitoreo-servidor

REST API server that receives daily Parquet reports from the Windows monitoring agents
deployed on university computer lab machines. Built with FastAPI + Uvicorn, stores files
on the local filesystem, no database required.

---

## Quick start (development)

```bash
cd monitoreo-servidor

# Create venv and install dependencies
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt

# Generate your development tokens (printed once — save them)
python scripts/generar_tokens.py --salas SALA-01 SALA-02
python scripts/generar_tokens.py --admin

# Start the server
uvicorn app.main:app --reload --port 8080
```

The server reads `config.yaml` and `tokens.yaml` from the current directory by default.
Set the `MONITOREO_CONFIG` environment variable to point to a different config file.

---

## Token management

| Command | Effect |
|---|---|
| `python scripts/generar_tokens.py --salas SALA-01` | New token for SALA-01 |
| `python scripts/generar_tokens.py --salas SALA-01 SALA-02 SALA-03` | Batch generation |
| `python scripts/generar_tokens.py --rotar SALA-01 --gracia-horas 24` | Rotate token, old token valid for 24 h |
| `python scripts/generar_tokens.py --admin` | New admin token, updates `config.yaml` |

Plaintext tokens are shown **once** to stdout. Hashes are stored in `tokens.yaml`.

---

## API reference

### `POST /v1/upload`

Receives a Parquet file from an agent.

| Field | Where | Value |
|---|---|---|
| `X-Auth-Token` | Header | Room plaintext token |
| `sala_codigo` | Form field | e.g. `SALA-01` |
| `archivo` | File field | `.parquet` file, snappy compression |

**Responses**

| Code | Meaning |
|---|---|
| 201 | Accepted and stored |
| 400 | Invalid room code or non-Parquet file |
| 401 | Missing or invalid token |
| 413 | File exceeds `max_upload_mb` |
| 500 | Internal error (check logs) |

### `GET /v1/health`

Liveness check. No auth required.

```json
{"status": "ok", "version": "1.0.0", "uptime_seconds": 3600}
```

### `GET /v1/status`

Aggregated stats. Requires `X-Admin-Token` header.

### `GET /v1/rooms`

Lists configured rooms (no hashes exposed). Requires `X-Admin-Token` header.

---

## Inspecting the audit log

```bash
# Last 50 events
python scripts/inspeccionar_log.py

# Last 100 events for SALA-01
python scripts/inspeccionar_log.py --last 100 --room SALA-01

# Events since a date
python scripts/inspeccionar_log.py --since "2026-05-26"

# Only rejected / error events
python scripts/inspeccionar_log.py --errors-only
```

---

## Running tests

```bash
pytest -v
```

---

## Production deployment (Linux)

```bash
# From the repository root on the server
cd monitoreo-servidor
sudo bash deploy/instalar.sh
```

The script:
1. Creates a `monitoreo` system user
2. Installs the app to `/opt/monitoreo-salas`
3. Creates data/log directories under `/var/lib/monitoreo-salas`
4. Copies example config to `/etc/monitoreo-salas/config.yaml`
5. Installs and enables the `monitoreo-api.service` systemd unit

After installation:

```bash
# Edit config
sudo nano /etc/monitoreo-salas/config.yaml

# Generate tokens
sudo -u monitoreo /opt/monitoreo-salas/.venv/bin/python \
    /opt/monitoreo-salas/scripts/generar_tokens.py \
    --salas SALA-01 SALA-02 \
    --tokens-file /etc/monitoreo-salas/tokens.yaml

# Generate admin token
sudo -u monitoreo /opt/monitoreo-salas/.venv/bin/python \
    /opt/monitoreo-salas/scripts/generar_tokens.py \
    --admin \
    --config-file /etc/monitoreo-salas/config.yaml

# Start
sudo systemctl start monitoreo-api
sudo systemctl status monitoreo-api
sudo journalctl -u monitoreo-api -f
```

---

## File layout

```
/opt/monitoreo-salas/       Application code and venv
/etc/monitoreo-salas/       config.yaml and tokens.yaml (root-owned, group-readable)
/var/lib/monitoreo-salas/
    parquet/YYYY/MM/DD/     Accepted Parquet files
    rejected/YYYY-MM-DD/    Files that failed validation
    log/ingest.log          Audit log (JSONL)
/var/log/monitoreo-salas/   API operational log
```
