"""The setup and status window (tkinter, built into Python: nothing to install).

First run: connect this computer (a device key, recommended, or the owner's email sign-in); with no
printer set up yet, the printer window opens by itself and looks for printers (Windows printers, the
POS's connections, the network, USB). Afterwards it shows whether printing is working, with a red
warning when a printer keeps failing. Closing the window doesn't stop printing: the background
service keeps going.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from . import APP_TITLE, VERSION, api, config, discover, setup, winprint
from .deliver import describe as describe_connection
from .paths import IS_WINDOWS, log_dir, status_path
from .printer import PrinterError

ERRORS = {
    "invalid_key": "密钥无效、已使用或已过期，请在后台重新生成。\nThat key is wrong, used or expired: create a new one on the dashboard.",
    "invalid_code": "验证码不正确。\nThat code isn't right.",
    "invalid_email": "请输入有效的邮箱地址。\nEnter a valid email address.",
    "code_expired": "验证码已过期或已发送新的验证码，请重新发送。\nThat code expired or a newer one was sent: send a new code.",
    "too_many_attempts": "验证码错误次数过多，请重新发送。\nToo many wrong codes: send a new code.",
    "email_failed": "邮件发送失败，请检查邮箱地址，稍后再试。\nThe email couldn't be sent: check the address and try again in a minute.",
    "cooldown": "请稍等片刻再重新发送验证码。\nPlease wait a moment before sending another code.",
    "device_revoked": "这台电脑已在后台被注销，请重新连接。\nThis computer was signed out on the dashboard: connect it again.",
    "not_authorized": "只有餐厅老板可以连接打印电脑。\nOnly the restaurant's owner can connect a printer computer.",
    "invalid_input": "请检查填写的内容（IP 地址如 192.168.1.100）。\nPlease check the fields (IP address like 192.168.1.100).",
    "bad_server_config": "服务器地址不正确。\nThe server address isn't right.",
    "rate_limited": "尝试次数过多，请稍后再试。\nToo many attempts, please wait a moment.",
}


def explain(e: BaseException) -> str:
    if isinstance(e, api.NetworkError):
        return "无法连接服务器，请检查网络。\nCan't reach the server: check the internet connection.\n\n" + str(e)
    if isinstance(e, api.ApiError):
        for code, text in ERRORS.items():
            if code in e.code:
                return text
        return f"服务器错误 Server error: {e.code}"
    if isinstance(e, PrinterError):
        return f"打印失败 Print failed:\n{e}"
    return f"{type(e).__name__}: {e}"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.results: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.session: Optional[str] = None
        self.email = ""
        self.resend_at = 0.0
        root.title(APP_TITLE)
        root.geometry("600x680")
        root.minsize(520, 560)
        if IS_WINDOWS:
            root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Microsoft YaHei UI" if IS_WINDOWS else "TkDefaultFont", 15, "bold"))
        style.configure("Big.TLabel", font=("Microsoft YaHei UI" if IS_WINDOWS else "TkDefaultFont", 12))
        self.body = ttk.Frame(root, padding=16)
        self.body.pack(fill="both", expand=True)
        self.view = ""
        self.auto_opened = False
        self.dialog: Optional["PrinterDialog"] = None
        self.render()
        root.after(200, self._drain)
        root.after(1500, self._tick)

    # --- background calls ----------------------------------------------------------------

    def bg(self, fn: Callable[[], Any], ok: Callable[[Any], None], button: Optional[ttk.Button] = None, quiet: bool = False,
           error: Optional[Callable[[BaseException], None]] = None) -> None:
        """Runs fn in a thread (the window never freezes); ok(result) back on the window's thread."""
        if button is not None:
            button.state(["disabled"])

        def run() -> None:
            try:
                value = fn()
                self.results.put(lambda: ok(value))
            except Exception as e:  # shown to the user, logged by the caller's module
                err = e
                if error is not None:
                    self.results.put(lambda: error(err))
                elif not quiet:
                    self.results.put(lambda: messagebox.showerror(APP_TITLE, explain(err), parent=self.root))
            finally:
                if button is not None:
                    self.results.put(lambda: button.winfo_exists() and button.state(["!disabled"]))

        threading.Thread(target=run, daemon=True).start()

    def _drain(self) -> None:
        while not self.results.empty():
            self.results.get()()
        self.root.after(150, self._drain)

    def _tick(self) -> None:
        if self.view == "status":
            self.update_status()
        elif self.view == "connect" and config.signed_in(config.load()):
            self.render()
        self.root.after(2000, self._tick)

    # --- screens -------------------------------------------------------------------------

    def clear(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()

    def render(self) -> None:
        self.clear()
        cfg = config.load()
        if config.signed_in(cfg):
            self.view = "status"
            self.status_screen(cfg)
        else:
            self.view = "connect"
            self.connect_screen(cfg)

    def connect_screen(self, cfg: Dict[str, Any]) -> None:
        ttk.Label(self.body, text="连接这台电脑 Connect this computer", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, wraplength=540, text="新订单会自动从后厨小票机打印出来。粘贴后台生成的设备密钥即可连接，也可以用老板邮箱登录。\nNew orders print by themselves on the kitchen printers. Paste a device key from the dashboard, or sign in with the owner's email.").pack(anchor="w", pady=(4, 8))
        tip = tk.Label(self.body, wraplength=540, justify="left", anchor="w", bg="#fbf5e8", fg="#6b5220", padx=10, pady=8,
                       text="推荐：在后台 Settings > Kitchen printers（设置 → 后厨打印机）生成设备密钥，粘贴到这里。\nRecommended: create a device key on the dashboard in Settings > Kitchen printers, and paste it here.")
        tip.pack(fill="x", pady=(0, 10))
        tabs = ttk.Notebook(self.body)
        tabs.pack(fill="both", expand=True)

        key_tab = ttk.Frame(tabs, padding=14)
        tabs.add(key_tab, text="设备密钥（推荐） Device key (recommended)")
        ttk.Label(key_tab, wraplength=520, text="在后台 设置 → 后厨打印机 → “生成设备密钥”，复制（或用手机扫码）后粘贴到下面。\nOn the dashboard: Settings > Kitchen printers > Create device key. Copy it (or scan its QR code with a phone) and paste it below.").pack(anchor="w", pady=(0, 8))
        ttk.Label(key_tab, text="设备密钥 Device key (xpk_…)").pack(anchor="w")
        key = ttk.Entry(key_tab, width=48)
        key.pack(fill="x", pady=(4, 10))
        row_k = ttk.Frame(key_tab)
        row_k.pack(anchor="w")

        def paste() -> None:
            try:
                text = self.root.clipboard_get()
            except tk.TclError:
                text = ""
            key.delete(0, "end")
            key.insert(0, setup.clean_key(text))

        ttk.Button(row_k, text="粘贴 Paste", command=paste).pack(side="left")
        btn = ttk.Button(row_k, text="连接 Connect")
        btn.configure(command=lambda: self.bg(lambda: setup.pair_with_key(key.get()), lambda _: self.connected(), btn) if key.get().strip() else None)
        btn.pack(side="left", padx=6)

        email_tab = ttk.Frame(tabs, padding=14)
        tabs.add(email_tab, text="邮箱登录 Email sign-in")
        ttk.Label(email_tab, wraplength=520, text="用老板在后台登录的邮箱，我们会发送 6 位验证码。\nUse the email the owner signs in to the dashboard with: we'll email a 6-digit code.").pack(anchor="w", pady=(0, 8))
        ttk.Label(email_tab, text="邮箱 Email").pack(anchor="w")
        email = ttk.Entry(email_tab, width=40)
        email.pack(fill="x", pady=(4, 0))
        row_s = ttk.Frame(email_tab)
        row_s.pack(fill="x", pady=(10, 4))
        send = ttk.Button(row_s, text="发送验证码 Send code")
        send.pack(side="left")
        sent = ttk.Label(email_tab, wraplength=520, foreground="#555", text="")
        sent.pack(anchor="w")
        ttk.Label(email_tab, text="邮箱验证码 Email code").pack(anchor="w", pady=(8, 0))
        code = ttk.Entry(email_tab, width=10)
        code.pack(anchor="w", pady=(4, 10))
        login = ttk.Button(email_tab, text="登录 Sign in")
        login.pack(anchor="w")
        pick = ttk.Frame(email_tab)
        pick.pack(fill="x", pady=(14, 0))

        def tick() -> None:
            if not send.winfo_exists():
                return
            left = int(self.resend_at - time.time() + 0.999)
            if left > 0:
                send.configure(text=f"重新发送 Resend ({left}s)", state="disabled")
                self.root.after(1000, tick)
            else:
                send.configure(text="重新发送 Resend code" if self.email else "发送验证码 Send code", state="normal")

        def on_sent(result: Dict[str, Any]) -> None:
            self.resend_at = time.time() + float(result.get("cooldownSeconds") or 60)
            minutes = result.get("expiresInMinutes") or 10
            sent.configure(text=f"验证码已发送到 {self.email}，{minutes} 分钟内有效。没收到请检查垃圾邮件文件夹。\nCode sent to {self.email} (works for {minutes} min). Not there? Check the spam or junk folder.")
            # After bg() re-enables the button (queued right behind this callback).
            self.root.after(50, tick)

        def do_send() -> None:
            try:
                self.email = setup.normalize_email(email.get())
            except api.ApiError as e:
                messagebox.showerror(APP_TITLE, explain(e), parent=self.root)
                return
            to = self.email
            self.bg(lambda: setup.send_code(to), on_sent, send)

        def do_login() -> None:
            to = self.email or email.get()

            def work() -> List[Dict[str, Any]]:
                self.session = setup.sign_in(to, code.get())
                return setup.owned_restaurants(self.session)

            self.bg(work, lambda rs: self.choose_restaurant(pick, rs), login)

        send.configure(command=do_send)
        login.configure(command=do_login)

        adv = ttk.Frame(self.body)
        adv.pack(fill="x", pady=(10, 0))
        ttk.Label(adv, text=f"服务器 Server: {cfg.get('site')}   ·   v{VERSION}", foreground="#777").pack(anchor="w")

    def choose_restaurant(self, frame: ttk.Frame, restaurants: List[Dict[str, Any]]) -> None:
        for w in frame.winfo_children():
            w.destroy()
        if not restaurants:
            messagebox.showerror(APP_TITLE, ERRORS["not_authorized"], parent=self.root)
            return
        names = [self._name(r.get("name")) for r in restaurants]
        ttk.Label(frame, text="选择餐厅 Choose the restaurant").pack(anchor="w")
        combo = ttk.Combobox(frame, values=names, state="readonly", width=40)
        combo.current(0)
        combo.pack(anchor="w", pady=(4, 8))
        btn = ttk.Button(frame, text="用这家餐厅 Use this restaurant")
        session = self.session or ""
        btn.configure(command=lambda: self.bg(lambda: setup.register(session, restaurants[combo.current()]["id"]), lambda _: self.connected(), btn))
        btn.pack(anchor="w")
        if len(restaurants) == 1:
            btn.invoke()

    def connected(self) -> None:
        self.session = None
        self.bg(setup.refresh, lambda _: self.render(), error=lambda _e: self.render())

    @staticmethod
    def _name(value: Any) -> str:
        if isinstance(value, dict):
            return " ".join(x for x in (value.get("zh"), value.get("en")) if x)
        return str(value or "")

    def status_screen(self, cfg: Dict[str, Any]) -> None:
        ttk.Label(self.body, text=self._name((cfg.get("restaurant") or {}).get("name")) or "XOrdering", style="Title.TLabel").pack(anchor="w")
        self.status_label = ttk.Label(self.body, style="Big.TLabel", wraplength=540)
        self.status_label.pack(anchor="w", pady=(6, 2))
        self.queue_label = ttk.Label(self.body, foreground="#555")
        self.queue_label.pack(anchor="w")
        # A printer that keeps failing: a red warning with the fix.
        self.alert = tk.Frame(self.body, bg="#fde8e6", padx=10, pady=8)
        self.alert_text = tk.Label(self.alert, bg="#fde8e6", fg="#8c1d18", justify="left", anchor="w", wraplength=440, font=("Microsoft YaHei UI" if IS_WINDOWS else "TkDefaultFont", 10, "bold"))
        self.alert_text.pack(side="left", fill="x", expand=True)
        self.alert_btn = ttk.Button(self.alert, text="修复 Fix", command=self.fix_stuck)
        self.alert_btn.pack(side="right")
        self.alert_shown = False

        box = ttk.LabelFrame(self.body, text=" 打印机 Printers ", padding=10)
        box.pack(fill="both", expand=True, pady=(14, 8))
        cols = ("name", "host", "paper", "mode", "state")
        self.status_cols = cols
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=6, selectmode="browse")
        for c, title, w in zip(cols, ("名称 Name", "连接 Connection", "纸宽 Paper", "类型 Type", "状态 Status"), (100, 150, 55, 100, 150)):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self.edit_printer())
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="添加 Add", command=lambda: self.edit_printer(new=True)).pack(side="left")
        ttk.Button(buttons, text="修改 Edit", command=self.edit_printer).pack(side="left", padx=6)
        self.conn_btn = ttk.Button(buttons, text="测试连接 Test", command=self.test_connection)
        self.conn_btn.pack(side="left")
        self.test_btn = ttk.Button(buttons, text="打印测试页 Test page", command=self.test_print)
        self.test_btn.pack(side="left", padx=6)
        self.preview_btn = ttk.Button(buttons, text="预览小票 Preview", command=self.preview)
        self.preview_btn.pack(side="left")
        self.conn_label = tk.Label(box, text="", anchor="w", justify="left", wraplength=520)
        self.conn_label.pack(anchor="w", pady=(6, 0))
        ttk.Label(box, wraplength=520, foreground="#555", text="每台开启的打印机都会打印完整订单。“添加 / 修改”会自动查找打印机。网络打印机建议在路由器上设固定 IP（DHCP 保留）。\nEach enabled printer prints the whole order. Add / Edit look for printers by themselves. Give a network printer a fixed IP on the router (DHCP reservation).").pack(anchor="w", pady=(8, 0))

        foot = ttk.Frame(self.body)
        foot.pack(fill="x", pady=(6, 0))
        ttk.Button(foot, text="打开日志 Logs", command=self.open_logs).pack(side="left")
        ttk.Button(foot, text="诊断报告 Diagnostics", command=lambda: ReportWindow(self, self.root, None)).pack(side="left", padx=(6, 0))
        ttk.Button(foot, text="断开此电脑 Disconnect", command=self.disconnect).pack(side="left", padx=6)
        ttk.Label(self.body, wraplength=540, foreground="#777", text=f"关闭此窗口后打印会在后台继续，开机自动运行。Printing continues in the background after you close this window, and starts with Windows.   v{VERSION}").pack(anchor="w", pady=(10, 0))
        self.update_status()
        if not cfg.get("printers") and not self.auto_opened:
            # First run: no printer yet. Open the printer window, which starts looking for printers.
            self.auto_opened = True
            self.root.after(300, lambda: self.edit_printer(new=True))

    def update_status(self) -> None:
        cfg = config.load()
        if not config.signed_in(cfg):
            self.render()
            return
        try:
            st = json.loads(status_path().read_text("utf-8"))
        except (OSError, ValueError):
            st = {}
        fresh = time.time() - float(st.get("updatedAt") or 0) < 30
        state = st.get("state") if fresh else "stopped"
        text = {
            "ok": "● 正常运行，等待新订单 Running: waiting for orders",
            "offline": "● 网络断开：订单会在恢复后自动补打 Offline: orders print when the connection is back",
            "revoked": "● 这台电脑已在后台注销，请断开后重新连接 Signed out on the dashboard: disconnect and connect again",
            "setup": "● 正在启动 Starting…",
            "stopped": "● 后台程序未运行，正在启动 Background service not running: starting it…",
        }.get(state or "stopped", state or "")
        color = {"ok": "#1b7f3b", "offline": "#a86400", "revoked": "#b3261e"}.get(state or "", "#555")
        self.status_label.configure(text=text, foreground=color)
        q = st.get("queue") or {}
        self.queue_label.configure(text=f"待打印 Waiting: {q.get('queued', 0)}   ·   已打印 Printed (14 days): {q.get('printed', 0)}")
        if state == "stopped":
            from .install import ensure_running

            ensure_running()
        printer_state = st.get("printers") or {}
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        printers = config.effective_printers(cfg)
        for p in printers:
            ps = printer_state.get(p["id"]) or {}
            s = "已关闭 Off" if not p.get("enabled", True) else "● 正常 OK" if ps.get("ok") else f"● {ps.get('error')}" if ps.get("error") else "—"
            if ps.get("ok") and ps.get("paperLow"):
                s = "● 纸快用完 Paper low"
            where = f"Windows: {p.get('windowsPrinter')}" if p.get("connection") == "windows" else f"{p.get('host')}:{p.get('port', 9100)}"
            self.tree.insert("", "end", iid=p["id"], values=(p.get("name"), where, f"{80 if p.get('render') == 'star' else p.get('paperWidth', 80)} mm", MODES.get(p.get("render") or "text", ""), s))
        self.show_alert(printers, printer_state if fresh else {}, st.get("queue") or {})
        for iid in selected:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
        if not self.tree.selection() and self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])

    def show_alert(self, printers: List[Dict[str, Any]], state: Dict[str, Any], queue: Dict[str, Any]) -> None:
        stuck = [(p, state[p["id"]]) for p in printers if p.get("enabled", True) and (state.get(p["id"]) or {}).get("stuck")]
        if not stuck:
            if self.alert_shown:
                self.alert.pack_forget()
                self.alert_shown = False
            return
        p, ps = stuck[0]
        minutes = max(1, int((time.time() - float(ps.get("failingSince") or time.time())) // 60))
        waiting = queue.get("queued", 0)
        tip = ("建议改用“Windows 已安装的打印机”方式（点“修复”）。Tip: switch to the Windows printer connection (Fix)."
               if p.get("connection") != "windows" else "请检查打印机电源、纸和线，或点“修复”重新侦测。Check power, paper and cable, or press Fix to look for it again.")
        self.alert_text.configure(text=f"⚠ {p.get('name')} 已经 {minutes} 分钟打不出来 hasn't printed for {minutes} min: {ps.get('error')}\n"
                                       f"{waiting} 张订单在排队，不会丢失，打印机恢复后自动补打。{waiting} ticket(s) wait and print once it works again.\n{tip}")
        self.alert_stuck_id = p["id"]
        if not self.alert_shown:
            self.alert.pack(fill="x", pady=(8, 0), after=self.queue_label)
            self.alert_shown = True

    def fix_stuck(self) -> None:
        pid = getattr(self, "alert_stuck_id", None)
        printer = next((p for p in config.effective_printers(config.load()) if p["id"] == pid), None)
        if printer is None:
            return
        last = setup.last_discovery()
        fresh = last if last and time.time() - float(last.get("at") or 0) < 1800 else None
        self.open_dialog(printer, discovery=fresh, autodetect=fresh is None)

    def selected_printer(self) -> Optional[Dict[str, Any]]:
        sel = self.tree.selection()
        return next((p for p in config.effective_printers(config.load()) if sel and p["id"] == sel[0]), None)

    def edit_printer(self, new: bool = False) -> None:
        cfg = config.load()
        printer = None if new else self.selected_printer()
        if printer is None:
            n = len(cfg.get("printers") or []) + 1
            printer = {"name": f"后厨 Kitchen {n}", "host": "", "port": 9100, "paperWidth": 80, "render": "star", "enabled": True,
                       "connection": "windows" if IS_WINDOWS else "tcp"}
        self.open_dialog(printer)

    def open_dialog(self, printer: Dict[str, Any], discovery: Optional[Dict[str, Any]] = None, autodetect: bool = True) -> None:
        if self.dialog is not None and self.dialog.top.winfo_exists():
            self.dialog.top.lift()
            return
        self.dialog = PrinterDialog(self, printer, discovery=discovery, autodetect=autodetect)

    def test_print(self) -> None:
        p = self.selected_printer()
        if not p:
            messagebox.showinfo(APP_TITLE, "请先选择或添加打印机。Select or add a printer first.", parent=self.root)
            return
        def failed(e: BaseException) -> None:
            text = explain_printer_error(e) if isinstance(e, PrinterError) else explain(e)
            messagebox.showerror(APP_TITLE, f"测试页没有打出来 The test page didn't print:\n\n{text}", parent=self.root)

        self.bg(lambda: setup.test_print(p), lambda _: messagebox.showinfo(APP_TITLE, f"已发送测试页到 {p['name']}（两张：普通单和预约单）。\nTest page sent to {p['name']} (two tickets: ASAP and scheduled).\n\n打不出来？Star TSP100 系列请选“Star 光栅”；中文乱码请选“图片”。\nNothing printed? Star TSP100: choose Star raster. Garbled Chinese: choose Picture.", parent=self.root), self.test_btn, error=failed)

    def test_connection(self) -> None:
        p = self.selected_printer()
        if not p:
            messagebox.showinfo(APP_TITLE, "请先选择或添加打印机。Select or add a printer first.", parent=self.root)
            return
        self.conn_label.configure(text=f"正在打印测试小票 Printing a test ticket ({describe_connection(p)})…", fg="#555")
        run_probe(self, p, self.conn_label, self.conn_btn)

    def preview(self) -> None:
        def opened(path: str) -> None:
            if IS_WINDOWS:
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

        self.bg(setup.preview_file, opened, self.preview_btn)

    def open_logs(self) -> None:
        path = str(log_dir())
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def disconnect(self) -> None:
        if messagebox.askyesno(APP_TITLE, "断开后这台电脑将不再打印订单，需要重新连接。确定吗？\nThis computer stops printing until it is connected again. Disconnect?", parent=self.root):
            setup.disconnect()
            self.render()


class PrinterDialog:
    """One printer's settings, with the printers this computer can find listed beside them.

    Opening it starts looking for printers in the background (discover.run): the list fills in with
    each step's progress and the window stays usable. A single real receipt printer found for a new
    printer is picked and filled in by itself: the user only presses Test connection and Save.
    """

    def __init__(self, app: App, printer: Dict[str, Any], discovery: Optional[Dict[str, Any]] = None, autodetect: bool = True) -> None:
        self.app = app
        self.printer = printer
        self.is_new = not printer.get("id")
        self.discovery: Optional[Dict[str, Any]] = None
        self.candidates: List[Dict[str, Any]] = []
        self.searching = False
        self.touched = False
        self.auto_key: Optional[str] = None
        self.win_printers: List[Dict[str, Any]] = []
        top = self.top = tk.Toplevel(app.root)
        top.title("打印机 Printer")
        top.transient(app.root)
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry(f"{min(960, sw - 40)}x{min(660, sh - 80)}")
        top.minsize(min(760, sw - 40), min(520, sh - 80))
        top.grab_set()
        outer = ttk.Frame(top, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        # --- left: the settings ---
        f = ttk.Frame(outer)
        f.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        ttk.Label(f, text="名称 Name").pack(anchor="w")
        self.name = ttk.Entry(f, width=34)
        self.name.insert(0, printer.get("name", ""))
        self.name.pack(anchor="w", fill="x", pady=(2, 8))

        ttk.Label(f, text="连接方式 Connection").pack(anchor="w")
        self.connection = tk.StringVar(value="windows" if printer.get("connection") == "windows" else "tcp")
        for value, label in (("windows", "Windows 已安装的打印机 Windows printer"), ("tcp", "网络 IP 直连 Network IP (direct)")):
            ttk.Radiobutton(f, text=label, value=value, variable=self.connection, command=self.sync_connection).pack(anchor="w")

        self.conn_box = ttk.Frame(f)
        self.conn_box.pack(fill="x", pady=(6, 8))
        # Windows printer
        self.win_frame = ttk.Frame(self.conn_box)
        ttk.Label(self.win_frame, text="Windows 打印机 Windows printer").pack(anchor="w")
        self.win_name = ttk.Combobox(self.win_frame, width=34, values=[printer.get("windowsPrinter")] if printer.get("windowsPrinter") else [])
        self.win_name.set(printer.get("windowsPrinter", ""))
        self.win_name.pack(anchor="w", fill="x", pady=(2, 2))
        self.win_name.bind("<<ComboboxSelected>>", lambda _e: self.on_windows_printer())
        self.win_info = ttk.Label(self.win_frame, foreground="#555", wraplength=300, text="")
        self.win_info.pack(anchor="w")
        self.to_ip_btn = ttk.Button(self.win_frame, text="改为直连 IP Use its IP directly", command=self.use_ip_directly)
        # Network IP
        self.tcp_frame = ttk.Frame(self.conn_box)
        ttk.Label(self.tcp_frame, text="IP 地址 IP address (192.168.x.x)").pack(anchor="w")
        self.host = ttk.Entry(self.tcp_frame, width=34)
        self.host.insert(0, printer.get("host", "") if printer.get("connection") != "windows" else "")
        self.host.pack(anchor="w", fill="x", pady=(2, 4))
        ttk.Label(self.tcp_frame, text="端口 Port").pack(anchor="w")
        self.port = ttk.Entry(self.tcp_frame, width=10)
        self.port.insert(0, str(printer.get("port", 9100)))
        self.port.pack(anchor="w", pady=(2, 0))

        ttk.Label(f, text="打印机类型 Printer type").pack(anchor="w")
        self.render = tk.StringVar(value=printer.get("render", "star"))
        for value, label in (("star", "Star 光栅 (TSP100III / TSP143)"), ("image", "ESC/POS 图片 Picture"), ("text", "ESC/POS 文字 Text (GBK)")):
            ttk.Radiobutton(f, text=label, value=value, variable=self.render, command=self.sync_paper).pack(anchor="w")
        ttk.Label(f, text="纸宽 Paper width").pack(anchor="w", pady=(6, 0))
        self.paper = tk.StringVar(value=str(printer.get("paperWidth", 80)))
        pr = ttk.Frame(f)
        pr.pack(anchor="w")
        self.paper_buttons = [ttk.Radiobutton(pr, text=f"{v} mm", value=v, variable=self.paper) for v in ("80", "58")]
        for btn in self.paper_buttons:
            btn.pack(side="left", padx=(0, 10))
        self.enabled = tk.BooleanVar(value=bool(printer.get("enabled", True)))
        ttk.Checkbutton(f, text="打印新订单 Print new orders", variable=self.enabled).pack(anchor="w", pady=(8, 0))

        self.probe_btn = ttk.Button(f, text="测试连接（打印一张测试小票）Test connection", command=self.probe)
        self.probe_btn.pack(anchor="w", fill="x", pady=(12, 4))
        self.result = tk.Label(f, text="", anchor="w", justify="left", fg="#555", wraplength=320)
        self.result.pack(anchor="w", fill="x")
        b = ttk.Frame(f)
        b.pack(anchor="w", pady=(10, 0))
        self.save_btn = ttk.Button(b, text="保存 Save", command=self.save)
        self.save_btn.pack(side="left")
        ttk.Button(b, text="取消 Cancel", command=top.destroy).pack(side="left", padx=6)

        # --- right: what was found ---
        r = ttk.LabelFrame(outer, text=" 找到的打印机 Printers found ", padding=10)
        r.grid(row=0, column=1, sticky="nsew")
        r.columnconfigure(0, weight=1)
        r.rowconfigure(2, weight=1)
        self.search_label = tk.Label(r, text="", anchor="w", justify="left", fg="#555", wraplength=520)
        self.search_label.grid(row=0, column=0, sticky="we")
        self.bar = ttk.Progressbar(r, mode="determinate", maximum=len(discover.STEPS))
        self.bar.grid(row=1, column=0, sticky="we", pady=(4, 6))
        cols = ("printer", "address", "source")
        self.found = ttk.Treeview(r, columns=cols, show="headings", height=8, selectmode="browse")
        for c, title, w in zip(cols, ("打印机 Printer", "地址 Address", "来源 Found as"), (200, 120, 200)):
            self.found.heading(c, text=title)
            self.found.column(c, width=w, anchor="w")
        self.found.tag_configure("best", background="#e9f6ec")
        self.found.tag_configure("dim", foreground="#888")
        self.found.grid(row=2, column=0, sticky="nsew")
        self.found.bind("<<TreeviewSelect>>", lambda _e: self.pick_found())
        tools = ttk.Frame(r)
        tools.grid(row=3, column=0, sticky="we", pady=(8, 0))
        self.search_btn = ttk.Button(tools, text="重新侦测 Detect again", command=self.search)
        self.search_btn.pack(side="left")
        ttk.Button(tools, text="诊断报告 Diagnostics", command=self.show_report).pack(side="left", padx=6)
        ttk.Label(r, wraplength=520, foreground="#666", text="点选一台即可填好设置。网络打印机建议在路由器上设固定 IP。\nClick one to fill in its settings. Give a network printer a fixed IP on the router.").grid(row=4, column=0, sticky="w", pady=(8, 0))

        for w in (self.name, self.host, self.port, self.win_name):
            w.bind("<Key>", lambda _e: self.mark_touched())
        self.sync_connection()
        self.sync_paper()
        self.load_windows_printers()
        if discovery:
            self.show_found(discovery)
        if autodetect:
            self.search()

    # --- form ---

    def mark_touched(self) -> None:
        self.touched = True

    def sync_connection(self) -> None:
        self.win_frame.pack_forget()
        self.tcp_frame.pack_forget()
        (self.win_frame if self.connection.get() == "windows" else self.tcp_frame).pack(fill="x")
        self.on_windows_printer()

    def sync_paper(self) -> None:
        star = self.render.get() == "star"
        if star:
            self.paper.set("80")
        for btn in self.paper_buttons:
            btn.state(["disabled"] if star else ["!disabled"])

    def win_printer_info(self, name: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.win_printers if p["name"] == name), None)

    def on_windows_printer(self) -> None:
        info = self.win_printer_info(self.win_name.get())
        self.to_ip_btn.pack_forget()
        if not info:
            self.win_info.configure(text="")
            return
        where = f"{info['ip']}:{info.get('tcpPort') or 9100}" if info.get("ip") else info.get("port", "")
        self.win_info.configure(text=f"驱动 Driver: {info.get('driver', '')}\n端口 Port: {where}" + (f"\n状态 Status: {info['status']}" if info.get("status") else ""))
        if info.get("ip"):
            self.to_ip_btn.pack(anchor="w", pady=(4, 0))

    def use_ip_directly(self) -> None:
        info = self.win_printer_info(self.win_name.get())
        if not info or not info.get("ip"):
            return
        self.host.delete(0, "end")
        self.host.insert(0, info["ip"])
        self.port.delete(0, "end")
        self.port.insert(0, str(info.get("tcpPort") or 9100))
        self.connection.set("tcp")
        self.sync_connection()
        self.result.configure(text=f"已改为直连 {info['ip']}:{info.get('tcpPort') or 9100}。请点“测试连接”。\nSwitched to the IP directly: press Test connection.", fg="#555")

    def load_windows_printers(self) -> None:
        if not IS_WINDOWS:
            return

        def done(printers: List[Dict[str, Any]]) -> None:
            if self.top.winfo_exists():
                self.set_windows_printers(printers)

        self.app.bg(lambda: winprint.list_printers(), done, quiet=True)

    def set_windows_printers(self, printers: List[Dict[str, Any]]) -> None:
        self.win_printers = [p for p in printers if not p.get("virtual")]
        names = [p["name"] for p in sorted(self.win_printers, key=lambda p: (not discover.guess(p["name"] + " " + p.get("driver", ""))["receipt"], p["name"].lower()))]
        current = self.win_name.get()
        if current and current not in names:
            names.insert(0, current)
        self.win_name.configure(values=names)
        self.on_windows_printer()

    def form(self) -> Dict[str, Any]:
        """The printer as filled in (not saved)."""
        try:
            port = int(self.port.get().strip() or "9100")
        except ValueError:
            port = -1
        windows = self.connection.get() == "windows"
        win = self.win_printer_info(self.win_name.get()) or {}
        host = (win.get("ip") or "") if windows else self.host.get().strip()
        p = dict(self.printer, name=self.name.get().strip(), host=host, port=int(win.get("tcpPort") or 9100) if windows else port,
                 paperWidth=80 if self.render.get() == "star" else int(self.paper.get()), render=self.render.get(), enabled=self.enabled.get(),
                 assignment=self.printer.get("assignment") or "full", connection="windows" if windows else "tcp",
                 windowsPrinter=self.win_name.get().strip() if windows else "")
        if not windows or p["windowsPrinter"] != self.printer.get("windowsPrinter"):
            p.pop("windowsMethod", None)
        return p

    def apply(self, c: Dict[str, Any], announce: str = "") -> None:
        """Fills the form from a found printer."""
        s = discover.settings_for(c)
        if s["connection"] == "windows":
            if s["windowsPrinter"] not in (self.win_name.cget("values") or ()):
                self.win_name.configure(values=[s["windowsPrinter"], *(self.win_name.cget("values") or ())])
            self.win_name.set(s["windowsPrinter"])
            if not self.win_printer_info(s["windowsPrinter"]):
                self.win_printers.append({"name": c["windowsPrinter"], "driver": c["driver"], "port": c["portName"], "ip": c["ip"], "tcpPort": c["port"] if c["ip"] else None, "status": c.get("winStatus", "")})
        if s["host"]:
            self.host.delete(0, "end")
            self.host.insert(0, s["host"])
            self.port.delete(0, "end")
            self.port.insert(0, str(s["port"]))
        if s["connection"]:
            self.connection.set(s["connection"])
        self.render.set(s["render"])
        self.paper.set(str(s["paperWidth"]))
        self.sync_connection()
        self.sync_paper()
        kind = MODES.get(s["render"], "")
        text = announce or "已填入，请点“测试连接”再“保存”。Filled in: press Test connection, then Save."
        self.result.configure(text=f"{c['name']} · {discover.address_text(c)} · {kind}\n{text}", fg="#1b5e20")

    # --- finding printers ---

    def search(self) -> None:
        if self.searching:
            return
        self.searching = True
        self.search_btn.state(["disabled"])
        self.bar.configure(value=0)
        self.search_label.configure(text="正在查找打印机… Looking for printers…", fg="#555")
        if not self.candidates:
            self.found.delete(*self.found.get_children())

        def progress(step: int, total: int, text: str) -> None:
            def show() -> None:
                if self.top.winfo_exists():
                    self.bar.configure(value=step - 1 + 0.5)
                    self.search_label.configure(text=f"正在查找打印机… Looking for printers… ({step}/{total})\n{text}")
            self.app.results.put(show)

        def done(res: Dict[str, Any]) -> None:
            self.searching = False
            if not self.top.winfo_exists():
                return
            self.search_btn.state(["!disabled"])
            self.bar.configure(value=len(discover.STEPS))
            self.show_found(res, auto=True)

        def failed(_e: BaseException) -> None:
            self.searching = False
            if self.top.winfo_exists():
                self.search_btn.state(["!disabled"])
                self.search_label.configure(text="查找出错，请点“诊断报告”。Looking for printers failed: see Diagnostics.", fg="#b3261e")

        self.app.bg(lambda: setup.find_printers(progress), done, error=failed)

    def show_found(self, res: Dict[str, Any], auto: bool = False) -> None:
        self.discovery = res
        self.candidates = res.get("candidates") or []
        if res.get("windowsPrinters"):
            self.set_windows_printers(res["windowsPrinters"])
        receipt = discover.receipt_printers(self.candidates)
        self.found.delete(*self.found.get_children())
        for i, c in enumerate(self.candidates):
            label = c["name"] + (f" ({c['brand']})" if c["brand"] and c["brand"].lower() not in c["name"].lower() else "")
            tag = "best" if c in receipt[:1] else ("dim" if c not in receipt else "")
            self.found.insert("", "end", iid=str(i), values=(label, discover.address_text(c), discover.source_text(c)), tags=(tag,) if tag else ())
        n = len(receipt)
        busy = [c for c in receipt if "in_use" in c["sources"]]
        if n == 0:
            self.search_label.configure(fg="#b3261e", text=(
                "没有找到小票打印机。请确认打印机开机、插好网线或 USB；USB 打印机需要先在 Windows 里装好驱动。点“诊断报告”可以复制详细信息发给我们。\n"
                "No receipt printer found. Is it on and cabled (network or USB)? A USB printer needs its Windows driver. Diagnostics has the details to send us."))
        else:
            extra = f"  {busy[0]['name']} 正被 {busy[0]['process'] or '其他程序'} 使用 (in use by {busy[0]['process'] or 'another program'})" if busy else ""
            self.search_label.configure(fg="#1b7f3b", text=f"找到 {n} 台小票打印机（最像的排在最前）Found {n} receipt printer{'s' if n != 1 else ''}, best first.{extra}")
        # Highlight the printer this one is set to, or the best match.
        current = next((i for i, c in enumerate(self.candidates) if (self.printer.get("windowsPrinter") and c["windowsPrinter"] == self.printer.get("windowsPrinter"))
                        or (self.printer.get("host") and c["ip"] == self.printer.get("host"))), None)
        if current is not None:
            self.found.selection_set(str(current))
            self.found.see(str(current))
        elif auto and self.is_new and not self.touched and n == 1 and not self.host.get().strip() and not self.win_name.get().strip():
            idx = self.candidates.index(receipt[0])
            self.auto_key = receipt[0]["key"]
            self.found.selection_set(str(idx))
            self.apply(receipt[0], "已自动选好并填好设置，请点“测试连接”，再点“保存”。\nPicked and filled in for you: press Test connection, then Save.")

    def pick_found(self) -> None:
        sel = self.found.selection()
        if not sel:
            return
        c = self.candidates[int(sel[0])]
        if c["key"] == self.auto_key:  # the selection made by the auto-pick itself: already filled in
            self.auto_key = None
            return
        if not c["connection"]:
            self.result.configure(text="这台 USB 打印机还没有 Windows 驱动：先安装打印机驱动，再点“重新侦测”。\nThis USB printer has no Windows driver yet: install it, then Detect again.", fg="#b3261e")
            return
        self.apply(c)

    def show_report(self) -> None:
        if self.discovery:
            ReportWindow(self.app, self.top, setup.diagnostics(self.discovery))
            return
        ReportWindow(self.app, self.top, None)

    # --- test, save ---

    def probe(self) -> None:
        p = self.form()
        if p["connection"] == "tcp" and not p["host"]:
            self.result.configure(text="请先填 IP 地址或选一台打印机。Enter the IP address or pick a printer first.", fg="#b3261e")
            return
        if p["connection"] == "windows" and not p["windowsPrinter"]:
            self.result.configure(text="请先选择 Windows 打印机。Choose the Windows printer first.", fg="#b3261e")
            return
        self.result.configure(text="正在打印测试小票… Printing a test ticket…", fg="#555")
        run_probe(self.app, p, self.result, self.probe_btn)

    def save(self) -> None:
        p = self.form()
        if not p["name"]:
            self.result.configure(text="请填名称。Enter a name.", fg="#b3261e")
            return
        if p["connection"] == "windows" and not p["windowsPrinter"]:
            self.result.configure(text="请先选择 Windows 打印机。Choose the Windows printer first.", fg="#b3261e")
            return

        def done(saved: Dict[str, Any]) -> None:
            self.top.destroy()
            self.app.update_status()
            if self.app.tree.exists(saved["id"]):
                self.app.tree.selection_set(saved["id"])
            if messagebox.askyesno(APP_TITLE, "已保存。现在打印测试页吗？\nSaved. Print a test page now?", parent=self.app.root):
                self.app.test_print()

        self.app.bg(lambda: setup.save_printer(p), done, self.save_btn)


class ReportWindow:
    """The diagnostics report: everything the search found, to copy and send for remote help."""

    def __init__(self, app: App, parent: tk.Misc, text: Optional[str]) -> None:
        self.app = app
        top = self.top = tk.Toplevel(parent)
        top.title("诊断报告 Diagnostics")
        top.transient(parent)
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry(f"{min(820, sw - 60)}x{min(600, sh - 100)}")
        f = ttk.Frame(top, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, wraplength=760, text="把下面的内容复制后发给我们，方便远程排查（不含密钥）。\nCopy this and send it to us for remote help (it contains no keys).").pack(anchor="w")
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(8, 8))
        self.text = tk.Text(body, wrap="none", font=("Consolas" if IS_WINDOWS else "Menlo", 9))
        ys = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(body, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="we")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.copy_btn = ttk.Button(row, text="一键复制 Copy", command=self.copy)
        self.copy_btn.pack(side="left")
        self.note = ttk.Label(row, foreground="#555", text="")
        self.note.pack(side="left", padx=10)
        ttk.Button(row, text="关闭 Close", command=top.destroy).pack(side="right")
        if text is None:
            self.set_text("正在收集… Collecting… (5-20 s)")
            self.copy_btn.state(["disabled"])
            self.app.bg(lambda: setup.diagnostics(setup.find_printers()), self.loaded)
        else:
            self.set_text(text)

    def loaded(self, text: str) -> None:
        if self.top.winfo_exists():
            self.set_text(text)
            self.copy_btn.state(["!disabled"])

    def set_text(self, text: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")

    def copy(self) -> None:
        text = self.text.get("1.0", "end").rstrip()
        self.top.clipboard_clear()
        self.top.clipboard_append(text)
        self.top.update()
        from .paths import log_dir

        self.note.configure(text=f"已复制 Copied ({len(text)} chars) · 也保存在 also saved to {log_dir() / 'diagnostics.txt'}")


MODES = {"star": "Star 光栅 raster", "image": "图片 Picture", "text": "文字 Text"}
PROBE_ERRORS = {
    "timeout": "打印机离线或 IP 不对（连接超时）：打印机没开机、IP 变了，或不在同一网络。\nPrinter offline or wrong IP (timed out): off, its IP changed, or another network.",
    "refused": "端口被占用或不对（拒绝连接）：打印机可能正在给其他程序（POS）出单，或者端口不是 9100。\nPort busy or wrong (refused): the printer may be busy with another program (POS), or the port isn't 9100.",
    "unreachable": "网段不对：这台电脑和打印机不在同一网络。\nWrong network: this computer can't reach the printer.",
    "busy": "打印机断开了连接（可能正忙于其他程序）。\nThe printer dropped the connection (busy with another program?).",
    "lost": "连接中断，小票没有送完。\nThe connection dropped before the ticket was sent.",
    "cover_open": "打印机盖子开着。\nThe printer's cover is open.",
    "paper_out": "打印机没纸了。\nThe printer is out of paper.",
}
HINTS = {
    "in_use": "\n建议：改用“Windows 已安装的打印机”方式，和 POS 共用同一个驱动。\nTip: use the Windows printer connection to share the driver with the POS.",
    "driver": "\n建议：在 Windows“打印机和扫描仪”里打印一张测试页确认驱动正常；或改用网络 IP 直连。\nTip: print a Windows test page from Printers & scanners to check the driver, or use the network IP.",
    "not_found": "\n建议：点“重新侦测”选择现在的打印机。\nTip: press Detect again and choose the printer.",
}


def explain_printer_error(e: PrinterError) -> str:
    code = getattr(e, "code", "error")
    base = str(e) if code in ("in_use", "wrong_subnet", "not_found", "driver", "paused", "spooler", "offline") else PROBE_ERRORS.get(code, str(e))
    detail = f"\n({e})" if code in PROBE_ERRORS else ""
    return base + detail + HINTS.get(code, "")


def run_probe(app: "App", printer: Dict[str, Any], label: tk.Label, button: Optional[ttk.Button]) -> None:
    """Test connection: prints a short test ticket; green when it went out, red with the reason."""

    def work() -> Dict[str, Any]:
        try:
            return setup.test_connection(printer)
        except PrinterError as e:
            return {"error": explain_printer_error(e)}

    def show(res: Dict[str, Any]) -> None:
        if not label.winfo_exists():
            return
        if res.get("error"):
            label.configure(text=f"● {res['error']}", fg="#b3261e")
            return
        st = res.get("status") or {}
        extra = "  · 纸快用完 paper low" if st.get("paperLow") else ""
        via = {"tcp": "IP 直连 network IP", "raw": "Windows 打印机 Windows printer", "driver": "Windows 驱动图片 picture through the driver"}.get(res.get("method") or "", "")
        note = "\n（这台打印机的驱动不接受原始数据，已改用驱动打印图片 The driver refuses raw data: printing pictures through it）" if res.get("fallback") else ""
        label.configure(text=f"● 已打印测试小票 Test ticket printed ({via}, {res.get('ms')} ms){extra}\n没出纸？请检查打印机类型。Nothing came out? Check the printer type.{note}", fg="#1b7f3b")

    app.bg(work, show, button)


def run() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
