-- =============================================================
-- analisis.sql
-- Example analysis queries for the Softracker monitoring system.
--
-- Sections:
--   1. Presence-based queries  (which apps were open)
--   2. Time-based queries      (how long apps were in the foreground)
--   3. Coverage and health     (which machines are reporting)
--   4. Security and compliance (unauthorised apps, error logs)
--
-- All queries target the monitoreo_apps schema.  Run with:
--   psql -d universidad -f sql/analisis.sql
--
-- Author: Daniel Santiago Perez Madera <dsperezm@udistrital.edu.co>
-- =============================================================

SET search_path TO monitoreo_apps, public;


-- =============================================================
-- 1. PRESENCE-BASED QUERIES
-- =============================================================

-- 1a) Top apps detected in the last 7 days, grouped by sala.
SELECT
    s.codigo          AS sala,
    a.nombre_proceso,
    COUNT(*)          AS detecciones,
    COUNT(DISTINCT m.equipo_id) AS equipos_unicos
FROM aplicaciones_detectadas a
JOIN muestras m ON m.muestra_id = a.muestra_id
JOIN equipos  e ON e.equipo_id  = m.equipo_id
JOIN salas    s ON s.sala_id    = e.sala_id
WHERE m.timestamp_servidor >= NOW() - INTERVAL '7 days'
GROUP BY s.codigo, a.nombre_proceso
ORDER BY s.codigo, detecciones DESC;


-- 1b) Unauthorised applications detected in the last 7 days.
SELECT
    s.codigo          AS sala,
    e.hostname,
    a.nombre_proceso,
    COUNT(*)          AS veces
FROM aplicaciones_detectadas a
JOIN muestras m  ON m.muestra_id = a.muestra_id
JOIN equipos  e  ON e.equipo_id  = m.equipo_id
JOIN salas    s  ON s.sala_id    = e.sala_id
LEFT JOIN catalogo_apps c
         ON LOWER(a.nombre_proceso) = LOWER(c.patron_nombre)
WHERE (c.autorizada = FALSE OR c.cat_app_id IS NULL)
  AND m.timestamp_servidor >= NOW() - INTERVAL '7 days'
GROUP BY s.codigo, e.hostname, a.nombre_proceso
ORDER BY veces DESC;


-- 1c) Application usage by category (last 30 days).
SELECT
    c.categoria,
    COUNT(*)                    AS detecciones,
    COUNT(DISTINCT m.equipo_id) AS equipos_unicos
FROM aplicaciones_detectadas a
JOIN muestras m ON m.muestra_id = a.muestra_id
JOIN catalogo_apps c
     ON LOWER(a.nombre_proceso) = LOWER(c.patron_nombre)
WHERE m.timestamp_servidor >= NOW() - INTERVAL '30 days'
GROUP BY c.categoria
ORDER BY detecciones DESC;


-- =============================================================
-- 2. TIME-BASED QUERIES  (require foreground-tracking agent)
-- =============================================================

-- 2a) Total foreground hours per app per sala, today.
--     Only rows with es_app_activa = TRUE carry meaningful time data.
SELECT
    s.codigo                                         AS sala,
    a.nombre_proceso,
    ROUND(SUM(a.duracion_activa_seg) / 3600.0, 2)    AS horas_activas,
    COUNT(DISTINCT m.equipo_id)                      AS equipos_unicos
FROM aplicaciones_detectadas a
JOIN muestras m ON m.muestra_id = a.muestra_id
JOIN equipos  e ON e.equipo_id  = m.equipo_id
JOIN salas    s ON s.sala_id    = e.sala_id
WHERE a.es_app_activa = TRUE
  AND m.timestamp_servidor >= CURRENT_DATE
GROUP BY s.codigo, a.nombre_proceso
ORDER BY s.codigo, horas_activas DESC;


-- 2b) Average active minutes per app per machine in the last 7 days.
SELECT
    s.codigo                                           AS sala,
    e.hostname,
    a.nombre_proceso,
    ROUND(AVG(a.duracion_activa_seg) / 60.0, 1)        AS avg_minutos_por_muestra,
    ROUND(SUM(a.duracion_activa_seg) / 60.0, 1)        AS total_minutos
