# monitoreo-report

FastAPI report application for the Parquet files stored by `monitoreo-servidor`.

It uses the same `config.yaml` format, especially:

- `storage.base_dir`
- `storage.parquet_subdir`
- `auth.admin_token_hash`
- `logging.file`

## Quick start

```bash
cd monitoreo-report
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8081
```

Set `MONITOREO_CONFIG` if the config file is not in the current directory.

## Endpoints

- `GET /v1/dashboard`
- `POST /v1/dashboard/login`
- `POST /v1/dashboard/logout`
- `GET /v1/reports/summary`
- `GET /v1/reports/excel`

All report data is read from `storage.base_dir/parquet/YYYY/MM/DD/`.
