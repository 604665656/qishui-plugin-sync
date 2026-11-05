# -*- coding: utf-8 -*-
"""plugin.py —— 数据同步与复盘 插件（页签 + 无界面任务）。

契约（host 约定）：
    setup(self, ctx)           宿主加载时调一次，拿到通行证 + 注册每日调度
    build(self, ctx) -> QWidget   返回一个页签控件（PySide6 只在里面 import）
    run_task(self) -> dict        无界面入口，可被计划任务/CLI 调用，必须不崩
    summary(self) -> str          给宿主状态栏用的简短摘要
    on_unload(self)               插件卸载/关页签时清理

硬规则：
    · 不 import 主程序；只用 ctx 与 qishui_core
    · 不自己拼账号路径；一律 ctx.account_* / ctx.store
    · PySide6 只在 build() 里 import
    · build() 全程防御式：任何异常都显示 QLabel，绝不带崩宿主
"""
from __future__ import annotations

import datetime

from . import engine_sync as engine


class SyncPlugin(object):
    def __init__(self):
        self.ctx = None
        self._cache_invalid = True
        self._status = None        # 顶部状态 QLabel
        self._sync_btn = None      # 立即同步 按钮
        self._progress = None      # 进度 QLabel
        self._review_edit = None   # 复盘 QTextEdit
        self._retro_edit = None    # 回顾 QTextEdit
        self._worker = None
        self._QThread = None
        self._Signal = None

    # ---------------------------------------------------------- setup
    def setup(self, ctx):
        self.ctx = ctx
        try:
            ctx.on_account_switch(self._on_switch)
        except Exception:
            pass
        # 注册每日调度（无界面时 ctx.schedule 可能为 None，先 guard）
        sched = getattr(ctx, "schedule", None)
        if sched is not None:
            try:
                sched.daily(self._scheduled_run, hm="07:30", name="sync",
                            title="数据同步与复盘")
            except Exception:
                pass

    def _on_switch(self, uid):
        # 切号后缓存作废，下次 build 重新拉取
        self._cache_invalid = True

    def _scheduled_run(self):
        # 计划任务入口：防御式，失败静默
        try:
            self.run_task()
        except Exception:
            pass

    # ---------------------------------------------------------- build
    def build(self, ctx):
        # ★ PySide6 只在这里 import（模块顶层不碰）
        try:
            from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                                           QLabel, QPushButton, QTextEdit,
                                           QFrame)
            from PySide6.QtCore import Qt, QThread, Signal
            from PySide6.QtGui import QFont
        except Exception as e:   # 没有 PySide6 / 环境异常 → 用 QLabel 兜底
            from PySide6 import QtWidgets  # noqa
            return QtWidgets.QLabel("无法加载界面依赖：%s" % e)

        try:
            return self._build_ui(ctx, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                  QPushButton, QTextEdit, QFrame, Qt, QThread,
                                  Signal, QFont)
        except Exception as e:
            # 任何构造异常都不许冒泡到宿主
            try:
                err = QLabel("「数据同步与复盘」界面加载失败：%s" % e)
                err.setWordWrap(True)
                return err
            except Exception:
                return QWidget()

    def _build_ui(self, ctx, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                  QPushButton, QTextEdit, QFrame, Qt, QThread, Signal, QFont):
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # ---------- 顶部：标题 + 状态 + 立即同步 ----------
        bar = QHBoxLayout()
        title = QLabel("数据同步与复盘")
        title.setFont(QFont("", 13))
        self._status = QLabel("")
        self._status.setStyleSheet("color:#888;")
        self._sync_btn = QPushButton("立即同步")
        self._sync_btn.clicked.connect(lambda: self._refresh(ctx))
        bar.addWidget(title)
        bar.addStretch(1)
        bar.addWidget(self._status)
        bar.addWidget(self._sync_btn)
        lay.addLayout(bar)

        # ---------- 进度条 ----------
        self._progress = QLabel("")
        self._progress.setStyleSheet("color:#666;")
        lay.addWidget(self._progress)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        lay.addWidget(line)

        # ---------- 复盘 / 回顾 两个文本区 ----------
        review = QTextEdit()
        review.setReadOnly(True)
        review.setLineWrapMode(QTextEdit.WidgetWidth)
        review.setFont(QFont("Consolas", 10))
        self._review_edit = review

        retro = QTextEdit()
        retro.setReadOnly(True)
        retro.setLineWrapMode(QTextEdit.WidgetWidth)
        retro.setFont(QFont("Consolas", 10))
        self._retro_edit = retro

        # 用两个垂直布局并排展示（左右分栏，宽屏更省纵向空间）
        hsplit = QHBoxLayout()
        vleft = QVBoxLayout()
        vleft.addWidget(QLabel("每日复盘"))
        vleft.addWidget(review, 1)
        vright = QVBoxLayout()
        vright.addWidget(QLabel("阶段回顾"))
        vright.addWidget(retro, 1)
        hsplit.addLayout(vleft)
        hsplit.addLayout(vright)
        lay.addLayout(hsplit, 1)

        # ---------- 先填缓存，再尝试刷新 ----------
        try:
            cached_review = engine.load_review(ctx)
            cached_retro = engine.load_retro(ctx)
            self._review_edit.setPlainText(cached_review)
            self._retro_edit.setPlainText(cached_retro)
            last = engine.load_last(ctx)
            sa = last.get("synced_at", "")
            if sa:
                self._set_status("已显示缓存（%s），可点「立即同步」刷新" % sa)
            else:
                self._set_status("暂无同步缓存，请点「立即同步」")
        except Exception:
            self._set_status("缓存读取失败，请点「立即同步」")

        self._QThread = QThread
        self._Signal = Signal
        return root

    # ---------------------------------------------------------- 数据填充
    def _set_status(self, text):
        if self._status is not None:
            try:
                self._status.setText(text)
            except Exception:
                pass

    def _set_progress(self, text):
        if self._progress is not None:
            try:
                self._progress.setText(text)
            except Exception:
                pass

    def _apply_review_retro(self, review, retro):
        try:
            if self._review_edit is not None and review:
                self._review_edit.setPlainText(review)
            if self._retro_edit is not None and retro:
                self._retro_edit.setPlainText(retro)
        except Exception:
            pass

    # ---------------------------------------------------------- 刷新（后台线程）
    def _refresh(self, ctx):
        if self._worker is not None and self._worker.isRunning():
            return  # 防连点
        try:
            from PySide6.QtCore import QThread, Signal  # noqa
        except Exception:
            # 极端情况：直接同步跑
            try:
                self._do_sync(ctx)
            except Exception:
                pass
            return

        Worker = self._make_worker(QThread, Signal, ctx)
        w = Worker()
        self._worker = w
        if self._sync_btn is not None:
            self._sync_btn.setEnabled(False)
        self._set_status("正在同步…")
        self._set_progress("后台拉取曲库/热词/流量/结算中…")
        w.finished.connect(lambda: self._on_refresh_done(ctx, w))
        w.start()

    def _make_worker(self, QThread, Signal, ctx):
        class _Worker(QThread):
            finished = Signal(object)
            def run(self_inner):
                try:
                    data = self._do_sync(ctx)
                except Exception as e:  # noqa: BLE001
                    data = {"ok": False, "errors": [str(e)], "updated": 0,
                            "review": "", "retro": ""}
                self.finished.emit(data)  # noqa
        return _Worker

    def _do_sync(self, ctx):
        """同步 + 复盘 + 回顾 的完整流程（在后台线程里跑）。"""
        report = engine.sync_all(ctx)
        ts = report.get("synced_at") or engine.ts_now()
        review = engine.build_review(ctx, ts)
        retro = engine.build_retro(ctx, ts)
        engine.save_review_retro(ctx, review, retro)
        report["review"] = review
        report["retro"] = retro
        return report

    def _on_refresh_done(self, ctx, w):
        data = getattr(w, "result", None)
        if data is None:
            data = {}
        try:
            self._apply_review_retro(data.get("review"), data.get("retro"))
        except Exception:
            pass
        if self._sync_btn is not None:
            try:
                self._sync_btn.setEnabled(True)
            except Exception:
                pass
        updated = data.get("updated", 0)
        errs = data.get("errors") or []
        if data.get("ok"):
            sa = data.get("synced_at", "")
            self._set_status("同步完成（%s，更新 %d 项）" % (sa, updated))
            self._set_progress("")
        else:
            msg = "同步失败（可能未登录/未开浏览器）"
            if errs:
                msg += "：%s" % ", ".join(errs[:3])
            self._set_status(msg)
            self._set_progress("")
        self._worker = None

    # ---------------------------------------------------------- 无界面任务
    def run_task(self):
        """无界面入口：全量同步 + 生成复盘/回顾，返回结果 dict。

        被计划任务或 `python -m sync --task` 调用。必须不崩：
        即使全部接口失败，也返回带 ok/updated/review/retro 的结构化 dict。
        """
        ctx = self.ctx
        if ctx is None:
            return {"ok": False, "updated": 0, "errors": ["ctx 未初始化"],
                    "review": "", "retro": ""}
        try:
            return self._do_sync(ctx)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "updated": 0, "errors": [str(e)],
                    "review": "", "retro": ""}

    # ---------------------------------------------------------- 摘要
    def summary(self):
        """给宿主状态栏用的简短摘要（无界面也可能被调用）。"""
        ctx = self.ctx
        if ctx is None:
            return "数据同步：未初始化"
        last = engine.load_last(ctx)
        sa = last.get("synced_at", "") or ""
        updated = last.get("updated", 0)
        # 只取 hh:mm
        hm = ""
        if sa:
            try:
                hm = sa.split(" ")[1] if " " in sa else sa
            except Exception:
                hm = sa
        if hm:
            return "数据同步：上次 %s，%d 项已更新" % (hm, updated)
        return "数据同步：尚未同步"

    # ---------------------------------------------------------- 卸载
    def on_unload(self):
        try:
            if self._worker is not None:
                self._worker.quit()
                self._worker.wait(1000)
        except Exception:
            pass
        self.ctx = None
