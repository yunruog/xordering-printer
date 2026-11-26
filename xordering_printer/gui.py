"""The setup and status window (tkinter, built into Python: nothing to install).

First run: connect this computer (a device key, recommended, or the owner's email sign-in), check the printers'
IP addresses and print a test page. Afterwards it shows whether printing is working. Closing the
window doesn't stop printing: the background service keeps going.
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

from . import APP_TITLE, VERSION, api, config, setup
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
        self.render()
        root.after(200, self._drain)
        root.after(1500, self._tick)

    # --- background calls ----------------------------------------------------------------

    def bg(self, fn: Callable[[], Any], ok: Callable[[Any], None], button: Optional[ttk.Button] = None) -> None:
        if button is not None:
            button.state(["disabled"])

        def run() -> None:
            try:
                value = fn()
                self.results.put(lambda: ok(value))
            except Exception as e:  # shown to the user, logged by the caller's module
                err = e
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
        self.bg(setup.refresh, lambda _: self.render())

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

        box = ttk.LabelFrame(self.body, text=" 打印机 Printers ", padding=10)
        box.pack(fill="both", expand=True, pady=(14, 8))
        cols = ("name", "host", "paper", "mode", "state")
        self.status_cols = cols
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=6, selectmode="browse")
        for c, title, w in zip(cols, ("名称 Name", "IP 地址 Address", "纸宽 Paper", "类型 Type", "状态 Status"), (110, 130, 60, 110, 150)):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self.edit_printer())
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="添加 Add", command=lambda: self.edit_printer(new=True)).pack(side="left")
        ttk.Button(buttons, text="修改 Edit", command=self.edit_printer).pack(side="left", padx=6)
        self.conn_btn = ttk.Button(buttons, text="测试连接 Test connection", command=self.test_connection)
        self.conn_btn.pack(side="left")
        self.test_btn = ttk.Button(buttons, text="打印测试页 Test page", command=self.test_print)
        self.test_btn.pack(side="left", padx=6)
        self.preview_btn = ttk.Button(buttons, text="预览小票 Preview", command=self.preview)
        self.preview_btn.pack(side="left")
        self.conn_label = tk.Label(box, text="", anchor="w", justify="left", wraplength=520)
        self.conn_label.pack(anchor="w", pady=(6, 0))
        ttk.Label(box, wraplength=520, foreground="#555", text="每台开启的打印机都会打印完整订单。IP 地址在打印机自检页上（多数机型：按住 FEED 键开机）。建议在路由器上给打印机设固定 IP（DHCP 保留），以免 IP 变了打不出来。\nEach enabled printer prints the whole order. The IP address is on the printer's self-test page (hold FEED while switching on). Give the printer a fixed IP on the router (DHCP reservation) so it doesn't change.").pack(anchor="w", pady=(8, 0))

        foot = ttk.Frame(self.body)
        foot.pack(fill="x", pady=(6, 0))
        ttk.Button(foot, text="打开日志 Logs", command=self.open_logs).pack(side="left")
        ttk.Button(foot, text="断开此电脑 Disconnect", command=self.disconnect).pack(side="left", padx=6)
        ttk.Label(self.body, wraplength=540, foreground="#777", text=f"关闭此窗口后打印会在后台继续，开机自动运行。Printing continues in the background after you close this window, and starts with Windows.   v{VERSION}").pack(anchor="w", pady=(10, 0))
        self.update_status()

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
        for p in cfg.get("printers") or []:
            ps = printer_state.get(p["id"]) or {}
            s = "已关闭 Off" if not p.get("enabled", True) else "● 正常 OK" if ps.get("ok") else f"● {ps.get('error')}" if ps.get("error") else "—"
            if ps.get("ok") and ps.get("paperLow"):
                s = "● 纸快用完 Paper low"
            self.tree.insert("", "end", iid=p["id"], values=(p.get("name"), f"{p.get('host')}:{p.get('port', 9100)}", f"{80 if p.get('render') == 'star' else p.get('paperWidth', 80)} mm", MODES.get(p.get("render") or "text", ""), s))
        for iid in selected:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
        if not self.tree.selection() and self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])

    def selected_printer(self) -> Optional[Dict[str, Any]]:
        sel = self.tree.selection()
        return next((p for p in config.load().get("printers") or [] if sel and p["id"] == sel[0]), None)

    def edit_printer(self, new: bool = False) -> None:
        cfg = config.load()
        printer = None if new else self.selected_printer()
        if printer is None:
            n = len(cfg.get("printers") or []) + 1
            printer = {"name": f"后厨 Kitchen {n}", "host": "", "port": 9100, "paperWidth": 80, "render": "star", "enabled": True}
        PrinterDialog(self, printer)

    def test_print(self) -> None:
        p = self.selected_printer()
        if not p:
            messagebox.showinfo(APP_TITLE, "请先选择或添加打印机。Select or add a printer first.", parent=self.root)
            return
        self.bg(lambda: setup.test_print(p), lambda _: messagebox.showinfo(APP_TITLE, f"已发送测试页到 {p['name']}（两张：普通单和预约单）。\nTest page sent to {p['name']} (two tickets: ASAP and scheduled).\n\n打不出来？Star TSP100 系列请选“Star 光栅”；中文乱码请选“图片”。\nNothing printed? Star TSP100: choose Star raster. Garbled Chinese: choose Picture.", parent=self.root), self.test_btn)

    def test_connection(self) -> None:
        p = self.selected_printer()
        if not p:
            messagebox.showinfo(APP_TITLE, "请先选择或添加打印机。Select or add a printer first.", parent=self.root)
            return
        self.conn_label.configure(text="正在连接 Connecting…", fg="#555")
        run_probe(self, p.get("host", ""), p.get("port", 9100), p.get("render", "text"), self.conn_label, self.conn_btn)

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
    def __init__(self, app: App, printer: Dict[str, Any]) -> None:
        self.app = app
        self.printer = printer
        top = self.top = tk.Toplevel(app.root)
        top.title("打印机 Printer")
        top.transient(app.root)
        top.grab_set()
        f = ttk.Frame(top, padding=16)
        f.pack(fill="both", expand=True)
        self.name = self._entry(f, "名称 Name", printer.get("name", ""), 0)
        self.host = self._entry(f, "IP 地址 IP address (192.168.x.x)", printer.get("host", ""), 2)
        self.port = self._entry(f, "端口 Port", str(printer.get("port", 9100)), 4)
        tools = ttk.Frame(f)
        tools.grid(row=3, column=1, sticky="nw", padx=(8, 0))
        self.scan_btn = ttk.Button(tools, text="扫描打印机 Scan", command=self.scan)
        self.scan_btn.pack(side="left")
        self.probe_btn = ttk.Button(tools, text="测试连接 Test connection", command=self.probe)
        self.probe_btn.pack(side="left", padx=(6, 0))
        self.found = tk.Listbox(f, height=4)
        self.found.grid(row=4, column=1, rowspan=7, sticky="nwe", padx=(8, 0), pady=(6, 0))
        self.found.bind("<<ListboxSelect>>", self.pick_found)
        self.result = tk.Label(f, text="建议在路由器上给打印机设固定 IP。\nTip: give the printer a fixed IP on the router.", anchor="w", justify="left", fg="#555", wraplength=260)
        self.result.grid(row=11, column=1, sticky="nw", padx=(8, 0), pady=(6, 0))
        self.render = tk.StringVar(value=printer.get("render", "star"))
        ttk.Label(f, text="打印机类型 Printer type").grid(row=6, column=0, sticky="w", pady=(8, 0))
        rr = ttk.Frame(f)
        rr.grid(row=7, column=0, sticky="w")
        for value, label in (("star", "Star 光栅 (TSP100III / TSP143)"), ("image", "ESC/POS 图片 Picture"), ("text", "ESC/POS 文字 Text (GBK)")):
            ttk.Radiobutton(rr, text=label, value=value, variable=self.render, command=self.sync_paper).pack(anchor="w")
        self.paper = tk.StringVar(value=str(printer.get("paperWidth", 80)))
        ttk.Label(f, text="纸宽 Paper width").grid(row=8, column=0, sticky="w", pady=(8, 0))
        pr = ttk.Frame(f)
        pr.grid(row=9, column=0, sticky="w")
        self.paper_buttons = [ttk.Radiobutton(pr, text=f"{v} mm", value=v, variable=self.paper) for v in ("80", "58")]
        for btn in self.paper_buttons:
            btn.pack(side="left", padx=(0, 10))
        self.sync_paper()
        self.enabled = tk.BooleanVar(value=bool(printer.get("enabled", True)))
        ttk.Checkbutton(f, text="打印新订单 Print new orders", variable=self.enabled).grid(row=10, column=0, sticky="w", pady=(10, 0))
        b = ttk.Frame(f)
        b.grid(row=12, column=0, columnspan=2, sticky="we", pady=(16, 0))
        self.save_btn = ttk.Button(b, text="保存 Save", command=self.save)
        self.save_btn.pack(side="left")
        ttk.Button(b, text="取消 Cancel", command=top.destroy).pack(side="left", padx=6)

    def sync_paper(self) -> None:
        star = self.render.get() == "star"
        if star:
            self.paper.set("80")
        for btn in self.paper_buttons:
            btn.state(["disabled"] if star else ["!disabled"])

    def scan(self) -> None:
        self.found.delete(0, "end")
        self.found.insert("end", "正在扫描 Scanning…")
        self.result.configure(text="正在扫描局域网（约 10 秒）Scanning the network (about 10 s)…", fg="#555")

        def done(ips: List[str]) -> None:
            self.found.delete(0, "end")
            for ip in ips:
                self.found.insert("end", ip)
            if ips:
                self.result.configure(text=f"找到 {len(ips)} 台，点选一个填入 Found {len(ips)}: click one to use it", fg="#1b7f3b")
            else:
                self.result.configure(text="没找到开放 9100 端口的设备。打印机开机并插好网线了吗？\nNothing with port 9100 open. Is the printer on and cabled?", fg="#b3261e")

        try:
            port = int(self.port.get().strip() or "9100")
        except ValueError:
            port = 9100
        self.app.bg(lambda: setup.find_printers(port), done, self.scan_btn)

    def pick_found(self, _e: Any = None) -> None:
        sel = self.found.curselection()
        if not sel:
            return
        ip = self.found.get(sel[0])
        if not ip[:1].isdigit():
            return
        self.host.delete(0, "end")
        self.host.insert(0, ip)
        self.probe()

    def probe(self) -> None:
        self.result.configure(text="正在连接 Connecting…", fg="#555")
        run_probe(self.app, self.host.get(), self.port.get(), self.render.get(), self.result, self.probe_btn)

    def _entry(self, f: ttk.Frame, label: str, value: str, row: int) -> ttk.Entry:
        ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=(8 if row else 0, 0))
        e = ttk.Entry(f, width=36)
        e.insert(0, value)
        e.grid(row=row + 1, column=0, sticky="we")
        return e

    def save(self) -> None:
        try:
            port = int(self.port.get().strip() or "9100")
        except ValueError:
            port = -1
        p = dict(self.printer, name=self.name.get().strip(), host=self.host.get().strip(), port=port, paperWidth=80 if self.render.get() == "star" else int(self.paper.get()), render=self.render.get(), enabled=self.enabled.get(), assignment=self.printer.get("assignment") or "full")
        p = {k: v for k, v in p.items() if k in ("id", "name", "host", "port", "paperWidth", "render", "enabled", "assignment")}

        def done(saved: Dict[str, Any]) -> None:
            self.top.destroy()
            self.app.update_status()
            if self.app.tree.exists(saved["id"]):
                self.app.tree.selection_set(saved["id"])
            if messagebox.askyesno(APP_TITLE, "已保存。现在打印测试页吗？\nSaved. Print a test page now?", parent=self.app.root):
                self.app.test_print()

        self.app.bg(lambda: setup.save_printer(p), done, self.save_btn)


MODES = {"star": "Star 光栅 raster", "image": "图片 Picture", "text": "文字 Text"}
PROBE_ERRORS = {
    "timeout": "连接超时：打印机没开、IP 不对或不在同一网络。\nTimed out: printer off, wrong IP, or another network.",
    "refused": "拒绝连接：端口不对，或打印机正在给别的设备（POS）出单，稍后再试。\nConnection refused: wrong port, or the printer is busy with another device (POS); try again.",
    "unreachable": "无法到达：这台电脑和打印机不在同一网络。\nUnreachable: this computer and the printer aren't on the same network.",
}


def run_probe(app: "App", host: str, port: Any, render_kind: str, label: tk.Label, button: Optional[ttk.Button]) -> None:
    """Test connection: green when the printer answers, red with the reason when it doesn't."""
    host = (host or "").strip()
    if not host:
        label.configure(text="请先填 IP 地址。Enter the IP address first.", fg="#b3261e")
        return
    try:
        port_n = int(str(port).strip() or "9100")
    except ValueError:
        port_n = 9100

    def ok(res: Dict[str, Any]) -> None:
        st = res.get("status") or {}
        extra = ""
        if st.get("coverOpen"):
            extra = "  ⚠ 盖子开着 cover open"
        elif st.get("paperEmpty"):
            extra = "  ⚠ 没纸了 out of paper"
        elif st.get("paperLow"):
            extra = "  · 纸快用完 paper low"
        label.configure(text=f"● 已连接 Connected: {host}:{port_n} ({res.get('ms')} ms){extra}", fg="#b3261e" if ("⚠" in extra) else "#1b7f3b")

    def work() -> Dict[str, Any]:
        try:
            return setup.test_connection(host, port_n, render_kind)
        except PrinterError as e:
            return {"error": PROBE_ERRORS.get(e.code, str(e))}

    def show(res: Dict[str, Any]) -> None:
        if res.get("error"):
            label.configure(text=f"● {res['error']}", fg="#b3261e")
        else:
            ok(res)

    app.bg(work, show, button)


def run() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
