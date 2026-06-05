"""Dashboard and report endpoints for monitoring data.

Author: Daniel Perez
"""

from __future__ import annotations

import html
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from app.auth import check_credentials, hash_password
from app.config import AppConfig
from app.dependencies import get_config
from app.reporting import ReportFilters, build_report, export_report_excel, load_monitoring_rows
from app.validation import validate_room_code

router = APIRouter()

COOKIE_NAME = "monitoreo_session"

# Default window shown when the user opens the dashboard without any filter.
_DEFAULT_DAYS = 7


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _is_authenticated(request: Request, config: AppConfig) -> bool:
    """Return True if the session cookie contains a valid password hash."""
    cookie = request.cookies.get(COOKIE_NAME)
    return bool(cookie and cookie == config.credentials.password_hash)


def _require_auth(request: Request, config: AppConfig) -> None:
    """Raise 401 if the request is not authenticated."""
    if not _is_authenticated(request, config):
        raise HTTPException(status_code=401, detail="authentication required")


# ── Dashboard ──────────────────────────────────────────────────────────────────

@router.get("/v1/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    fecha_inicio: date | None = Query(None),
    fecha_fin: date | None = Query(None),
    sala_codigo: str | None = Query(None),
    filtrado: str | None = Query(None),
    config: AppConfig = Depends(get_config),
) -> HTMLResponse:
    """Render the admin dashboard. Redirects to login if not authenticated."""
    if not _is_authenticated(request, config):
        return HTMLResponse(_login_html(), status_code=200)

    error_msg: str | None = None

    if filtrado == "1" and (fecha_inicio is None or fecha_fin is None):
        error_msg = "Debes ingresar ambas fechas para filtrar." if not fecha_inicio and not fecha_fin \
            else "Debes ingresar la fecha de inicio." if not fecha_inicio \
            else "Debes ingresar la fecha de fin."

    if fecha_inicio is None or fecha_fin is None:
        fecha_fin = date.today()
        fecha_inicio = fecha_fin - timedelta(days=_DEFAULT_DAYS - 1)

    try:
        filters = _build_filters(fecha_inicio, fecha_fin, sala_codigo)
        report = _load_report(config, filters)
    except HTTPException as exc:
        filters = _build_filters(fecha_inicio, fecha_fin, None)
        report = _load_report(config, filters)

    return HTMLResponse(_dashboard_html(report, filters, error=error_msg))