FROM aplicaciones_detectadas a
JOIN muestras m ON m.muestra_id = a.muestra_id
JOIN equipos  e ON e.equipo_id  = m.equipo_id
JOIN salas    s ON s.sala_id    = e.sala_id
WHERE a.es_app_activa = TRUE
  AND m.timestamp_servidor >= NOW() - INTERVAL '7 days'
GROUP BY s.codigo, e.hostname, a.nombre_proceso
ORDER BY total_minutos DESC;


-- 2c) Daily active time trend for a specific app (replace 'chrome').
SELECT
    DATE(m.timestamp_servidor)                         AS fecha,
    s.codigo                                           AS sala,
    ROUND(SUM(a.duracion_activa_seg) / 3600.0, 2)      AS horas_activas
FROM aplicaciones_detectadas a
JOIN muestras m ON m.muestra_id = a.muestra_id
JOIN equipos  e ON e.equipo_id  = m.equipo_id
JOIN salas    s ON s.sala_id    = e.sala_id
WHERE LOWER(a.nombre_proceso) = 'chrome'
  AND a.es_app_activa = TRUE
  AND m.timestamp_servidor >= NOW() - INTERVAL '30 days'
GROUP BY fecha, s.codigo
ORDER BY fecha DESC, s.codigo;


-- =============================================================
-- 3. COVERAGE AND HEALTH
-- =============================================================

-- 3a) Contact status of all active machines.
SELECT * FROM v_equipos_estado
ORDER BY sala, hostname;

-- 3b) Machines that have NOT reported in the last hour (possible outage).
SELECT * FROM v_equipos_estado
WHERE estado <> 'ok'
ORDER BY sala, hostname;

-- 3c) Coverage percentage per sala in the last 24 hours.
SELECT
    s.codigo                                                          AS sala,
    COUNT(*) FILTER (WHERE e.ultimo_reporte >= NOW() - INTERVAL '24 hours') AS reportando,
    COUNT(*)                                                          AS total,
    ROUND(
        100.0
        * COUNT(*) FILTER (WHERE e.ultimo_reporte >= NOW() - INTERVAL '24 hours')
        / NULLIF(COUNT(*), 0),
        1
    )                                                                 AS pct_cobertura
FROM equipos e
JOIN salas s ON s.sala_id = e.sala_id
WHERE e.activo
GROUP BY s.codigo
ORDER BY pct_cobertura;

-- 3d) Machines that have never reported (possibly misconfigured agents).
SELECT s.codigo AS sala, e.hostname, e.creado_en
FROM equipos e
JOIN salas s ON s.sala_id = e.sala_id
WHERE e.primer_reporte IS NULL
ORDER BY e.creado_en DESC;


-- =============================================================
-- 4. SECURITY AND COMPLIANCE
-- =============================================================

-- 4a) Recent ingestion errors (bad tokens, invalid JSON, etc.).
SELECT
    timestamp_utc,
    ip_origen,
    motivo,
    LEFT(detalles, 80) AS detalles
FROM log_ingesta_errores
WHERE timestamp_utc >= NOW() - INTERVAL '1 day'
ORDER BY timestamp_utc DESC
LIMIT 100;

-- 4b) IPs with repeated authentication failures (possible intrusion).
SELECT
    ip_origen,
    COUNT(*) AS intentos_fallidos,
    MIN(timestamp_utc) AS primer_intento,
    MAX(timestamp_utc) AS ultimo_intento
FROM log_ingesta_errores
WHERE motivo = 'token_invalido'
  AND timestamp_utc >= NOW() - INTERVAL '7 days'
GROUP BY ip_origen
HAVING COUNT(*) > 5
ORDER BY intentos_fallidos DESC;

-- 4c) Data retention cleanup (run monthly via cron or pg_cron).
-- DELETE FROM muestras
--  WHERE timestamp_servidor < NOW() - INTERVAL '90 days';
-- VACUUM ANALYZE muestras;
