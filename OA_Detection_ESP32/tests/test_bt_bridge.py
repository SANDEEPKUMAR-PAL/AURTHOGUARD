"""Unit tests for the ESP32 Bluetooth Serial <-> WebSocket bridge and BT sketch."""

import asyncio
import json
import os
from pathlib import Path

from bt_bridge import bridge_fd_and_websocket, find_serial_port, parse_json_line

REPO_ROOT = Path(__file__).resolve().parent.parent
BT_DIR = REPO_ROOT.parent / "OA_Detection_ESP32_Bluetooth" / "OA_Detection_ESP32_BT"


class _FakeWS:
    """Minimal in-memory WebSocket double supporting send() and async iteration."""

    def __init__(self, incoming: list[str]):
        self.sent: list[str] = []
        self._incoming = list(incoming)

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        await asyncio.sleep(0.01)
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)


def test_bt_sketch_files_present():
    assert (BT_DIR / "OA_Detection_ESP32_BT.ino").exists()
    assert (BT_DIR / "config.h").exists()
    ino_text = (BT_DIR / "OA_Detection_ESP32_BT.ino").read_text(encoding="utf-8")
    assert "#include <BluetoothSerial.h>" in ino_text
    assert "calibrateGyroBias(" in ino_text
    assert "readAveragedADC(" in ino_text


def test_parse_json_line_filters_debug_logs():
    assert parse_json_line("[BT] Ready! Pair your Mac") is None
    assert parse_json_line("   ") is None
    assert parse_json_line('{"type":"data","kneeAngle":32.5}') == '{"type":"data","kneeAngle":32.5}'


def test_find_serial_port_selects_first_match():
    found = find_serial_port(candidates_override=["/dev/cu.OA-Wearable-BT"])
    assert found == "/dev/cu.OA-Wearable-BT"
    assert find_serial_port(candidates_override=[]) is None


def test_bridge_fd_and_websocket_bidirectional():
    r_esp_to_ws, w_esp_to_ws = os.pipe()
    r_ws_to_esp, w_ws_to_esp = os.pipe()
    os.set_blocking(r_esp_to_ws, False)

    fake_ws = _FakeWS(['{"cmd":"calibrate_step1"}'])

    async def _run():
        os.write(w_esp_to_ws, b'[LOG] Boot OK\n{"type":"data","kneeAngleC":28.4}\n')

        async def _close_later():
            await asyncio.sleep(0.06)
            os.close(w_esp_to_ws)

        await asyncio.gather(
            bridge_fd_and_websocket(r_esp_to_ws, w_ws_to_esp, fake_ws),
            _close_later(),
        )

    try:
        asyncio.run(_run())
        assert len(fake_ws.sent) == 1
        assert json.loads(fake_ws.sent[0])["kneeAngleC"] == 28.4
        cmd_bytes = os.read(r_ws_to_esp, 1024).decode("utf-8")
        assert '{"cmd":"calibrate_step1"}\n' == cmd_bytes
    finally:
        for fd in (r_esp_to_ws, r_ws_to_esp, w_ws_to_esp):
            try:
                os.close(fd)
            except OSError:
                pass

