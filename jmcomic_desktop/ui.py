"""Tkinter interface for the local JM downloader."""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import font as tkfont
from tkinter import ttk

import tkinter as tk

from . import core

# ---------------------------------------------------------------------------
# Palette

BG = "#0e0b12"
CARD = "#17121e"
CARD_ALT = "#211a2a"
CARD_HOVER = "#2a2135"
INPUT = "#211a2a"
BORDER = "#30263b"
BORDER_LIGHT = "#443550"
TEXT = "#f7f3f9"
MUTED = "#a99cb7"
FAINT = "#74677f"
ACCENT = "#ef4f7d"
ACCENT_HOVER = "#ff648d"
ACCENT_DARK = "#a32d50"
ACCENT_SOFT = "#3b202b"
SUCCESS = "#63d5a4"
WARNING = "#f0c36a"
DANGER = "#ff7085"
DISABLED = "#2a2232"


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def _pick_font(root: Tk) -> tuple[str, str]:
    families = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"):
        if name in families:
            ui = name
            break
    else:
        ui = "TkDefaultFont"
    for name in ("Cascadia Mono", "Consolas", "Courier New"):
        if name in families:
            mono = name
            break
    else:
        mono = "TkFixedFont"
    return ui, mono


class FlatButton(tk.Button):
    """Flat button with a hover state that also respects disabled state."""

    def __init__(self, master, text, command, *, kind: str = "secondary", **kwargs):
        if kind == "primary":
            normal, hover, fg = ACCENT, ACCENT_HOVER, "#ffffff"
        elif kind == "ghost":
            normal, hover, fg = CARD, CARD_ALT, MUTED
        elif kind == "danger":
            normal, hover, fg = "#3b1d27", "#512432", "#ff92a1"
        else:
            normal, hover, fg = CARD_ALT, CARD_HOVER, TEXT

        super().__init__(
            master,
            text=text,
            command=command,
            bg=normal,
            fg=fg,
            activebackground=hover,
            activeforeground=fg,
            disabledforeground=FAINT,
            bd=0,
            relief="flat",
            highlightthickness=0,
            cursor="hand2",
            **kwargs,
        )
        self._normal = normal
        self._hover = hover
        self._fg = fg
        self._enabled = True
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_enter(self, _event=None):
        if self._enabled:
            self.configure(bg=self._hover)

    def _on_leave(self, _event=None):
        if self._enabled:
            self.configure(bg=self._normal)

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.configure(
            state="normal" if enabled else "disabled",
            bg=self._normal if enabled else DISABLED,
            cursor="hand2" if enabled else "arrow",
        )


