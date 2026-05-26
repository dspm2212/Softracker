-- =============================================================
-- 02_seed.sql
-- Initial data for the Softracker monitoring system.
--
-- Inserts sample rooms (salas), scheduling fringes (franjas),
-- an application catalogue, and generates one authentication
-- token per room.  Token plaintext values are printed via
-- RAISE NOTICE and must be recorded immediately; only the
-- SHA-256 hash is stored in the database.
--
-- Usage:
--   psql -h <host> -U <admin> -d universidad -f sql/02_seed.sql 2>&1 | grep TOKEN
--
-- Expected output (one line per sala):
--   NOTICE:  TOKEN for SALA-01: aBcDeFgHiJkLmN...
--
-- Author: Daniel Santiago Perez Madera <dsperezm@udistrital.edu.co>
-- =============================================================

SET search_path TO monitoreo_apps, public;

-- --- Rooms (adjust codes and names to match the real layout) -----------
INSERT INTO salas (codigo, nombre, ubicacion) VALUES
  ('SALA-01', 'Sala 1', 'Edificio A - Piso 1'),
  ('SALA-02', 'Sala 2', 'Edificio A - Piso 1'),
  ('SALA-03', 'Sala 3', 'Edificio A - Piso 2'),
  ('SALA-04', 'Sala 4', 'Edificio A - Piso 2'),
  ('SALA-05', 'Sala 5', 'Edificio B - Piso 1')
ON CONFLICT (codigo) DO NOTHING;

-- --- Scheduling fringes (informational, not enforced by the agent) ----
INSERT INTO franjas_recoleccion (nombre, dia_semana, hora_inicio, hora_fin, intervalo_min) VALUES
  ('manana_lv', 0, '06:00', '12:00', 25),
  ('manana_lv', 1, '06:00', '12:00', 25),
  ('manana_lv', 2, '06:00', '12:00', 25),
  ('manana_lv', 3, '06:00', '12:00', 25),
  ('manana_lv', 4, '06:00', '12:00', 25),
  ('tarde_lv',  0, '13:00', '22:00', 25),
  ('tarde_lv',  1, '13:00', '22:00', 25),
  ('tarde_lv',  2, '13:00', '22:00', 25),
  ('tarde_lv',  3, '13:00', '22:00', 25),
  ('tarde_lv',  4, '13:00', '22:00', 25),
  ('sabado',    5, '07:00', '13:00', 30)
ON CONFLICT DO NOTHING;

-- --- Application catalogue (extend as needed) -------------------------
INSERT INTO catalogo_apps (patron_nombre, nombre_amigable, categoria, autorizada) VALUES
  ('Code',      'Visual Studio Code',   'IDE',       TRUE),
  ('devenv',    'Visual Studio',        'IDE',       TRUE),
  ('pycharm64', 'PyCharm',             'IDE',       TRUE),
  ('matlab',    'MATLAB',              'Ingenieria', TRUE),
  ('chrome',    'Google Chrome',       'Navegador',  TRUE),
  ('firefox',   'Mozilla Firefox',     'Navegador',  TRUE),
  ('opera',     'Opera',               'Navegador',  TRUE),
  ('msedge',    'Microsoft Edge',      'Navegador',  TRUE),
  ('WINWORD',   'Microsoft Word',      'Ofimatica',  TRUE),
  ('EXCEL',     'Microsoft Excel',     'Ofimatica',  TRUE),
  ('POWERPNT',  'Microsoft PowerPoint','Ofimatica',  TRUE),
  ('steam',     'Steam',               'Juegos',     FALSE),
  ('discord',   'Discord',             'Chat',       FALSE)
ON CONFLICT (patron_nombre) DO NOTHING;

-- --- Generate one token per sala -------------------------------------
-- Each token is a 48-character random string.  The plaintext value is
-- displayed once via RAISE NOTICE.  Copy each token to the config.json
-- of every machine in the corresponding sala.
DO $$
DECLARE
    sala_rec    RECORD;
    nuevo_token TEXT;
BEGIN
    FOR sala_rec IN SELECT sala_id, codigo FROM salas WHERE activa LOOP
        nuevo_token := encode(gen_random_bytes(36), 'base64');
        nuevo_token := translate(nuevo_token, '+/=', '-_');

        INSERT INTO tokens_ingesta (sala_id, descripcion, token_hash)
        VALUES (
            sala_rec.sala_id,
            'Token for ' || sala_rec.codigo,
            encode(digest(nuevo_token, 'sha256'), 'hex')
        )
        ON CONFLICT DO NOTHING;

        RAISE NOTICE 'TOKEN for %: %', sala_rec.codigo, nuevo_token;
    END LOOP;
END$$;
