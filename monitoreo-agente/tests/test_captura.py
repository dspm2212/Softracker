"""
Tests for agente.captura module.

Mocks win32gui, win32process, and psutil to test the capture logic
independently of real running processes.

Author: Daniel Perez
"""

from unittest.mock import MagicMock

import psutil
import pytest
from pytest_mock import MockerFixture

from agente.captura import (
    _build_app_list,
    _extract_process_info,
    _safe_exe_path,
    _safe_memory_mb,
    capturar_apps_en_uso,
    construir_muestra,
)


def _make_proc(
    username: str = "DESKTOP\\estudiante",
    name: str = "Code.exe",
    exe: str = "C:\\fake\\Code.exe",
    rss_mb: float = 100.0,
) -> MagicMock:
    """Build a mock psutil.Process-like object with preset attribute values."""
    proc = MagicMock()
    proc.username.return_value = username
    proc.name.return_value = name
    proc.exe.return_value = exe
    proc.memory_info.return_value.rss = int(rss_mb * 1024 * 1024)
    return proc


# ── _safe_exe_path / _safe_memory_mb ─────────────────────────────────────────

class TestSafeHelpers:
    def test_exe_path_returns_string(self) -> None:
        proc = _make_proc(exe="C:\\Windows\\notepad.exe")
        assert _safe_exe_path(proc) == "C:\\Windows\\notepad.exe"

    def test_exe_path_none_on_access_denied(self) -> None:
        proc = _make_proc()
        proc.exe.side_effect = psutil.AccessDenied(pid=1)
        assert _safe_exe_path(proc) is None

    def test_exe_path_none_on_no_such_process(self) -> None:
        proc = _make_proc()
        proc.exe.side_effect = psutil.NoSuchProcess(pid=1)
        assert _safe_exe_path(proc) is None

    def test_memory_rounded_to_2_decimals(self) -> None:
        proc = _make_proc(rss_mb=123.456789)
        assert _safe_memory_mb(proc) == round(123.456789, 2)

    def test_memory_zero_on_access_denied(self) -> None:
        proc = _make_proc()
        proc.memory_info.side_effect = psutil.AccessDenied(pid=1)
        assert _safe_memory_mb(proc) == 0.0

    def test_memory_zero_on_no_such_process(self) -> None:
        proc = _make_proc()
        proc.memory_info.side_effect = psutil.NoSuchProcess(pid=1)
        assert _safe_memory_mb(proc) == 0.0


# ── _extract_process_info ─────────────────────────────────────────────────────

class TestExtractProcessInfo:
    def test_returns_dict_for_current_user(self, mocker: MockerFixture) -> None:
        mocker.patch("agente.captura.psutil.Process", return_value=_make_proc(rss_mb=100))

        result = _extract_process_info(42, "VS Code", "estudiante")

        assert result is not None
        assert result["pid"] == 42
        assert result["nombre_ejecutable"] == "Code.exe"
        assert result["nombre_proceso"] == "Code"
        assert result["titulo_ventana"] == "VS Code"
        assert result["memoria_mb"] == 100.0
        assert result["ruta_ejecutable"] == "C:\\fake\\Code.exe"

    def test_stem_strips_extension(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "agente.captura.psutil.Process",
            return_value=_make_proc(name="chrome.exe"),
        )
        result = _extract_process_info(1, "Chrome", "estudiante")
        assert result is not None
        assert result["nombre_proceso"] == "chrome"

    def test_returns_none_for_other_user(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "agente.captura.psutil.Process",
            return_value=_make_proc(username="DESKTOP\\admin"),
        )
        assert _extract_process_info(99, "Admin App", "estudiante") is None

    def test_returns_none_on_no_such_process(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "agente.captura.psutil.Process",
            side_effect=psutil.NoSuchProcess(pid=99),
        )
        assert _extract_process_info(99, "Ghost", "estudiante") is None

    def test_returns_none_on_access_denied(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "agente.captura.psutil.Process",
            side_effect=psutil.AccessDenied(pid=99),
        )
        assert _extract_process_info(99, "Locked", "estudiante") is None

    def test_username_without_domain_prefix(self, mocker: MockerFixture) -> None:
        """username() returning just 'estudiante' (no domain) is also accepted."""
        mocker.patch(
            "agente.captura.psutil.Process",
            return_value=_make_proc(username="estudiante"),
        )
        result = _extract_process_info(1, "App", "estudiante")
        assert result is not None


# ── _build_app_list ───────────────────────────────────────────────────────────

class TestBuildAppList:
    def test_deduplicates_same_pid(self, mocker: MockerFixture) -> None:
        mocker.patch("agente.captura.psutil.Process", return_value=_make_proc())
        entries = [("Window A", 100), ("Window B", 100), ("Window C", 100)]
        result = _build_app_list(entries, "estudiante")
        assert len(result) == 1

    def test_first_title_wins_for_duplicate_pid(self, mocker: MockerFixture) -> None:
        mocker.patch("agente.captura.psutil.Process", return_value=_make_proc())
        entries = [("First Title", 100), ("Second Title", 100)]
        result = _build_app_list(entries, "estudiante")
        assert result[0]["titulo_ventana"] == "First Title"

    def test_multiple_distinct_pids(self, mocker: MockerFixture) -> None:
        def fake_proc(pid: int) -> MagicMock:
            return _make_proc(name=f"app{pid}.exe")

        mocker.patch("agente.captura.psutil.Process", side_effect=fake_proc)
        entries = [("App 1", 1), ("App 2", 2), ("App 3", 3)]
        result = _build_app_list(entries, "estudiante")
        assert len(result) == 3
        assert {r["pid"] for r in result} == {1, 2, 3}

    def test_empty_entries_returns_empty(self) -> None:
        assert _build_app_list([], "estudiante") == []


