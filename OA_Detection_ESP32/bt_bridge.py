"""Automatic macOS Bluetooth Serial (RFCOMM SPP) <-> FastAPI WebSocket bridge.

Connects a paired ESP32 running `OA_Detection_ESP32_BT.ino` ("OA-Wearable-BT")
over macOS `/dev/cu.OA-Wearable-BT*` directly into `ws://127.0.0.1:8000/ws/esp32`,
forwarding 10 Hz JSON telemetry up and calibration/threshold/alert commands down.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import select
import sys
import termios
from pathlib import Path
from typing import Iterable, Optional

import websockets

DEFAULT_WS_URL = "ws://127.0.0.1:8000/ws/esp32"
DEFAULT_BT_PATTERNS = (
    "/dev/cu.*OA-Wearable*",
    "/dev/cu.*oa-wearable*",
    "/dev/cu.*ESP32*",
)
DEFAULT_USB_PATTERNS = (
    "/dev/cu.usbserial*",
    "/dev/cu.SLAB_USBtoUART*",
    "/dev/cu.wchusbserial*",
    "/dev/cu.usbmodem*",
)


def find_serial_port(
    preferred_port: Optional[str] = None,
    usb_mode: bool = False,
    candidates_override: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Return the first matching Bluetooth SPP (or USB) serial device path."""
    if preferred_port:
        return preferred_port if os.path.exists(preferred_port) else None

    if candidates_override is not None:
        matches = list(candidates_override)
    else:
        patterns = DEFAULT_USB_PATTERNS if usb_mode else DEFAULT_BT_PATTERNS
        matches = []
        for pat in patterns:
            matches.extend(sorted(glob.glob(pat)))

    return matches[0] if matches else None


def configure_raw_serial(fd: int, baud: int = 115200) -> None:
    """Configure an open serial/PTY file descriptor into raw 8N1 mode if supported."""
    try:
        attrs = termios.tcgetattr(fd)
    except termios.error:
        return

    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
    iflag &= ~(
        termios.IGNBRK
        | termios.BRKINT
        | termios.PARMRK
        | termios.ISTRIP
        | termios.INLCR
        | termios.IGNCR
        | termios.ICRNL
        | termios.IXON
    )
    oflag &= ~termios.OPOST
    lflag &= ~(
        termios.ECHO
        | termios.ECHONL
        | termios.ICANON
        | termios.ISIG
        | termios.IEXTEN
    )
    cflag = (cflag & ~(termios.CSIZE | termios.PARENB)) | termios.CS8 | termios.CLOCAL | termios.CREAD
    cc[termios.VMIN] = 1
    cc[termios.VTIME] = 0

    baud_const = getattr(termios, f"B{baud}", termios.B115200)
    ispeed = baud_const
    ospeed = baud_const

    try:
        termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
    except termios.error:
        # Bluetooth RFCOMM virtual ports on macOS sometimes ignore baud changes.
        pass


