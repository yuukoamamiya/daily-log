"""pywebview desktop shell for the reusable Daily Log web client."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .paths import AppPaths
from .single_instance import InstanceAlreadyRunning, SingleInstance


LOGGER = logging.getLogger("daily_log.desktop")


def _program_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def _load_dependencies():
    try:
        import pystray
        import webview
        from PIL import Image, ImageDraw
    except ImportError as error:
        raise SystemExit(
            "桌面模式需要安装 pywebview、pystray 和 Pillow；请先安装 requirements-app.txt。"
        ) from error
    return Image, ImageDraw, pystray, webview


def _tray_image(Image, ImageDraw):
    image = Image.new("RGBA", (64, 64), (27, 33, 29, 255))
    draw = ImageDraw.Draw(image)
    colors = ((255, 255, 255, 255), (246, 199, 91, 255), (232, 132, 112, 255), (121, 182, 154, 255))
    for index, color in enumerate(colors):
        x = 14 + (index % 2) * 18
        y = 14 + (index // 2) * 18
        draw.rounded_rectangle((x, y, x + 12, y + 12), radius=3, fill=color)
    return image


class DesktopApplication:
    CLOSE_BACKUP_TIMEOUT = 15

    def __init__(
        self,
        *,
        webview,
        pystray,
        window,
        server,
        instance,
        backup=None,
        backup_status=None,
        flush=None,
        idle_backup=None,
    ):
        self.webview = webview
        self.pystray = pystray
        self.window = window
        self.server = server
        self.instance = instance
        self.tray = None
        self.allow_close = False
        self._backup_callback = backup
        self._backup_status_callback = backup_status
        self._flush_callback = flush
        self._idle_backup = idle_backup
        self._close_lock = threading.RLock()
        self._close_cancelled = threading.Event()
        self._closing = False
        self._close_generation = 0
        self._close_thread = None

    def start_tray(self, image) -> None:
        menu = self.pystray.Menu(
            self.pystray.MenuItem("打开 Daily Log", self._show_window, default=True),
            self.pystray.MenuItem("立即备份", self._backup),
            self.pystray.MenuItem("退出", self._exit),
        )
        self.tray = self.pystray.Icon("daily-log", image, "Daily Log", menu)
        threading.Thread(target=self.tray.run, name="daily-log-tray", daemon=True).start()

    def on_closing(self, *_args) -> bool:
        if self.allow_close:
            return True
        self._request_exit()
        return False

    def _request_exit(self) -> None:
        with self._close_lock:
            if self.allow_close or self._closing:
                return
        try:
            status = self._runtime_backup_status()
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("读取退出前备份状态失败，将尝试安全收尾：%s", error)
            status = {"pending": True, "busy": False}
        if status.get("pending") or status.get("busy"):
            self._begin_close()
        else:
            self._exit_now()

    def _begin_close(self) -> None:
        with self._close_lock:
            if self.allow_close or self._closing:
                return
            self._closing = True
            self._close_generation += 1
            generation = self._close_generation
            self._close_cancelled.clear()
        self._set_tray_title("Daily Log：正在保存并备份，点击打开可取消退出")
        self.window.hide()
        self._close_thread = threading.Thread(
            target=self._finish_close,
            args=(generation,),
            name="daily-log-close",
            daemon=True,
        )
        self._close_thread.start()

    def _show_window(self, *_args) -> None:
        with self._close_lock:
            closing = self._closing and not self.allow_close
            if closing:
                self._close_cancelled.set()
        self.window.show()
        if not closing:
            self._set_tray_title("Daily Log")

    def _minimize_to_tray(self, *_args) -> None:
        if self._closing:
            return
        self.window.hide()
        self._notify_background_backup()

    def _notify_background_backup(self) -> None:
        if self._idle_backup is not None:
            try:
                self._idle_backup.notify()
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("无法触发闲时自动备份：%s", error)
            return
        try:
            import web_server

            if web_server.AUTO_BACKUP is not None:
                web_server.AUTO_BACKUP.notify()
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("无法触发闲时自动备份：%s", error)

    def _set_tray_title(self, title: str) -> None:
        if self.tray is not None:
            self.tray.title = title

    def _runtime_backup_status(self) -> dict:
        if self._backup_status_callback is not None:
            return self._backup_status_callback()
        import web_server

        return web_server.get_backup_status()

    def _flush_local(self) -> None:
        if self._flush_callback is not None:
            self._flush_callback()
            return
        import web_server

        web_server.worker().flush()

    def _backup_before_close(self, *, timeout: float) -> None:
        if self._backup_callback is not None:
            self._backup_callback("关闭前自动备份", timeout=timeout)
            return
        import web_server

        web_server.backup_now("关闭前自动备份", timeout=timeout)

    def _wait_for_backup_idle(self, deadline: float) -> dict:
        status = self._runtime_backup_status()
        while status.get("busy") and time.monotonic() < deadline:
            time.sleep(0.1)
            status = self._runtime_backup_status()
        return status

    def _close_was_cancelled(self, generation: int) -> bool:
        with self._close_lock:
            return (
                self._close_generation != generation
                or not self._closing
                or self._close_cancelled.is_set()
            )

    def _finish_close(self, generation: int) -> None:
        try:
            if self._close_was_cancelled(generation):
                return
            self._flush_local()
            if self._close_was_cancelled(generation):
                return
            deadline = time.monotonic() + self.CLOSE_BACKUP_TIMEOUT
            status = self._wait_for_backup_idle(deadline)
            remaining = deadline - time.monotonic()
            if (
                not self._close_was_cancelled(generation)
                and status.get("pending")
                and not status.get("busy")
                and remaining > 0
            ):
                self._set_tray_title("Daily Log：正在备份，点击打开可取消退出")
                self._backup_before_close(timeout=remaining)
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("关闭前备份失败，将保留待备份状态：%s", error)
        finally:
            stale = False
            with self._close_lock:
                if self._close_generation != generation or not self._closing:
                    stale = True
                    cancelled = True
                else:
                    cancelled = self._close_cancelled.is_set()
                    self._closing = False
                    if not cancelled:
                        self.allow_close = True
            if not stale:
                self._set_tray_title("Daily Log")
                if not cancelled:
                    self.window.destroy()

    def _exit_now(self) -> None:
        with self._close_lock:
            self.allow_close = True
            self._closing = False
            self._close_cancelled.set()
        if self.tray is not None:
            self.tray.stop()
        self.window.destroy()

    def _backup(self, *_args) -> None:
        def run() -> None:
            try:
                import web_server

                web_server.backup_now("托盘手动备份")
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("托盘备份失败：%s", error)

        threading.Thread(target=run, name="daily-log-tray-backup", daemon=True).start()

    def _exit(self, *_args) -> None:
        self._request_exit()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 Daily Log Windows 桌面客户端")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data-dir", type=Path, help="用户数据目录；默认使用当前系统的本机应用数据目录")
    parser.add_argument("--migrate-from", type=Path, help="仅首次启动使用：从旧版 daily-log 仓库显式迁移个人数据")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    Image, ImageDraw, pystray, webview = _load_dependencies()

    if args.smoke_test:
        if not (_program_root() / "web" / "index.html").is_file():
            raise SystemExit(f"网页资源不存在：{_program_root() / 'web'}")
        return 0

    scripts = _program_root() / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import web_server

    paths = AppPaths(args.data_dir.expanduser().resolve()) if args.data_dir else AppPaths.default()
    migration_source = args.migrate_from.expanduser().resolve() if args.migrate_from else None
    web_server.configure_runtime(
        paths,
        migration_source,
        explicit_data_dir=bool(args.data_dir or os.environ.get("DAILY_LOG_STATE_DIR")),
    )
    instance = SingleInstance(paths.instance_lock)
    try:
        instance.acquire()
    except InstanceAlreadyRunning as error:
        if instance.reopen_available:
            try:
                instance.request_reopen()
            except OSError:
                print(str(error), file=sys.stderr)
                return 1
            print("Daily Log 已在运行，正在打开已有窗口。")
            return 0
        print(str(error), file=sys.stderr)
        return 1

    server = None
    server_thread = None
    application = None
    try:
        server = web_server.ThreadingHTTPServer(("127.0.0.1", 0), web_server.DailyLogHandler)
        web_server.initialize_runtime()
        server_thread = threading.Thread(target=server.serve_forever, name="daily-log-http", daemon=True)
        server_thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        window = webview.create_window(
            "Daily Log",
            url,
            width=1180,
            height=820,
            min_size=(860, 620),
        )
        application = DesktopApplication(
            webview=webview,
            pystray=pystray,
            window=window,
            server=server,
            instance=instance,
            idle_backup=web_server.AUTO_BACKUP,
        )
        instance.mark_reopen_available()
        instance.start_reopen_listener(application._show_window)
        window.events.closing += application.on_closing
        def hide_if_minimized(*_args) -> None:
            if web_server.CONFIG.public()["application"].get("startMinimized"):
                application._minimize_to_tray()

        window.events.loaded += hide_if_minimized
        application.start_tray(_tray_image(Image, ImageDraw))
        webview.start()
        return 0
    finally:
        if application is not None:
            application.allow_close = True
            if application.tray is not None:
                application.tray.stop()
        instance.stop_reopen_listener()
        if server is not None and server_thread is not None and server_thread.is_alive():
            server.shutdown()
        if server is not None:
            server.server_close()
        if web_server.AUTO_BACKUP is not None:
            web_server.AUTO_BACKUP.stop()
        if web_server.WORKER is not None:
            web_server.WORKER.stop(flush=True)
        from .diagnostics import close_logging

        instance.release()
        close_logging()


if __name__ == "__main__":
    raise SystemExit(main())
