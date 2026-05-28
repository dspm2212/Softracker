"""
Tests for agente.envio module.

Covers: MOCK_API bypass, HTTP 200/201 success, non-2xx failure, network
exceptions, retry count, exponential backoff, request headers and form
data, and procesar_pendientes flow (success, failure, mixed).

Author: Daniel Perez
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from pytest_mock import MockerFixture

from agente.envio import enviar_archivo, procesar_pendientes


# ── shared helpers ────────────────────────────────────────────────────────────


@pytest.fixture
def parquet_file(tmp_path: Path) -> Path:
    """A fake Parquet file sitting in pendientes/."""
    p = tmp_path / "pendientes" / "2026-05-27_PC-01.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"PAR1fake")
    return p


def _response(status: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    return r


def _cfg(datos_dir: Path, reintentos: int = 3) -> dict:
    return {
        "api_url": "http://test.server/v1/upload",
        "token": "TEST-TOKEN",
        "sala_codigo": "SALA-01",
        "timeout_envio_seg": 10,
        "reintentos_envio": reintentos,
        "datos_dir": str(datos_dir),
    }


def _patch_http(mocker: MockerFixture, responses: list) -> MagicMock:
    """Patch requests.post with a sequence of responses and suppress sleep."""
    mocker.patch("agente.envio.time.sleep")
    return mocker.patch("agente.envio.requests.post", side_effect=responses)


# ── MOCK_API mode ─────────────────────────────────────────────────────────────


class TestMockApiMode:
    def test_returns_true_immediately(
        self, parquet_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        assert enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, 3) is True

    def test_never_calls_requests(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        mock_post = mocker.patch("agente.envio.requests.post")
        enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, 3)
        mock_post.assert_not_called()


# ── HTTP success / failure ────────────────────────────────────────────────────


class TestHttpSend:
    def test_http_200_returns_true(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        _patch_http(mocker, [_response(200)])
        assert enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, 0) is True

    def test_http_201_returns_true(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        _patch_http(mocker, [_response(201)])
        assert enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, 0) is True

    def test_http_500_returns_false(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        _patch_http(mocker, [_response(500), _response(500)])
        assert enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, 1) is False

    def test_connection_error_returns_false(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        mocker.patch("agente.envio.time.sleep")
        mocker.patch(
            "agente.envio.requests.post",
            side_effect=requests.ConnectionError("refused"),
        )
        assert enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, 1) is False

    def test_timeout_error_returns_false(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        mocker.patch("agente.envio.time.sleep")
        mocker.patch(
            "agente.envio.requests.post",
            side_effect=requests.Timeout("timed out"),
        )
        assert enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, 0) is False

    def test_success_on_second_attempt(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        _patch_http(mocker, [_response(503), _response(200)])
        assert enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, 3) is True


# ── request headers and form data ────────────────────────────────────────────


class TestRequestContents:
    def _send_one(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
        token: str = "MY-TOKEN",
        sala: str = "SALA-05",
    ) -> MagicMock:
        monkeypatch.delenv("MOCK_API", raising=False)
        mock_post = mocker.patch(
            "agente.envio.requests.post", return_value=_response(200)
        )
        mocker.patch("agente.envio.time.sleep")
        enviar_archivo(parquet_file, "http://x.com", token, sala, 10, 0)
        return mock_post

    def test_auth_token_header(
        self, parquet_file: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_post = self._send_one(parquet_file, mocker, monkeypatch, token="MY-TOKEN")
        assert mock_post.call_args.kwargs["headers"]["X-Auth-Token"] == "MY-TOKEN"

    def test_sala_codigo_in_form_data(
        self, parquet_file: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_post = self._send_one(parquet_file, mocker, monkeypatch, sala="SALA-05")
        assert mock_post.call_args.kwargs["data"]["sala_codigo"] == "SALA-05"

    def test_file_sent_as_multipart(
        self, parquet_file: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_post = self._send_one(parquet_file, mocker, monkeypatch)
        assert "archivo" in mock_post.call_args.kwargs["files"]

    def test_correct_url_used(
        self, parquet_file: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        mock_post = mocker.patch(
            "agente.envio.requests.post", return_value=_response(200)
        )
        mocker.patch("agente.envio.time.sleep")
        enviar_archivo(parquet_file, "http://real.server/v1/upload", "tok", "S", 10, 0)
        assert mock_post.call_args.args[0] == "http://real.server/v1/upload"


# ── retry count and exponential backoff ──────────────────────────────────────


class TestRetriesAndBackoff:
    def test_total_attempts_is_reintentos_plus_one(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        mock_post = _patch_http(mocker, [_response(500)] * 4)
        enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, reintentos=3)
        assert mock_post.call_count == 4  # 1 initial + 3 retries

    def test_no_sleep_before_first_attempt(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        mocker.patch("agente.envio.requests.post", return_value=_response(200))
        mock_sleep = mocker.patch("agente.envio.time.sleep")
        enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, reintentos=3)
        mock_sleep.assert_not_called()

    def test_backoff_values_are_2_4_8(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        mock_sleep = mocker.patch("agente.envio.time.sleep")
        mocker.patch("agente.envio.requests.post", return_value=_response(500))
        enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, reintentos=3)
        assert [c.args[0] for c in mock_sleep.call_args_list] == [2, 4, 8]

    def test_stops_after_first_success(
        self,
        parquet_file: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        mock_post = _patch_http(mocker, [_response(500), _response(200), _response(200)])
        enviar_archivo(parquet_file, "http://x.com", "tok", "S", 10, reintentos=3)
        assert mock_post.call_count == 2  # stopped at attempt 2


# ── procesar_pendientes ───────────────────────────────────────────────────────


class TestProcesarPendientes:
    def test_empty_pending_returns_zero_stats(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        assert procesar_pendientes(tmp_path, _cfg(tmp_path)) == {
            "enviados": 0, "fallidos": 0, "total": 0,
        }

    def test_success_moves_file_and_counts_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        p = tmp_path / "pendientes" / "2026-05-27_PC.parquet"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"fake")

        stats = procesar_pendientes(tmp_path, _cfg(tmp_path))

        assert not p.exists()
        assert stats == {"enviados": 1, "fallidos": 0, "total": 1}

    def test_failure_keeps_file_and_counts_it(
        self,
        tmp_path: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        mocker.patch("agente.envio.requests.post", return_value=_response(500))
        mocker.patch("agente.envio.time.sleep")
        p = tmp_path / "pendientes" / "2026-05-27_PC.parquet"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"fake")

        stats = procesar_pendientes(tmp_path, _cfg(tmp_path, reintentos=0))

        assert p.exists()
        assert stats == {"enviados": 0, "fallidos": 1, "total": 1}

    def test_mixed_results_counted_correctly(
        self,
        tmp_path: Path,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MOCK_API", raising=False)
        _patch_http(mocker, [_response(200), _response(500), _response(201)])

        pending = tmp_path / "pendientes"
        pending.mkdir(parents=True)
        for name in ("2026-05-25_PC.parquet", "2026-05-26_PC.parquet", "2026-05-27_PC.parquet"):
            (pending / name).write_bytes(b"fake")

        stats = procesar_pendientes(tmp_path, _cfg(tmp_path, reintentos=0))

        assert stats["total"] == 3
        assert stats["enviados"] == 2
        assert stats["fallidos"] == 1

    def test_sent_files_land_in_enviados(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        p = tmp_path / "pendientes" / "2026-05-27_PC.parquet"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"fake")

        procesar_pendientes(tmp_path, _cfg(tmp_path))

        enviados = list((tmp_path / "enviados").rglob("*.parquet"))
        assert len(enviados) == 1
        assert enviados[0].name == "2026-05-27_PC.parquet"