@router.post("/v1/dashboard/login")
def dashboard_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    config: AppConfig = Depends(get_config),
) -> Response:
    """Validate credentials and set a session cookie."""
    if not check_credentials(username, password, config):
        return HTMLResponse(_login_html(error="Usuario o contrasena incorrectos"), status_code=401)

    response = RedirectResponse("/v1/dashboard", status_code=303)
    # Cookie stores the password hash — never the plaintext password.
    response.set_cookie(
        COOKIE_NAME,
        config.credentials.password_hash,
        max_age=8 * 60 * 60,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/v1/dashboard/logout")
def dashboard_logout() -> Response:
    response = RedirectResponse("/v1/dashboard", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ── JSON / Excel API ───────────────────────────────────────────────────────────

@router.get("/v1/reports/summary")
def report_summary(
    request: Request,
    fecha_inicio: date | None = Query(None),
    fecha_fin: date | None = Query(None),
    sala_codigo: str | None = Query(None),
    x_admin_token: str | None = Header(None),
    config: AppConfig = Depends(get_config),
) -> dict:
    """Return JSON summary data. Accepts session cookie or X-Admin-Token header."""
    _require_auth_flexible(request, x_admin_token, config)
    filters = _build_filters(fecha_inicio, fecha_fin, sala_codigo)
    report = _load_report(config, filters)
    return {
        "filters": {
            "fecha_inicio": filters.fecha_inicio.isoformat() if filters.fecha_inicio else None,
            "fecha_fin": filters.fecha_fin.isoformat() if filters.fecha_fin else None,
            "sala_codigo": filters.sala_codigo,
        },
        "summary": report["summary"],
        "rooms": report["rooms"],
        "apps": report["apps"],
        "machines": report["machines"],
    }


@router.get("/v1/reports/excel")
def report_excel(
    request: Request,
    fecha_inicio: date | None = Query(None),
    fecha_fin: date | None = Query(None),
    sala_codigo: str | None = Query(None),
    x_admin_token: str | None = Header(None),
    config: AppConfig = Depends(get_config),
) -> StreamingResponse:
    """Export monitoring data as an Excel workbook."""
    _require_auth_flexible(request, x_admin_token, config)
    filters = _build_filters(fecha_inicio, fecha_fin, sala_codigo)
    report = _load_report(config, filters)
    content = export_report_excel(report)

    filename_parts = ["monitoreo"]
    if filters.sala_codigo:
        filename_parts.append(filters.sala_codigo.lower())
    if filters.fecha_inicio:
        filename_parts.append(filters.fecha_inicio.isoformat())
    if filters.fecha_fin:
        filename_parts.append(filters.fecha_fin.isoformat())
    filename = "_".join(filename_parts) + ".xlsx"

    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _require_auth_flexible(
    request: Request,
    x_admin_token: str | None,
    config: AppConfig,
) -> None:
    """Accept either a valid session cookie or the password via X-Admin-Token header."""
    if _is_authenticated(request, config):
        return
    if x_admin_token and hash_password(x_admin_token) == config.credentials.password_hash:
        return
    raise HTTPException(status_code=401, detail="authentication required")


def _load_report(config: AppConfig, filters: ReportFilters) -> dict:
    rows = load_monitoring_rows(
        config.storage.base_dir,
        config.storage.parquet_subdir,
        filters,
    )
    return build_report(rows)


def _build_filters(
    fecha_inicio: date | None,
    fecha_fin: date | None,
    sala_codigo: str | None,
) -> ReportFilters:
    if fecha_inicio and fecha_fin and fecha_inicio > fecha_fin:
        raise HTTPException(status_code=400, detail="fecha_inicio must be <= fecha_fin")
    if sala_codigo and not validate_room_code(sala_codigo):
        raise HTTPException(status_code=400, detail="invalid room code")
    return ReportFilters(fecha_inicio, fecha_fin, sala_codigo)


# ── HTML rendering ─────────────────────────────────────────────────────────────

def _login_html(error: str | None = None) -> str:
    error_block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Monitoreo - Acceso</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f0f4f8; color: #1a2332; }}
    main {{ max-width: 380px; margin: 12vh auto; background: #fff; border: 1px solid #d1dae6;
            padding: 32px; border-radius: 10px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }}
    h1 {{ margin: 0 0 6px; font-size: 22px; }}
    p.subtitle {{ margin: 0 0 22px; color: #64748b; font-size: 13px; }}
    label {{ display: block; font-size: 13px; font-weight: 700; margin-bottom: 6px; color: #374151; }}
    input {{ width: 100%; padding: 10px 12px; border: 1px solid #cbd5e1;
             border-radius: 6px; font-size: 14px; margin-bottom: 14px; }}
    input:focus {{ outline: none; border-color: #176b87; box-shadow: 0 0 0 3px rgba(23,107,135,.15); }}
    button {{ width: 100%; padding: 11px; border: 0; border-radius: 6px;
              background: #176b87; color: #fff; font-size: 15px; font-weight: 700; cursor: pointer; }}
    button:hover {{ background: #135d75; }}
    .error {{ color: #b91c1c; font-size: 13px; font-weight: 700; margin-bottom: 12px; }}
  </style>
</head>
<body>
  <main>
    <h1>Monitoreo Salas</h1>
    <p class="subtitle">Sistema de seguimiento de aplicaciones</p>
    {error_block}
    <form method="post" action="/v1/dashboard/login">
      <label for="username">Usuario</label>
      <input id="username" name="username" type="text" autocomplete="username"
             autocapitalize="none" required autofocus>
      <label for="password">Contrasena</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Ingresar</button>
    </form>
  </main>
</body>
</html>"""


def _dashboard_html(report: dict, filters: ReportFilters, error: str | None = None) -> str:
    summary = report["summary"]
    query = _query_string(filters)

    room_rows    = "\n".join(_room_row(r)    for r in report["rooms"][:100])
    machine_rows = "\n".join(_machine_row(r) for r in report["machines"][:200])
    app_rows     = "\n".join(_app_row(r)     for r in report["apps"][:100])

    empty_rooms    = '<tr><td colspan="8">Sin datos recibidos.</td></tr>'    if not room_rows    else ""
    empty_machines = '<tr><td colspan="8">Sin equipos registrados.</td></tr>' if not machine_rows else ""
    empty_apps     = '<tr><td colspan="5">Sin aplicaciones registradas.</td></tr>' if not app_rows else ""

    filter_room  = html.escape(filters.sala_codigo or "")
    filter_start = filters.fecha_inicio.isoformat() if filters.fecha_inicio else ""
    filter_end   = filters.fecha_fin.isoformat()   if filters.fecha_fin   else ""

    primera = html.escape(str(summary.get("primera_captura") or "-"))
    ultima  = html.escape(str(summary.get("ultima_captura")  or "-"))
    toast_block = (
        f'<div class="toast" id="toast">'
        f'<span>⚠ {html.escape(error)}</span>'
        f'<button onclick="document.getElementById(\'toast\').style.display=\'none\'">✕</button>'
        f'</div>'
        f'<script>setTimeout(function(){{var t=document.getElementById("toast");if(t)t.style.display="none";}},2000);</script>'
    ) if error else ""

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboard - Monitoreo Salas</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f0f4f8; color: #1a2332; }}
    header {{ background: #1a2332; color: #fff; padding: 14px 24px;
              display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
    h1 {{ margin: 0; font-size: 20px; letter-spacing: .3px; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 20px 24px; }}
    /* Filters */
    form.filters {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end;
                    background: #fff; border: 1px solid #d1dae6; border-radius: 8px;
                    padding: 14px 16px; margin-bottom: 18px; }}
    .filter-group {{ display: flex; flex-direction: column; gap: 4px; }}
    label {{ font-size: 11px; font-weight: 700; color: #475569; text-transform: uppercase; }}
    input[type=date], input[type=text] {{ padding: 8px 10px; border: 1px solid #cbd5e1;
      border-radius: 6px; font-size: 13px; background: #fff; min-width: 130px; }}
    .btn {{ padding: 9px 16px; border: 0; border-radius: 6px; font-size: 13px;
            font-weight: 700; cursor: pointer; text-decoration: none; display: inline-block; }}
    .btn-primary {{ background: #176b87; color: #fff; }}
    .btn-primary:hover {{ background: #135d75; }}
    .btn-excel {{ background: #16a34a; color: #fff; }}
    .btn-excel:hover {{ background: #15803d; }}
    .btn-logout {{ background: #475569; color: #fff; font-size: 13px; }}
    /* Metrics */
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                gap: 12px; margin-bottom: 20px; }}
    .metric {{ background: #fff; border: 1px solid #d1dae6; border-radius: 8px; padding: 14px 16px; }}
    .metric span {{ display: block; font-size: 11px; font-weight: 700; color: #64748b;
                    text-transform: uppercase; margin-bottom: 4px; }}
    .metric strong {{ display: block; font-size: 26px; color: #1a2332; }}
    .metric small {{ font-size: 11px; color: #94a3b8; }}
    /* Sections */
    section {{ background: #fff; border: 1px solid #d1dae6; border-radius: 8px;
               padding: 16px; margin-bottom: 18px; overflow-x: auto; }}
    h2 {{ font-size: 15px; font-weight: 700; margin: 0 0 12px; color: #1a2332; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th {{ background: #1a2332; color: #fff; padding: 9px 10px; text-align: left;
          font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }}
    td {{ border-bottom: 1px solid #e8edf3; padding: 8px 10px; color: #374151; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #f8fafc; }}
    @media(max-width:700px) {{ form.filters {{ flex-direction: column; }}
      .metrics {{ grid-template-columns: 1fr 1fr; }} main {{ padding: 14px; }} }}
    /* Toast */
    .toast {{ position: fixed; bottom: 24px; right: 24px; z-index: 999;
              display: flex; align-items: center; gap: 12px;
              background: #b91c1c; color: #fff; font-size: 14px; font-weight: 600;
              padding: 14px 18px; border-radius: 8px;
              box-shadow: 0 4px 16px rgba(0,0,0,.25); max-width: 400px; }}
    .toast button {{ background: none; border: none; color: #fff; font-size: 18px;
                     cursor: pointer; line-height: 1; padding: 0; flex-shrink: 0; }}
  </style>
</head>
<body>
  {toast_block}
  <header>
    <h1>Monitoreo Salas</h1>
    <form method="post" action="/v1/dashboard/logout">
      <button class="btn btn-logout" type="submit">Cerrar sesion</button>
    </form>
  </header>
  <main>
    <form class="filters" method="get" action="/v1/dashboard">
      <input type="hidden" name="filtrado" value="1">
      <div class="filter-group">
        <label for="fecha_inicio">Desde</label>
        <input id="fecha_inicio" name="fecha_inicio" type="date" value="{filter_start}">
      </div>
      <div class="filter-group">
        <label for="fecha_fin">Hasta</label>
        <input id="fecha_fin" name="fecha_fin" type="date" value="{filter_end}">
      </div>
      <div class="filter-group">
        <label for="sala_codigo">Sala</label>
        <input id="sala_codigo" name="sala_codigo" type="text"
               value="{filter_room}" placeholder="SALA-01" style="max-width:110px">
      </div>
      <div class="filter-group" style="justify-content:flex-end">
        <label>&nbsp;</label>
        <div style="display:flex;gap:8px">
          <button class="btn btn-primary" type="submit">Filtrar</button>
          <a class="btn btn-excel" href="/v1/reports/excel{query}">Exportar Excel</a>
        </div>
      </div>
    </form>

    <div class="metrics">
      {_metric("Salas",    summary["salas"])}
      {_metric("Equipos",  summary["equipos"])}
      {_metric("Capturas", summary["capturas"])}
      {_metric("Apps",     summary["registros_apps"])}
      {_metric("Sin apps", summary["capturas_sin_apps"])}
      {_metric("Total registros", summary["total_registros"])}
    </div>

    <section>
      <h2>Salas</h2>
      <table>
        <thead><tr>
          <th>Sala</th><th>Equipos</th><th>Capturas</th>
          <th>Apps</th><th>Sin apps</th><th>Top app</th>
          <th>Primera captura</th><th>Ultima captura</th>
        </tr></thead>
        <tbody>{room_rows}{empty_rooms}</tbody>
      </table>
    </section>

    <section>
      <h2>Equipos</h2>
      <table>
        <thead><tr>
          <th>Equipo</th><th>Sala</th><th>Capturas</th>
          <th>Apps</th><th>Sin apps</th><th>Top app</th>
          <th>Primera captura</th><th>Ultima captura</th>
        </tr></thead>
        <tbody>{machine_rows}{empty_machines}</tbody>
      </table>
    </section>

    <section>
      <h2>Aplicaciones</h2>
      <table>
        <thead><tr>
          <th>Proceso</th><th>Registros</th><th>Salas</th>
          <th>Equipos</th><th>Memoria promedio MB</th>
        </tr></thead>
        <tbody>{app_rows}{empty_apps}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""


def _query_string(filters: ReportFilters) -> str:
    params: list[str] = []
    if filters.fecha_inicio:
        params.append(f"fecha_inicio={filters.fecha_inicio.isoformat()}")
    if filters.fecha_fin:
        params.append(f"fecha_fin={filters.fecha_fin.isoformat()}")
    if filters.sala_codigo:
        params.append(f"sala_codigo={html.escape(filters.sala_codigo)}")
    return "?" + "&".join(params) if params else ""


def _metric(label: str, value: object) -> str:
    return (
        f'<div class="metric">'
        f'<span>{html.escape(label)}</span>'
        f'<strong>{html.escape(str(value))}</strong>'
        f'</div>'
    )


def _room_row(row: dict) -> str:
    values = [
        row.get("sala_codigo", ""),
        row.get("equipos", ""),
        row.get("capturas", ""),
        row.get("registros_apps", ""),
        row.get("capturas_sin_apps", ""),
        row.get("app_mas_frecuente", ""),
        _short_dt(row.get("primera_captura")),
        _short_dt(row.get("ultima_captura")),
    ]
    return "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in values) + "</tr>"


def _machine_row(row: dict) -> str:
    values = [
        row.get("hostname", ""),
        row.get("sala_codigo", ""),
        row.get("capturas", ""),
        row.get("registros_apps", ""),
        row.get("capturas_sin_apps", ""),
        row.get("app_mas_frecuente", ""),
        _short_dt(row.get("primera_captura")),
        _short_dt(row.get("ultima_captura")),
    ]
    return "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in values) + "</tr>"


def _app_row(row: dict) -> str:
    values = [
        row.get("nombre_proceso", ""),
        row.get("registros", ""),
        row.get("salas", ""),
        row.get("equipos", ""),
        row.get("memoria_mb_promedio", ""),
    ]
    return "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in values) + "</tr>"


def _short_dt(value: object) -> str:
    """Trim ISO datetime to 'YYYY-MM-DD HH:MM' for compact table display."""
    s = str(value or "")
    if len(s) >= 16:
        return s[:16].replace("T", " ")
    return s