def open_serial_fd(port_path: str, baud: int = 115200) -> int:
    """Open a Bluetooth SPP or USB serial port in non-blocking raw mode."""
    fd = os.open(port_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    configure_raw_serial(fd, baud=baud)
    return fd


def parse_json_line(raw_line: str) -> Optional[str]:
    """Validate that a serial line is a JSON object and return clean string, else None."""
    line = raw_line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
        if isinstance(obj, dict):
            return json.dumps(obj, separators=(",", ":"))
    except ValueError:
        return None
    return None


async def bridge_fd_and_websocket(
    read_fd: int,
    write_fd: int,
    ws,
    *,
    send_usb_enable: bool = False,
) -> None:
    """Bidirectionally pump newline-delimited JSON between serial fds and WebSocket."""
    loop = asyncio.get_running_loop()
    rx_buf = bytearray()
    stop_event = asyncio.Event()

    if send_usb_enable:
        os.write(write_fd, b"usb_on\n")

    async def serial_to_ws() -> None:
        while not stop_event.is_set():
            try:
                chunk = await loop.run_in_executor(None, _read_chunk_blocking, read_fd)
            except OSError:
                break
            if chunk is None:
                continue
            if chunk == b"":
                break
            rx_buf.extend(chunk)
            while b"\n" in rx_buf:
                raw_bytes, _, rest = rx_buf.partition(b"\n")
                rx_buf[:] = rest
                text = raw_bytes.decode("utf-8", errors="ignore")
                clean_json = parse_json_line(text)
                if clean_json:
                    await ws.send(clean_json)
        stop_event.set()

    async def ws_to_serial() -> None:
        try:
            async for msg in ws:
                if stop_event.is_set():
                    break
                payload = msg.strip() if isinstance(msg, str) else msg.decode("utf-8", errors="ignore").strip()
                if payload:
                    os.write(write_fd, (payload + "\n").encode("utf-8"))
        except Exception:
            pass
        finally:
            stop_event.set()

    tasks = [
        asyncio.create_task(serial_to_ws()),
        asyncio.create_task(ws_to_serial()),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for t in done:
        exc = t.exception()
        if exc and not isinstance(exc, asyncio.CancelledError):
            raise exc


def _read_chunk_blocking(fd: int) -> Optional[bytes]:
    """Wait up to 0.25s for readable bytes on fd; returns None on timeout/EAGAIN, b'' on EOF."""
    readable, _, _ = select.select([fd], [], [], 0.25)
    if not readable:
        return None
    try:
        return os.read(fd, 4096)
    except BlockingIOError:
        return None


async def bridge_subprocess_and_websocket(helper_path: str, target_name: str, ws) -> None:
    """Bidirectionally pump newline JSON between native Bluetooth helper and WebSocket."""
    proc = await asyncio.create_subprocess_exec(
        helper_path,
        target_name,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stop_event = asyncio.Event()
    frames = 0

    async def proc_to_ws() -> None:
        nonlocal frames
        assert proc.stdout is not None
        try:
            while not stop_event.is_set():
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                text = line_bytes.decode("utf-8", errors="ignore")
                clean_json = parse_json_line(text)
                if clean_json:
                    await ws.send(clean_json)
                    frames += 1
                    if frames == 1:
                        print("[BT-Bridge] Live ESP32 Bluetooth telemetry flowing!", flush=True)
                    elif frames % 200 == 0:
                        print(f"[BT-Bridge] Streamed {frames} live frames...", flush=True)
        finally:
            stop_event.set()

    async def stderr_logger() -> None:
        assert proc.stderr is not None
        while not stop_event.is_set():
            line_bytes = await proc.stderr.readline()
            if not line_bytes:
                break
            msg = line_bytes.decode("utf-8", errors="ignore").strip()
            if msg:
                print(msg, flush=True)

    async def ws_to_proc() -> None:
        assert proc.stdin is not None
        try:
            async for msg in ws:
                if stop_event.is_set():
                    break
                payload = msg.strip() if isinstance(msg, str) else msg.decode("utf-8", errors="ignore").strip()
                if payload:
                    proc.stdin.write((payload + "\n").encode("utf-8"))
                    await proc.stdin.drain()
        except Exception:
            pass
        finally:
            stop_event.set()

    try:
        tasks = [
            asyncio.create_task(proc_to_ws()),
            asyncio.create_task(stderr_logger()),
            asyncio.create_task(ws_to_proc()),
        ]
        # Wait until primary stream or WS reader ends, then cancel remaining tasks immediately
        done, pending = await asyncio.wait(
            [tasks[0], tasks[2]],
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop_event.set()
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()


async def run_bridge_forever(
    ws_url: str = DEFAULT_WS_URL,
    preferred_port: Optional[str] = None,
    usb_mode: bool = False,
    poll_interval: float = 2.0,
) -> None:
    """Continuously wait for paired ESP32 Bluetooth/USB port and bridge to dashboard."""
    mode_label = "USB Serial" if usb_mode else "Bluetooth ('OA-Wearable-BT')"
    print(f"[BT-Bridge] Watching for {mode_label} -> {ws_url}", flush=True)
    last_wait_msg = False
    native_helper = Path(__file__).resolve().with_name("macos_bt_spp")
    use_native_macos = (
        sys.platform == "darwin"
        and not usb_mode
        and not preferred_port
        and native_helper.exists()
    )

    while True:
        if use_native_macos:
            try:
                async with websockets.connect(ws_url, open_timeout=5, ping_interval=20) as ws:
                    await bridge_subprocess_and_websocket(str(native_helper), "OA-Wearable", ws)
            except Exception as exc:
                print(f"[BT-Bridge] Native Bluetooth retry ({exc}) in {poll_interval:.0f}s...", flush=True)
            await asyncio.sleep(poll_interval)
            continue

        port = find_serial_port(preferred_port=preferred_port, usb_mode=usb_mode)
        if not port:
            if not last_wait_msg:
                print(
                    "[BT-Bridge] Waiting for paired ESP32 port (System Settings -> Bluetooth -> OA-Wearable-BT)...",
                    flush=True,
                )
                last_wait_msg = True
            await asyncio.sleep(poll_interval)
            continue

        last_wait_msg = False
        print(f"[BT-Bridge] Opening {port}...", flush=True)
        fd: Optional[int] = None
        try:
            fd = open_serial_fd(port)
            async with websockets.connect(ws_url, open_timeout=5, ping_interval=20) as ws:
                print(f"[BT-Bridge] Connected! Streaming {port} <-> {ws_url}", flush=True)
                await bridge_fd_and_websocket(fd, fd, ws, send_usb_enable=usb_mode)
        except Exception as exc:
            print(f"[BT-Bridge] Disconnected ({exc}); retrying in {poll_interval:.0f}s...", flush=True)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        await asyncio.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="ESP32 Bluetooth SPP <-> OA Dashboard Bridge")
    parser.add_argument("--ws", default=DEFAULT_WS_URL, help="Backend WebSocket URL")
    parser.add_argument("--port", default=None, help="Explicit /dev/cu.* serial port path")
    parser.add_argument("--usb", action="store_true", help="Watch USB serial ports instead of Bluetooth")
    args = parser.parse_args()

    try:
        asyncio.run(run_bridge_forever(ws_url=args.ws, preferred_port=args.port, usb_mode=args.usb))
    except KeyboardInterrupt:
        print("\n[BT-Bridge] Stopped.", flush=True)


if __name__ == "__main__":
    main()