# ── capturar_apps_en_uso (full pipeline, mocked win32) ───────────────────────

class TestCapturarAppsEnUso:
    def test_always_returns_list(self) -> None:
        """capturar_apps_en_uso returns a list even on a real machine in CI."""
        assert isinstance(capturar_apps_en_uso(), list)

    def test_two_pids_plus_duplicate(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two distinct PIDs + one duplicate: result must have exactly 2 entries."""
        monkeypatch.setenv("USERNAME", "estudiante")

        def fake_enum(callback, extra):  # noqa: ANN001
            callback(1001, extra)  # → pid 100
            callback(1002, extra)  # → pid 200
            callback(1003, extra)  # → pid 100 (duplicate, must be skipped)

        mocker.patch("agente.captura.win32gui.EnumWindows", side_effect=fake_enum)
        mocker.patch("agente.captura.win32gui.IsWindowVisible", return_value=True)
        mocker.patch(
            "agente.captura.win32gui.GetWindowText",
            side_effect={1001: "VS Code", 1002: "Chrome", 1003: "VS Code 2"}.get,
        )
        mocker.patch(
            "agente.captura.win32process.GetWindowThreadProcessId",
            side_effect={1001: (1, 100), 1002: (2, 200), 1003: (3, 100)}.get,
        )
        mocker.patch(
            "agente.captura.psutil.Process",
            side_effect=lambda pid: _make_proc(
                name="Code.exe" if pid == 100 else "chrome.exe", rss_mb=float(pid)
            ),
        )

        result = capturar_apps_en_uso()

        assert len(result) == 2
        assert {r["pid"] for r in result} == {100, 200}

    def test_skips_other_user_processes(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("USERNAME", "estudiante")
        mocker.patch(
            "agente.captura.win32gui.EnumWindows",
            side_effect=lambda cb, ex: cb(5001, ex),
        )
        mocker.patch("agente.captura.win32gui.IsWindowVisible", return_value=True)
        mocker.patch("agente.captura.win32gui.GetWindowText", return_value="Admin App")
        mocker.patch(
            "agente.captura.win32process.GetWindowThreadProcessId",
            return_value=(1, 999),
        )
        mocker.patch(
            "agente.captura.psutil.Process",
            return_value=_make_proc(username="DESKTOP\\admin"),
        )
        assert capturar_apps_en_uso() == []

    def test_skips_invisible_windows(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("USERNAME", "estudiante")
        mocker.patch(
            "agente.captura.win32gui.EnumWindows",
            side_effect=lambda cb, ex: cb(6001, ex),
        )
        mocker.patch("agente.captura.win32gui.IsWindowVisible", return_value=False)
        mock_proc = mocker.patch("agente.captura.psutil.Process")

        assert capturar_apps_en_uso() == []
        mock_proc.assert_not_called()

    def test_skips_empty_title_windows(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("USERNAME", "estudiante")
        mocker.patch(
            "agente.captura.win32gui.EnumWindows",
            side_effect=lambda cb, ex: cb(7001, ex),
        )
        mocker.patch("agente.captura.win32gui.IsWindowVisible", return_value=True)
        mocker.patch("agente.captura.win32gui.GetWindowText", return_value="")
        mock_proc = mocker.patch("agente.captura.psutil.Process")

        assert capturar_apps_en_uso() == []
        mock_proc.assert_not_called()


# ── construir_muestra ─────────────────────────────────────────────────────────

class TestConstruirMuestra:
    def test_all_required_keys_present(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("USERNAME", "estudiante")
        monkeypatch.setenv("COMPUTERNAME", "PC-SALA01-001")
        mocker.patch("agente.captura.capturar_apps_en_uso", return_value=[])

        muestra = construir_muestra()

        assert muestra["hostname"] == "PC-SALA01-001"
        assert muestra["usuario"] == "estudiante"
        assert "timestamp_utc" in muestra
        assert muestra["cantidad_apps"] == 0
        assert muestra["apps"] == []

    def test_cantidad_apps_matches_list_length(self, mocker: MockerFixture) -> None:
        fake_apps = [{"pid": i} for i in range(5)]
        mocker.patch("agente.captura.capturar_apps_en_uso", return_value=fake_apps)
        assert construir_muestra()["cantidad_apps"] == 5

    def test_timestamp_includes_utc_offset(self, mocker: MockerFixture) -> None:
        mocker.patch("agente.captura.capturar_apps_en_uso", return_value=[])
        ts = construir_muestra()["timestamp_utc"]
        assert "+00:00" in ts
