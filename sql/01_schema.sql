-- =============================================================
-- 01_schema.sql
-- PostgreSQL schema for the Softracker monitoring system.
--
-- Defines tables for rooms (salas), client machines (equipos),
-- per-sala authentication tokens, monitoring samples (muestras),
-- and detected applications (aplicaciones_detectadas).
--
-- The aplicaciones_detectadas table includes two columns that
-- support the active-time tracking feature introduced in v2:
--   duracion_activa_seg  -- seconds the process held the foreground
--   es_app_activa        -- true when the process accumulated any
--                           foreground time in the reporting interval
--
-- Auxiliary tables cover a soft-auth error log, an optional app
-- catalogue for friendly names, and scheduling fringe metadata.
--
-- Author: Daniel Santiago Perez Madera <dsperezm@udistrital.edu.co>
-- =============================================================

CREATE SCHEMA IF NOT EXISTS monitoreo_apps;
SET search_path TO monitoreo_apps, public;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -------------------------------------------------------------
-- salas
-- One row per physical computer lab (room).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS salas (
    sala_id   SERIAL       PRIMARY KEY,
    codigo    VARCHAR(20)  NOT NULL UNIQUE,
    nombre    VARCHAR(100) NOT NULL,
    ubicacion VARCHAR(150),
    activa    BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- -------------------------------------------------------------
-- equipos
-- One row per client machine.  Auto-registered by the API on
-- first contact; no pre-provisioning required.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equipos (
    equipo_id      SERIAL       PRIMARY KEY,
    sala_id        INTEGER      NOT NULL REFERENCES salas(sala_id) ON DELETE CASCADE,
    hostname       VARCHAR(100) NOT NULL,
    ip             INET,
    mac            MACADDR,
    numero_equipo  INTEGER,
    activo         BOOLEAN      NOT NULL DEFAULT TRUE,
    primer_reporte TIMESTAMP,
    ultimo_reporte TIMESTAMP,
    creado_en      TIMESTAMP    NOT NULL DEFAULT NOW(),
    UNIQUE (sala_id, hostname)
);

CREATE INDEX IF NOT EXISTS idx_equipos_sala
    ON equipos(sala_id) WHERE activo;

CREATE INDEX IF NOT EXISTS idx_equipos_hostname
    ON equipos(hostname);

-- -------------------------------------------------------------
-- tokens_ingesta
-- One shared token per sala (stored as SHA-256 hash only).
-- Using a sala-level token simplifies cloned-image deployments:
-- every machine in the same room uses the same token.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tokens_ingesta (
    token_id    SERIAL       PRIMARY KEY,
    sala_id     INTEGER      REFERENCES salas(sala_id) ON DELETE CASCADE,
    descripcion VARCHAR(150),
    token_hash  TEXT         NOT NULL UNIQUE,
    activo      BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_en   TIMESTAMP    NOT NULL DEFAULT NOW(),
    ultimo_uso  TIMESTAMP,
    expira_en   TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tokens_hash
    ON tokens_ingesta(token_hash) WHERE activo;

-- -------------------------------------------------------------
-- muestras
-- One row per agent transmission.  dedupe_key prevents duplicate
-- inserts when the agent retransmits a buffered sample.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS muestras (
    muestra_id         BIGSERIAL   PRIMARY KEY,
    equipo_id          INTEGER     NOT NULL REFERENCES equipos(equipo_id) ON DELETE CASCADE,
    timestamp_local    TIMESTAMPTZ NOT NULL,
    timestamp_servidor TIMESTAMP   NOT NULL DEFAULT NOW(),
    usuario_activo     VARCHAR(100),
    sesion_activa      BOOLEAN,
    dedupe_key         VARCHAR(64) NOT NULL,
    UNIQUE (equipo_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_muestras_equipo_ts
    ON muestras(equipo_id, timestamp_local DESC);

CREATE INDEX IF NOT EXISTS idx_muestras_servidor
    ON muestras(timestamp_servidor DESC);

-- -------------------------------------------------------------
-- aplicaciones_detectadas
-- One row per application per sample.
--   duracion_activa_seg: foreground seconds during the interval.
--   es_app_activa:       true when the process was ever in focus.
-- Both columns default to 0 / FALSE for backward compatibility
-- with agents that do not support foreground tracking.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aplicaciones_detectadas (
    app_id              BIGSERIAL    PRIMARY KEY,
    muestra_id          BIGINT       NOT NULL REFERENCES muestras(muestra_id) ON DELETE CASCADE,
    nombre_proceso      VARCHAR(255) NOT NULL,
    nombre_ejecutable   VARCHAR(255),
    ruta_ejecutable     TEXT,
    titulo_ventana      TEXT,
    pid                 INTEGER,
    memoria_mb          NUMERIC(10,2),
    duracion_activa_seg NUMERIC(10,2) NOT NULL DEFAULT 0,
    es_app_activa       BOOLEAN       NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_apps_muestra
    ON aplicaciones_detectadas(muestra_id);

CREATE INDEX IF NOT EXISTS idx_apps_nombre
    ON aplicaciones_detectadas(nombre_proceso);

-- Partial index: only index rows with foreground data for fast
-- time-based queries without scanning inactive rows.
CREATE INDEX IF NOT EXISTS idx_apps_activa
    ON aplicaciones_detectadas(muestra_id, nombre_proceso)
    WHERE es_app_activa = TRUE;

-- -------------------------------------------------------------
-- catalogo_apps
-- Optional lookup table for friendly display names and categories.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalogo_apps (
    cat_app_id      SERIAL       PRIMARY KEY,
    patron_nombre   VARCHAR(255) NOT NULL UNIQUE,
    nombre_amigable VARCHAR(150) NOT NULL,
    categoria       VARCHAR(50),
    autorizada      BOOLEAN DEFAULT TRUE
);

-- -------------------------------------------------------------
-- franjas_recoleccion
-- Informational metadata about the expected reporting schedule.
-- The agent does not query this table; it is used by dashboards.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS franjas_recoleccion (
    franja_id     SERIAL   PRIMARY KEY,
    nombre        VARCHAR(50)  NOT NULL,
    dia_semana    SMALLINT     NOT NULL,
    hora_inicio   TIME         NOT NULL,
    hora_fin      TIME         NOT NULL,
    intervalo_min SMALLINT     NOT NULL DEFAULT 25,
    activa        BOOLEAN DEFAULT TRUE
);

-- -------------------------------------------------------------
-- log_ingesta_errores
-- Audit log for rejected ingestion attempts (bad tokens, etc.).
-- Useful for detecting misconfigured agents or intrusion attempts.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS log_ingesta_errores (
    log_id        BIGSERIAL  PRIMARY KEY,
    timestamp_utc TIMESTAMP  NOT NULL DEFAULT NOW(),
    ip_origen     INET,
    motivo        VARCHAR(50),
    detalles      TEXT
);

-- -------------------------------------------------------------
-- Views
-- -------------------------------------------------------------

-- v_equipos_estado: live contact-status of every active machine.
CREATE OR REPLACE VIEW v_equipos_estado AS
SELECT
    s.codigo                      AS sala,
    e.hostname,
    e.ultimo_reporte,
    NOW() - e.ultimo_reporte      AS tiempo_sin_reportar,
    CASE
        WHEN e.ultimo_reporte IS NULL                          THEN 'nunca_reporto'
        WHEN e.ultimo_reporte < NOW() - INTERVAL '1 hour'     THEN 'sin_contacto'
        WHEN e.ultimo_reporte < NOW() - INTERVAL '30 minutes' THEN 'atrasado'
        ELSE 'ok'
    END                           AS estado
FROM equipos e
JOIN salas s ON s.sala_id = e.sala_id
WHERE e.activo;

-- v_apps_top_dia: most-detected applications in the last 24 hours,
-- enriched with total foreground time.
CREATE OR REPLACE VIEW v_apps_top_dia AS
SELECT
    s.codigo                                             AS sala,
    a.nombre_proceso,
    COUNT(*)                                             AS detecciones,
    COUNT(DISTINCT m.equipo_id)                          AS equipos_unicos,
    ROUND(SUM(a.duracion_activa_seg) / 3600.0, 2)        AS horas_activas_total
FROM aplicaciones_detectadas a
JOIN muestras m ON m.muestra_id = a.muestra_id
JOIN equipos  e ON e.equipo_id  = m.equipo_id
JOIN salas    s ON s.sala_id    = e.sala_id
WHERE m.timestamp_servidor >= NOW() - INTERVAL '1 day'
GROUP BY s.codigo, a.nombre_proceso
ORDER BY s.codigo, detecciones DESC;