class App(tk.Tk):
    POLL_MS = 90

    def __init__(self):
        super().__init__()
        self.settings = core.load_settings()
        self.history = core.load_history()
        self.meta: dict | None = None
        self.download_worker: core.DownloadWorker | None = None
        self.meta_worker: core.MetadataWorker | None = None
        self.meta_cancel = None
        self.events: queue.Queue = queue.Queue()
        self._busy = False
        self._parsing = False
        self._download_started_at: float | None = None
        self._tick_job = None
        self._progress_images = 0
        self._total_photos = 0
        self._last_save_path = ""
        self._closing = False

        self.ui_font, self.mono_font = _pick_font(self)
        self._configure_window()
        self._configure_styles()
        self._build_ui()
        self._apply_settings_to_form()
        self._render_history()
        self.report_callback_exception = self._report_tk_exception
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_MS, self._poll_events)
        self.after(250, self._focus_input)

    # ------------------------------------------------------------------ setup

    def _configure_window(self):
        self.title(core.APP_DISPLAY_NAME)
        self.configure(bg=BG)
        self.minsize(1000, 700)
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(1160, max(980, sw - 120))
        height = min(790, max(700, sh - 110))
        if self.settings.window_geometry:
            match = re.fullmatch(r"(\d+)x(\d+)", self.settings.window_geometry)
            if match:
                width = max(980, min(1400, int(match.group(1))))
                height = max(700, min(1000, int(match.group(2))))
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2 - 10)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self._set_icon()

    def _set_icon(self):
        try:
            from PIL import Image, ImageTk

            path = resource_path("assets/icon.png")
            if path.is_file():
                image = Image.open(path).convert("RGBA").resize((64, 64))
                self._icon_image = ImageTk.PhotoImage(image)
                self.iconphoto(True, self._icon_image)
        except Exception:
            pass

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Download.Horizontal.TProgressbar",
            troughcolor=CARD_ALT,
            background=ACCENT,
            bordercolor=CARD_ALT,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=8,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=INPUT,
            background=INPUT,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=5,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", INPUT)],
            foreground=[("readonly", TEXT)],
        )

    def _build_ui(self):
        # Thin accent line at the very top.
        tk.Frame(self, bg=ACCENT, height=3).pack(fill="x")

        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=28, pady=(16, 12))

        self._build_logo(header)

        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left", padx=(13, 0))
        tk.Label(
            titles,
            text=core.APP_DISPLAY_NAME,
            bg=BG,
            fg=TEXT,
            font=(self.ui_font, 19, "bold"),
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="本地漫画下载器  ·  整本 / 指定章节  ·  ZIP / PDF / 长图",
            bg=BG,
            fg=MUTED,
            font=(self.ui_font, 9),
        ).pack(anchor="w", pady=(2, 0))

        header_actions = tk.Frame(header, bg=BG)
        header_actions.pack(side="right")
        FlatButton(
            header_actions,
            "  打开目录  ",
            self._open_download_dir,
            kind="ghost",
            font=(self.ui_font, 9),
            padx=10,
            pady=7,
        ).pack(side="right", padx=(6, 0))
        FlatButton(
            header_actions,
            "  设置  ",
            self._open_settings,
            kind="ghost",
            font=(self.ui_font, 9),
            padx=10,
            pady=7,
        ).pack(side="right")

        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True, padx=28, pady=(0, 8))
        main.grid_columnconfigure(0, weight=5, minsize=535)
        main.grid_columnconfigure(1, weight=4, minsize=390)
        main.grid_rowconfigure(0, weight=1)

        left = tk.Frame(
            main, bg=CARD, highlightthickness=1, highlightbackground=BORDER
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = tk.Frame(
            main, bg=CARD, highlightthickness=1, highlightbackground=BORDER
        )
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self._build_left_card(left)
        self._build_right_card(right)
        self._build_footer()

    def _build_logo(self, parent):
        try:
            from PIL import Image, ImageTk

            path = resource_path("assets/icon.png")
            if path.is_file():
                image = Image.open(path).convert("RGBA").resize((48, 48))
                self._logo_image = ImageTk.PhotoImage(image)
                tk.Label(parent, image=self._logo_image, bg=BG).pack(side="left")
                return
        except Exception:
            pass
        badge = tk.Frame(parent, bg=ACCENT, width=48, height=48)
        badge.pack(side="left")
        badge.pack_propagate(False)
        tk.Label(
            badge, text="JM", bg=ACCENT, fg="white", font=(self.ui_font, 13, "bold")
        ).pack(expand=True)

    def _build_left_card(self, card):
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True, padx=24, pady=20)

        self._section_title(body, "下载任务", "支持链接、JM 车号、单章链接")

        tk.Label(
            body,
            text="作品链接 / 车号",
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 10, "bold"),
        ).pack(anchor="w", pady=(13, 6))
        self.input_var = StringVar()
        input_wrap, self.input_entry = self._entry(body, self.input_var, size=12)
        input_wrap.pack(fill="x")
        self.input_entry.bind("<Return>", lambda _e: self.start_parse())
        self.input_entry.bind("<Control-Return>", lambda _e: self.start_download())
        tk.Label(
            body,
            text="示例：JM123456    18comic.vip/album/123456    p123456（单章）",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
        ).pack(anchor="w", pady=(5, 0))

        save_head = tk.Frame(body, bg=CARD)
        save_head.pack(fill="x", pady=(12, 6))
        tk.Label(
            save_head,
            text="保存位置",
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 10, "bold"),
        ).pack(side="left")
        tk.Label(
            save_head,
            text="下载目录 / 本子名 / 章节 / 图片",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
        ).pack(side="right")

        self.save_var = StringVar()
        save_wrap = tk.Frame(
            body,
            bg=INPUT,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BORDER_LIGHT,
        )
        save_wrap.pack(fill="x")
        save_entry = tk.Entry(
            save_wrap,
            textvariable=self.save_var,
            bg=INPUT,
            fg=MUTED,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(self.ui_font, 9),
        )
        save_entry.pack(side="left", fill="x", expand=True, padx=(12, 4), pady=9)
        browse = FlatButton(
            save_wrap,
            "浏览",
            self._browse_folder,
            kind="ghost",
            font=(self.ui_font, 9),
            padx=10,
            pady=5,
        )
        browse.pack(side="right", padx=4, pady=4)

        chapter_head = tk.Frame(body, bg=CARD)
        chapter_head.pack(fill="x", pady=(12, 6))
        tk.Label(
            chapter_head,
            text="章节范围",
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 10, "bold"),
        ).pack(side="left")
        tk.Label(
            chapter_head,
            text="留空 = 全部章节",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
        ).pack(side="right")

        self.chapter_var = StringVar()
        chapter_wrap, self.chapter_entry = self._entry(body, self.chapter_var, size=10)
        chapter_wrap.pack(fill="x")
        self.chapter_entry.bind("<Return>", lambda _e: self.start_download())
        tk.Label(
            body,
            text="可填 1-3,5 或 p123456；单章链接会自动忽略此栏。",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
        ).pack(anchor="w", pady=(5, 0))

        export_head = tk.Frame(body, bg=CARD)
        export_head.pack(fill="x", pady=(12, 4))
        tk.Label(
            export_head,
            text="额外导出",
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 10, "bold"),
        ).pack(side="left")
        tk.Label(
            export_head,
            text="原图始终保留，导出文件在「导出」文件夹",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
        ).pack(side="right")

        export_row = tk.Frame(body, bg=CARD)
        export_row.pack(fill="x")
        self.zip_var = BooleanVar(value=self.settings.zip_enabled)
        self.pdf_var = BooleanVar(value=self.settings.pdf_enabled)
        self.long_var = BooleanVar(value=self.settings.long_enabled)
        for label, variable in (
            ("ZIP 压缩包", self.zip_var),
            ("PDF 合并", self.pdf_var),
            ("PNG 长图", self.long_var),
        ):
            tk.Checkbutton(
                export_row,
                text=label,
                variable=variable,
                bg=CARD,
                fg=MUTED,
                activebackground=CARD,
                activeforeground=TEXT,
                selectcolor=INPUT,
                highlightthickness=0,
                bd=0,
                font=(self.ui_font, 9),
                cursor="hand2",
            ).pack(side="left", padx=(0, 16))

        action_row = tk.Frame(body, bg=CARD)
        action_row.pack(fill="x", pady=(14, 0))
        self.parse_btn = FlatButton(
            action_row,
            "解析信息",
            self.start_parse,
            kind="secondary",
            font=(self.ui_font, 10, "bold"),
            padx=16,
            pady=11,
        )
        self.parse_btn.pack(side="left")
        self.download_btn = FlatButton(
            action_row,
            "开始下载",
            self.start_download,
            kind="primary",
            font=(self.ui_font, 11, "bold"),
            padx=18,
            pady=11,
        )
        self.download_btn.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self.cancel_btn = FlatButton(
            action_row,
            "取消",
            self.cancel_download,
            kind="danger",
            font=(self.ui_font, 10, "bold"),
            padx=14,
            pady=11,
        )

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(15, 13))
        progress_head = tk.Frame(body, bg=CARD)
        progress_head.pack(fill="x")
        self.state_dot = tk.Label(
            progress_head,
            text="●",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 9),
        )
        self.state_dot.pack(side="left")
        self.state_label = tk.Label(
            progress_head,
            text="准备就绪",
            bg=CARD,
            fg=MUTED,
            font=(self.ui_font, 9, "bold"),
        )
        self.state_label.pack(side="left", padx=(5, 0))
        self.elapsed_label = tk.Label(
            progress_head,
            text="",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
        )
        self.elapsed_label.pack(side="right")

        self.progress_bar = ttk.Progressbar(
            body,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            style="Download.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill="x", pady=(8, 5))
        self.progress_detail = tk.Label(
            body,
            text="粘贴链接后点击「解析信息」查看详情",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
            anchor="w",
        )
        self.progress_detail.pack(fill="x")

    def _build_right_card(self, card):
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True, padx=20, pady=18)

        self._section_title(body, "作品信息", "解析后可预览章节")

        self.info_host = tk.Frame(body, bg=CARD, height=202)
        self.info_host.pack(fill="x", pady=(8, 0))
        self.info_host.pack_propagate(False)
        self._render_info_placeholder()

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(12, 10))

        log_head = tk.Frame(body, bg=CARD)
        log_head.pack(fill="x")
        tk.Label(
            log_head,
            text="运行日志",
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 9, "bold"),
        ).pack(side="left")
        clear = tk.Label(
            log_head,
            text="清空",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
            cursor="hand2",
        )
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda _e: self._clear_log())

        log_wrap = tk.Frame(body, bg="#120e19", highlightthickness=1, highlightbackground=BORDER)
        log_wrap.pack(fill="x", pady=(5, 0))
        self.log_text = tk.Text(
            log_wrap,
            height=5,
            bg="#120e19",
            fg=MUTED,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            font=(self.mono_font, 8),
            padx=9,
            pady=7,
            state="disabled",
        )
        self.log_text.pack(fill="x")
        self._append_log("就绪。粘贴链接或车号，然后开始。", prefix=False)

        history_head = tk.Frame(body, bg=CARD)
        history_head.pack(fill="x", pady=(12, 6))
        tk.Label(
            history_head,
            text="最近下载",
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 9, "bold"),
        ).pack(side="left")
        self.history_count = tk.Label(
            history_head,
            text="",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
        )
        self.history_count.pack(side="right")

        self.history_host = tk.Frame(body, bg=CARD)
        self.history_host.pack(fill="both", expand=True)

    def _build_footer(self):
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=30, pady=(0, 9))
        self.footer_status = tk.Label(
            footer,
            text="● 就绪",
            bg=BG,
            fg=FAINT,
            font=(self.ui_font, 8),
        )
        self.footer_status.pack(side="left")
        tk.Label(
            footer,
            text=f"jmcomic 2.7.10  ·  v{core.APP_VERSION}  ·  仅用于个人备份",
            bg=BG,
            fg=FAINT,
            font=(self.ui_font, 8),
        ).pack(side="right")

    # --------------------------------------------------------------- helpers

    def _section_title(self, parent, text, hint=""):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x")
        bar = tk.Frame(row, bg=ACCENT, width=3, height=16)
        bar.pack(side="left", padx=(0, 8))
        bar.pack_propagate(False)
        tk.Label(
            row,
            text=text,
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 11, "bold"),
        ).pack(side="left")
        if hint:
            tk.Label(
                row,
                text=hint,
                bg=CARD,
                fg=FAINT,
                font=(self.ui_font, 8),
            ).pack(side="right")
        return row

    def _entry(self, parent, variable, size=10):
        wrap = tk.Frame(
            parent,
            bg=INPUT,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        entry = tk.Entry(
            wrap,
            textvariable=variable,
            bg=INPUT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(self.ui_font, size),
        )
        entry.pack(fill="x", padx=12, pady=9 if size < 12 else 10)
        entry.bind(
            "<FocusIn>",
            lambda _e: wrap.configure(highlightbackground=ACCENT),
        )
        entry.bind(
            "<FocusOut>",
            lambda _e: wrap.configure(highlightbackground=BORDER),
        )
        return wrap, entry

    def _apply_settings_to_form(self):
        self.save_var.set(self.settings.download_dir)
        if self.settings.last_input:
            self.input_var.set(self.settings.last_input)

    def _focus_input(self):
        if not self._closing:
            self.input_entry.focus_set()

    def _render_info_placeholder(self):
        for child in self.info_host.winfo_children():
            child.destroy()
        badge = tk.Frame(self.info_host, bg=ACCENT_SOFT, width=58, height=58)
        badge.pack(pady=(20, 9))
        badge.pack_propagate(False)
        tk.Label(
            badge,
            text="↓",
            bg=ACCENT_SOFT,
            fg=ACCENT,
            font=(self.ui_font, 24, "bold"),
        ).pack(expand=True)
        tk.Label(
            self.info_host,
            text="等待解析",
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 11, "bold"),
        ).pack()
        tk.Label(
            self.info_host,
            text="粘贴作品链接或车号，点击「解析信息」",
            bg=CARD,
            fg=FAINT,
            font=(self.ui_font, 8),
        ).pack(pady=(3, 0))

    def _render_meta(self, meta: dict):
        self.meta = meta
        for child in self.info_host.winfo_children():
            child.destroy()

        kind_text = "整本" if meta.get("kind") == "album" else "单章"
        title = str(meta.get("title") or "未命名作品")
        if len(title) > 64:
            title = title[:64] + "…"
        author = str(meta.get("author", "未知作者"))
        if len(author) > 54:
            author = author[:54] + "…"
        tk.Label(
            self.info_host,
            text=title,
            bg=CARD,
            fg=TEXT,
            font=(self.ui_font, 13, "bold"),
            anchor="w",
            justify="left",
            wraplength=400,
        ).pack(fill="x", anchor="w")
        tk.Label(
            self.info_host,
            text=f"JM{meta.get('id', '')}  ·  {author}  ·  {kind_text}",
            bg=CARD,
            fg=ACCENT,
            font=(self.ui_font, 8, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(3, 8))

        stats = tk.Frame(self.info_host, bg=CARD)
        stats.pack(fill="x")
        self._stat_pill(stats, str(meta.get("chapter_count", 0)), "章节")
        self._stat_pill(stats, str(meta.get("page_count", 0)), "页")
        if meta.get("kind") == "photo" and meta.get("album_title"):
            self._stat_pill(stats, str(meta.get("album_title")), "所属作品", wide=True)

        tags = meta.get("tags") or []
        if tags:
            tk.Label(
                self.info_host,
                text="标签  " + "  /  ".join(str(tag) for tag in tags[:5]),
                bg=CARD,
                fg=MUTED,
                font=(self.ui_font, 8),
                anchor="w",
                wraplength=400,
                justify="left",
            ).pack(fill="x", pady=(7, 0))

        chapters = meta.get("chapters") or []
        if chapters:
            preview = "   ".join(
                f"{int(chapter.get('index', 0)):02d} {chapter.get('name', '')}"[:34]
                for chapter in chapters[:3]
            )
            if len(chapters) > 3:
                preview += f"   等 {len(chapters)} 章"
            tk.Label(
                self.info_host,
                text="章节  " + preview,
                bg=CARD,
                fg=FAINT,
                font=(self.ui_font, 8),
                anchor="w",
                wraplength=400,
                justify="left",
            ).pack(fill="x", pady=(5, 0))

        description = str(meta.get("description") or "").strip()
        if description:
            description = re.sub(r"\s+", " ", description)
            if len(description) > 110:
                description = description[:110] + "…"
            tk.Label(
                self.info_host,
                text=description,
                bg=CARD,
                fg=FAINT,
                font=(self.ui_font, 8),
                anchor="w",
                wraplength=400,
                justify="left",
            ).pack(fill="x", pady=(5, 0))

    def _stat_pill(self, parent, value, label, wide=False):
        pill = tk.Frame(parent, bg=CARD_ALT)
        pill.pack(side="left", padx=(0, 7))
        tk.Label(
            pill,
            text=value[:18] if wide else value,
            bg=CARD_ALT,
            fg=TEXT,
            font=(self.ui_font, 9 if wide else 12, "bold"),
            anchor="w",
        ).pack(anchor="w", padx=9, pady=(5, 0))
        tk.Label(
            pill,
            text=label,
            bg=CARD_ALT,
            fg=FAINT,
            font=(self.ui_font, 7),
            anchor="w",
        ).pack(anchor="w", padx=9, pady=(0, 5))

    def _render_history(self):
        for child in self.history_host.winfo_children():
            child.destroy()
        self.history_count.configure(text=f"{len(self.history)} 条" if self.history else "")
        if not self.history:
            tk.Label(
                self.history_host,
                text="还没有下载记录",
                bg=CARD,
                fg=FAINT,
                font=(self.ui_font, 8),
            ).pack(anchor="w", pady=(4, 0))
            return
        for entry in self.history[:3]:
            self._history_row(entry)

    def _history_row(self, entry):
        row = tk.Frame(self.history_host, bg=CARD_ALT)
        row.pack(fill="x", pady=(0, 5))

        text = tk.Frame(row, bg=CARD_ALT)
        text.pack(side="left", fill="x", expand=True, padx=(9, 2), pady=5)
        title = str(entry.get("title") or "未命名")
        if len(title) > 30:
            title = title[:30] + "…"
        tk.Label(
            text,
            text=title,
            bg=CARD_ALT,
            fg=TEXT,
            font=(self.ui_font, 8, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            text,
            text=f"JM{entry.get('jm_id', '')}  ·  {entry.get('time', '')}  ·  {entry.get('status', '完成')}",
            bg=CARD_ALT,
            fg=FAINT,
            font=(self.ui_font, 7),
            anchor="w",
        ).pack(fill="x")

        open_btn = FlatButton(
            row,
            "打开",
            lambda e=entry: self._open_path(e.get("save_path", "")),
            kind="ghost",
            font=(self.ui_font, 8),
            padx=8,
            pady=4,
        )
        open_btn.configure(bg=CARD_ALT)
        open_btn._normal = CARD_ALT
        open_btn._hover = CARD_HOVER
        open_btn.pack(side="right", padx=5)

    # ------------------------------------------------------------------ log

    def _append_log(self, text, prefix=True):
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {text}" if prefix else text
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 80:
            self.log_text.delete("1.0", f"{lines - 60}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_state(self, text, color=MUTED, footer=None):
        self.state_label.configure(text=text, fg=color)
        self.state_dot.configure(fg=color)
        if footer is not None:
            self.footer_status.configure(text=f"● {footer}", fg=color)

    # --------------------------------------------------------------- actions

    def _browse_folder(self):
        current = Path(self.save_var.get()).expanduser()
        initial = current if current.is_dir() else Path.home()
        selected = filedialog.askdirectory(
            parent=self,
            initialdir=str(initial),
            title="选择下载保存位置",
            mustexist=False,
        )
        if selected:
            self.save_var.set(selected)

    def _open_download_dir(self):
        self._open_path(self.save_var.get(), create=True)

    def _open_path(self, path: str, create=False):
        if not path:
            messagebox.showwarning("还没有目录", "请先完成一次下载，或选择保存位置。", parent=self)
            return
        target = Path(path).expanduser()
        if create:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                messagebox.showerror("无法创建目录", str(error), parent=self)
                return
        if not target.exists():
            messagebox.showwarning("目录不存在", f"找不到：\n{target}", parent=self)
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception as error:
            messagebox.showerror("无法打开目录", str(error), parent=self)

    def start_parse(self):
        if self._parsing or self._busy:
            return
        try:
            spec = core.parse_input(self.input_var.get())
        except ValueError as error:
            messagebox.showwarning("无法识别", str(error), parent=self)
            self.input_entry.focus_set()
            return

        self._parsing = True
        self.parse_btn.set_enabled(False)
        self._set_state("正在解析…", WARNING, "解析中")
        self._append_log(f"解析 JM{spec.jm_id}（{'单章' if spec.kind == 'photo' else '整本'}）")
        self.meta_cancel = threading.Event()
        self.meta_worker = core.MetadataWorker(spec, self.settings, self.events, self.meta_cancel)
        self.meta_worker.start()

    def start_download(self):
        if self._busy:
            return
        try:
            spec = core.parse_input(self.input_var.get())
        except ValueError as error:
            messagebox.showwarning("无法识别", str(error), parent=self)
            self.input_entry.focus_set()
            return

        save_dir = Path(self.save_var.get().strip()).expanduser()
        if not str(save_dir):
            messagebox.showwarning("请选择目录", "请先选择下载保存位置。", parent=self)
            return
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("目录不可用", f"无法创建保存目录：\n{error}", parent=self)
            return

        self.settings.download_dir = str(save_dir)
        self.settings.last_input = self.input_var.get().strip()[:500]
        self.settings.zip_enabled = bool(self.zip_var.get())
        self.settings.pdf_enabled = bool(self.pdf_var.get())
        self.settings.long_enabled = bool(self.long_var.get())
        try:
            core.save_settings(self.settings)
        except OSError:
            pass

        exports = {
            "zip": bool(self.zip_var.get()),
            "pdf": bool(self.pdf_var.get()),
            "long": bool(self.long_var.get()),
        }
        self.download_worker = core.DownloadWorker(
            spec,
            self.settings,
            self.chapter_var.get(),
            exports,
            self.events,
        )
        self._set_busy(True)
        self.progress_bar["value"] = 0
        self._progress_images = 0
        self._total_photos = 0
        self._download_started_at = time.monotonic()
        self.elapsed_label.configure(text="用时 00:00")
        self._tick_elapsed()
        self._set_state("正在连接…", ACCENT, "下载中")
        self.progress_detail.configure(text="正在获取作品信息…")
        self._append_log(
            f"开始下载 JM{spec.jm_id}"
            + ("（单章）" if spec.kind == "photo" else "")
            + (f"，章节 {self.chapter_var.get().strip()}" if self.chapter_var.get().strip() else "")
        )
        self.download_worker.start()

    def cancel_download(self):
        if self.download_worker and self.download_worker.is_alive():
            self.download_worker.cancel()
            self.cancel_btn.set_enabled(False)
            self._set_state("正在取消…", WARNING, "取消中")
            self._append_log("已请求取消，等待当前请求收尾…")

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.parse_btn.set_enabled(not busy)
        self.download_btn.set_enabled(not busy)
        self.download_btn.configure(text="下载中…" if busy else "开始下载")
        self.cancel_btn.set_enabled(busy)
        if busy:
            if not self.cancel_btn.winfo_ismapped():
                self.cancel_btn.pack(side="left", padx=(10, 0))
        else:
            self.cancel_btn.pack_forget()
            if self._tick_job is not None:
                try:
                    self.after_cancel(self._tick_job)
                except Exception:
                    pass
                self._tick_job = None
            self._download_started_at = None

    def _tick_elapsed(self):
        if self._download_started_at is None:
            return
        elapsed = max(0, int(time.monotonic() - self._download_started_at))
        self.elapsed_label.configure(text=f"用时 {elapsed // 60:02d}:{elapsed % 60:02d}")
        self._tick_job = self.after(1000, self._tick_elapsed)

    # ----------------------------------------------------------------- events

    def _poll_events(self):
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.after(self.POLL_MS, self._poll_events)

    def _handle_event(self, kind, payload):
        if kind == "meta":
            self._parsing = False
            self.parse_btn.set_enabled(not self._busy)
            self._render_meta(payload)
            if not self._busy:
                self._set_state("已解析，可以下载", SUCCESS, "已解析")
                self._append_log(f"已解析：{payload.get('title', '')}")
        elif kind == "meta_error":
            self._parsing = False
            self.parse_btn.set_enabled(True)
            self._set_state("解析失败", DANGER, "解析失败")
            self._append_log("解析失败：" + payload.get("message", "").splitlines()[0])
            self._show_error("解析失败", payload)
        elif kind == "started":
            self._total_photos = int(payload.get("total_photos") or 0)
            self._last_save_path = str(payload.get("save_path") or "")
            self._set_state("正在下载", ACCENT, "下载中")
            if self._total_photos:
                self.progress_detail.configure(
                    text=f"共 {self._total_photos} 章 · 已下载 {self._progress_images} 张"
                )
            self._append_log(f"开始：{payload.get('title', '')}（{self._total_photos} 章）")
        elif kind == "progress":
            self._progress_images = int(payload.get("images") or 0)
            self._update_progress(
                int(payload.get("photos") or 0),
                int(payload.get("total_photos") or 0),
            )
        elif kind == "chapter":
            done = int(payload.get("done") or 0)
            total = int(payload.get("total") or 0)
            self._progress_images = int(payload.get("images") or 0)
            self._update_progress(done, total)
            name = str(payload.get("name") or "")
            if len(name) > 28:
                name = name[:28] + "…"
            self._append_log(
                f"章节 {done}/{total} 完成：{int(payload.get('index') or done):02d} {name}"
            )
        elif kind == "done":
            self._finish_download(payload)
        elif kind == "cancelled":
            self._set_busy(False)
            self._set_state("已取消", WARNING, "已取消")
            self.progress_detail.configure(text="任务已取消，已下载的图片会保留。")
            self._append_log("任务已取消。")
        elif kind == "error":
            self._set_busy(False)
            self._set_state("下载失败", DANGER, "下载失败")
            self.progress_detail.configure(text="下载未完成，详情见日志。")
            self._append_log("下载失败：" + payload.get("message", "").splitlines()[0])
            self._show_error("下载失败", payload)

    def _update_progress(self, done_photos, total_photos):
        if total_photos <= 0:
            return
        if total_photos == 1 and self.meta and int(self.meta.get("page_count") or 0) > 0:
            fraction = min(0.985, self._progress_images / max(1, int(self.meta["page_count"])))
        else:
            fraction = min(1.0, done_photos / total_photos)
        self.progress_bar["value"] = max(0.0, min(100.0, fraction * 100))
        detail = f"已下载 {self._progress_images} 张"
        if total_photos > 1:
            detail += f" · 章节 {done_photos}/{total_photos}"
        self.progress_detail.configure(text=detail)

    def _finish_download(self, payload):
        self._set_busy(False)
        self.progress_bar["value"] = 100
        self._set_state("下载完成", SUCCESS, "下载完成")
        images = int(payload.get("images") or self._progress_images or 0)
        save_path = str(payload.get("save_path") or "")
        self._last_save_path = save_path
        self.progress_detail.configure(text=f"共 {images} 张 · 已保存")
        self._append_log(f"下载完成：{images} 张 → {save_path}")
        exports = payload.get("exports") or []
        if exports:
            self._append_log(f"导出文件 {len(exports)} 个：{Path(exports[0]).parent}")

        self.meta = self.meta or {}
        title = payload.get("title") or self.meta.get("title") or "未命名"
        jm_id = ""
        try:
            jm_id = core.parse_input(self.input_var.get()).jm_id
        except ValueError:
            pass
        entry = {
            "key": f"{jm_id}:{save_path}",
            "title": title,
            "jm_id": jm_id,
            "time": time.strftime("%m-%d %H:%M"),
            "save_path": save_path,
            "status": f"完成 · {images} 张",
        }
        self.history = core.add_history(entry)
        self._render_history()
        self._set_state("下载完成", SUCCESS, "下载完成")
        messagebox.showinfo(
            "下载完成",
            f"已下载 {images} 张图片。\n\n保存位置：\n{save_path or self.save_var.get()}",
            parent=self,
        )

    def _show_error(self, title, payload):
        message = payload.get("message", "未知错误")
        log_path = payload.get("log_path", "")
        extra = f"\n\n错误记录：\n{log_path}" if log_path else ""
        messagebox.showerror(title, f"{message}{extra}", parent=self)

    # --------------------------------------------------------------- settings

    def _open_settings(self):
        SettingsDialog(self)

    def apply_settings(self, settings: core.Settings):
        old_dir = self.settings.download_dir
        self.settings = settings
        try:
            core.save_settings(settings)
        except OSError as error:
            messagebox.showerror("保存失败", f"无法保存设置：\n{error}", parent=self)
            return
        if str(self.save_var.get()).strip() == old_dir:
            self.save_var.set(settings.download_dir)
        self._set_state("设置已保存", SUCCESS, "就绪")
        self._append_log("设置已保存，下次任务生效。")

    # ------------------------------------------------------------------ close

    def _on_close(self):
        if self._busy and self.download_worker and self.download_worker.is_alive():
            if not messagebox.askyesno(
                "正在下载",
                "下载还在进行，确定要退出吗？\n已下载的图片会保留。",
                parent=self,
            ):
                return
            self.download_worker.cancel()
        self._closing = True
        try:
            self.settings.last_input = self.input_var.get().strip()[:500]
            self.settings.zip_enabled = bool(self.zip_var.get())
            self.settings.pdf_enabled = bool(self.pdf_var.get())
            self.settings.long_enabled = bool(self.long_var.get())
            self.settings.download_dir = self.save_var.get().strip() or self.settings.download_dir
            self.settings.window_geometry = f"{self.winfo_width()}x{self.winfo_height()}"
            core.save_settings(self.settings)
        except Exception:
            pass
        self.destroy()

    def _report_tk_exception(self, exc, value, tb):
        detail = "".join(traceback.format_exception(exc, value, tb))
        path = core.write_error_log(detail)
        try:
            messagebox.showerror(
                "界面错误",
                f"程序遇到一个界面错误：\n{value}\n\n错误记录：\n{path}",
                parent=self,
            )
        except Exception:
            pass


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("设置")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()

        width, height = 520, 558
        app.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - width) // 2
        y = app.winfo_rooty() + (app.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

        self.proxy_var = StringVar(value=app.settings.proxy)
        self.cookie_var = StringVar(value=app.settings.cookie)
        self.client_var = StringVar(value=app.settings.client_impl)
        self.image_threads_var = StringVar(value=str(app.settings.image_threads))
        self.photo_threads_var = StringVar(value=str(app.settings.photo_threads))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=18)

        tk.Label(
            body,
            text="设置",
            bg=BG,
            fg=TEXT,
            font=(app.ui_font, 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body,
            text="修改会在下一次任务开始时生效",
            bg=BG,
            fg=FAINT,
            font=(app.ui_font, 8),
        ).pack(anchor="w", pady=(2, 14))

        self._field_label(body, "网络代理")
        self._entry(body, self.proxy_var)
        tk.Label(
            body,
            text="留空或 system = 跟随系统；none = 直连；也可填 127.0.0.1:7890",
            bg=BG,
            fg=FAINT,
            font=(app.ui_font, 7),
        ).pack(anchor="w", pady=(3, 10))

        self._field_label(body, "AVS Cookie（可选）")
        self._entry(body, self.cookie_var)
        tk.Label(
            body,
            text="少数需要登录的作品才需要；只填 AVS 的值或完整 Cookie 均可",
            bg=BG,
            fg=FAINT,
            font=(app.ui_font, 7),
        ).pack(anchor="w", pady=(3, 10))

        self._field_label(body, "接口")
        clients = tk.Frame(body, bg=BG)
        clients.pack(fill="x")
        for text, value in (
            ("APP 接口（推荐）", "api"),
            ("网页端（可能受地区限制）", "html"),
        ):
            tk.Radiobutton(
                clients,
                text=text,
                value=value,
                variable=self.client_var,
                bg=BG,
                fg=MUTED,
                activebackground=BG,
                activeforeground=TEXT,
                selectcolor=INPUT,
                highlightthickness=0,
                bd=0,
                font=(app.ui_font, 9),
                cursor="hand2",
            ).pack(side="left", padx=(0, 16))

        threads = tk.Frame(body, bg=BG)
        threads.pack(fill="x", pady=(12, 0))
        left = tk.Frame(threads, bg=BG)
        left.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._field_label(left, "图片并发")
        self._spin(left, self.image_threads_var, 1, 50)
        right = tk.Frame(threads, bg=BG)
        right.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._field_label(right, "章节并发")
        self._spin(right, self.photo_threads_var, 1, 20)

        tk.Label(
            body,
            text="并发越高速度越快，也会给服务器带来更大压力；建议保持默认值。",
            bg=BG,
            fg=FAINT,
            font=(app.ui_font, 7),
            wraplength=460,
            justify="left",
        ).pack(anchor="w", pady=(7, 0))

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=24, pady=(0, 18))
        FlatButton(
            buttons,
            "取消",
            self.destroy,
            kind="secondary",
            font=(app.ui_font, 10),
            padx=18,
            pady=9,
        ).pack(side="right")
        FlatButton(
            buttons,
            "保存设置",
            self._save,
            kind="primary",
            font=(app.ui_font, 10, "bold"),
            padx=18,
            pady=9,
        ).pack(side="right", padx=(0, 9))
        self.bind("<Escape>", lambda _e: self.destroy())

    def _field_label(self, parent, text):
        tk.Label(
            parent,
            text=text,
            bg=BG,
            fg=TEXT,
            font=(self.app.ui_font, 9, "bold"),
        ).pack(anchor="w", pady=(0, 5))

    def _entry(self, parent, variable):
        wrap = tk.Frame(
            parent,
            bg=INPUT,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        wrap.pack(fill="x")
        entry = tk.Entry(
            wrap,
            textvariable=variable,
            bg=INPUT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(self.app.ui_font, 9),
        )
        entry.pack(fill="x", padx=10, pady=8)
        return entry

    def _spin(self, parent, variable, low, high):
        spin = tk.Spinbox(
            parent,
            from_=low,
            to=high,
            textvariable=variable,
            bg=INPUT,
            fg=TEXT,
            buttonbackground=CARD_ALT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            font=(self.app.ui_font, 9),
            width=8,
        )
        spin.pack(fill="x", ipady=6)

    def _save(self):
        try:
            image_threads = int(self.image_threads_var.get())
            photo_threads = int(self.photo_threads_var.get())
        except ValueError:
            messagebox.showwarning("数值无效", "并发数必须是整数。", parent=self)
            return
        if not (1 <= image_threads <= 50 and 1 <= photo_threads <= 20):
            messagebox.showwarning("数值超出范围", "图片并发 1-50，章节并发 1-20。", parent=self)
            return
        settings = replace(
            self.app.settings,
            proxy=self.proxy_var.get().strip() or "system",
            cookie=self.cookie_var.get().strip(),
            client_impl=self.client_var.get(),
            image_threads=image_threads,
            photo_threads=photo_threads,
        )
        self.app.apply_settings(settings)
        self.destroy()


def main():
    app = App()
    app.mainloop()
