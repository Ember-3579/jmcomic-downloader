"""Entry point for the local JM comic downloader."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import traceback


def enable_windows_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def self_test() -> int:
    from pathlib import Path

    import jmcomic

    from jmcomic_desktop import core

    print(f"app {core.APP_VERSION}")
    print(f"jmcomic {jmcomic.__version__}")
    print(f"frozen {getattr(sys, 'frozen', False)}")
    spec = core.parse_input("https://18comic.vip/album/123456/?v=1")
    assert (spec.kind, spec.jm_id) == ("album", "123456")
    spec = core.parse_input("p654321")
    assert (spec.kind, spec.jm_id) == ("photo", "654321")
    settings = core.Settings(download_dir=str(Path.cwd() / "build" / "selftest"))
    option = core.build_option(
        settings,
        settings.download_dir,
        "album",
        {"zip": True, "pdf": True, "long": True},
    )
    assert option is not None
    assert option.dir_rule.base_dir.endswith("selftest")

    # Exercise the exact pieces that are imported dynamically at runtime.
    import tkinter as tk

    import curl_cffi  # noqa: F401
    import img2pdf
    import pikepdf
    from PIL import Image

    test_dir = Path.cwd() / "build" / "selftest"
    test_dir.mkdir(parents=True, exist_ok=True)
    png_path = test_dir / "probe.png"
    pdf_path = test_dir / "probe.pdf"
    Image.new("RGB", (24, 24), "white").save(png_path)
    pdf_path.write_bytes(img2pdf.convert(str(png_path)))
    assert pdf_path.read_bytes()[:4] == b"%PDF"
    with pikepdf.open(pdf_path):
        pass

    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    print("selftest ok")
    return 0


def smoke_gui() -> int:
    enable_windows_dpi_awareness()
    from jmcomic_desktop.ui import App

    app = App()
    app.after(1200, app.destroy)
    app.mainloop()
    return 0


def network_check(jm_id: str) -> int:
    from pathlib import Path

    from jmcomic_desktop import core

    settings = core.Settings(download_dir=str(Path.cwd() / "build" / "selftest"))
    option = core.build_option(settings, settings.download_dir, "album", {})
    album = option.new_jm_client().get_album_detail(jm_id)
    print(f"network ok: JM{album.id} {album.name} ({len(album)} chapters)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-gui", action="store_true")
    parser.add_argument("--check-network", metavar="JM_ID", default=None)
    args, _unknown = parser.parse_known_args()
    if args.self_test:
        return self_test()
    if args.smoke_gui:
        return smoke_gui()
    if args.check_network:
        return network_check(args.check_network)

    enable_windows_dpi_awareness()
    try:
        from jmcomic_desktop.ui import main as run_gui

        run_gui()
    except Exception:
        detail = traceback.format_exc()
        try:
            from jmcomic_desktop.core import write_error_log

            path = write_error_log(detail)
        except Exception:
            path = "(无法写入错误日志)"
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"JM 漫画下载器启动失败：\n\n{detail[-900:]}\n\n错误记录：\n{path}",
                "启动失败",
                0x10,
            )
        except Exception:
            print(detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
