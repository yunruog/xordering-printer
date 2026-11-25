# XOrdering Printer · 后厨小票打印

顾客在 XOrdering 下单后，订单会自动从店里后厨的小票机打印出来。这个程序装在店里的 Windows 电脑上，
每几秒向 XOrdering 拉取新订单，发送到网线连接的小票机（ESC/POS，端口 9100），打印完回报“已打印”。
所有餐厅共用这一个程序。

When a customer orders on XOrdering, the ticket prints by itself on the kitchen's receipt printers.
This program runs on the restaurant's Windows computer: every few seconds it asks XOrdering for new
tickets, sends them to the network printers (ESC/POS, port 9100) and reports them printed. One
program for every restaurant.

**下载 Download:** [XOrderingPrinter.exe](https://github.com/yunruog/xordering-printer/releases/latest/download/XOrderingPrinter.exe)
(Windows 10/11, 不需要安装其他软件 nothing else to install)

---

## 到店安装步骤（约 10 分钟）

**准备：** 店里的 Windows 电脑、两台小票机都插好网线并开机，电脑和打印机连在同一个路由器上。
需要餐厅老板的手机（接收验证码），或者提前在后台生成的“设备密钥”。

1. **查打印机的 IP 地址。** 关掉打印机，按住 **FEED（走纸）** 键再打开电源，一两秒后松开，打印机会打出
   一张自检页，上面写着 IP 地址（例如 `192.168.1.100`）。两台打印机都这样做，把两个 IP 记下来。
2. **下载程序。** 在电脑上打开上面的下载链接，下载 `XOrderingPrinter.exe`。
3. **打开程序。** 双击下载好的文件。如果出现蓝色的“Windows 已保护你的电脑”，点 **更多信息** →
   **仍要运行**。如果防火墙询问，点 **允许**。
4. **连接这台电脑**（二选一）：
   - **手机号登录：** 选“手机号登录”，填老板的手机号，点“发送验证码”，输入短信里的 6 位数字，点“登录”。
   - **设备密钥：** 在手机或电脑上打开 XOrdering 后台 → 设置 → 后厨打印机 → “生成设备密钥”，
     把 `xpk_` 开头的那串字复制到程序的“设备密钥”里，点“连接”。
5. **填打印机。** 如果后台还没设置打印机，点“添加”，填名称（如“后厨 1”）和第 1 步查到的 IP，保存；
   第二台同样添加。已经在后台填好的，这里会自动显示，核对一下 IP 是否正确。
6. **测试打印。** 选中一台打印机，点“测试打印”。两台都试一下。
   - 测试页上的中文是**乱码**：点“修改”，把“中文打印方式”改成“图片”，保存后再测试。
   - 用的是 58 毫米的窄纸：把“纸宽”改成 58 mm。
7. **完成。** 关掉窗口即可，程序会在后台继续打印，电脑重启后也会自动运行。
   可以在顾客页面下一个测试订单，看两台打印机是否都出单，后台订单上会显示“已打印”。

**以后：** 不用管它。程序每天自动检查更新；断网或打印机没纸、没开机时订单不会丢，恢复后会自动补打，
同一张单不会重复打印。后台订单列表会显示每张单“待打印 / 已打印 / 打印失败”，需要时点“重新打印”。
电脑丢了或不用了，在后台“后厨打印机”里把这台电脑“注销”即可。

**常见问题**

| 现象 | 处理 |
| --- | --- |
| 程序显示“网络断开” | 检查电脑能否上网；恢复后订单会自动补打 |
| 某台打印机显示错误 / 后台显示“打印失败” | 检查打印机电源、网线、纸、盖子；IP 是否变了（重新打自检页核对，在程序里“修改”） |
| 打印机 IP 经常变 | 请在路由器里给打印机设置固定 IP（DHCP 保留） |
| 中文乱码 | 把这台打印机的“中文打印方式”改成“图片” |
| 想看详细记录 | 程序里点“打开日志” |

---

## Setup in the shop (English)

1. Print each printer's self-test page (switch it off, hold **FEED**, switch it on) and note its IP address.
2. Download `XOrderingPrinter.exe` on the shop's Windows computer and double-click it
   (SmartScreen: **More info → Run anyway**; firewall prompt: **Allow**).
3. Connect: sign in with the owner's phone number (SMS code), or paste a device key from the
   dashboard (Settings → Kitchen printers → Create device key).
4. Add or check both printers' IP addresses, then **Test print** each one. Garbled Chinese: set
   *Chinese text* to *Picture*. 58 mm paper: set the paper width.
5. Close the window. Printing continues in the background and starts with Windows; it updates
   itself daily from this repository's releases.

---

## How it works

```
XOrdering (Supabase)  <── HTTPS, every 4 s ──  XOrderingPrinter (shop PC)  ── TCP 9100 ──> printers
  print_device_poll      tickets for this computer          jobs.db (SQLite)             ESC/POS
  print_device_report    printed / failed                    retries, dedupe              text (GBK) or picture
```

- **Server details** aren't in the code: the program reads the XOrdering site's
  `printer-config.json` (the server URL and the publishable key the website itself uses).
- **Device token:** connecting (device key, or the owner's SMS sign-in, used once and signed out)
  gives this computer a long-lived token. Only its hash is on the server; the dashboard can sign
  the computer out. Stored in `%APPDATA%\XOrderingPrinter\config.json` with the printers, outside
  the program, so updates and reinstalls keep it.
- **No lost or double tickets:** each ticket (one order on one printer) is saved in
  `%APPDATA%\XOrderingPrinter\jobs.db` as soon as it arrives and printed once; a lost "printed"
  report is sent again without printing again. Printer offline: the ticket waits and retries
  (back-off up to 30 s; reported as failed on the dashboard). Internet down: tickets already
  received print, and orders placed meanwhile arrive with the next poll. A reprint from the
  dashboard is a new ticket. Orders rejected or cancelled before printing are not printed.
- **Printer checks:** before a ticket the printer is asked for its status (DLE EOT): cover open,
  paper out and offline fail the ticket instead of losing it. Printers that don't answer are
  printed to anyway. Every ticket ends with a feed and a cut.
- **Chinese:** text mode sends GBK (`FS &`, most printers for Chinese-speaking markets); image mode
  draws the ticket with a Chinese font of the computer (Microsoft YaHei / SimHei / SimSun) and sends
  it as a raster picture (`GS v 0`) for printers without a Chinese font.
- **Always running:** first run copies the program to `%LOCALAPPDATA%\XOrderingPrinter\bin\`, adds
  a shortcut to the Startup folder (`--service`) and a scheduled task that starts it again every 5
  minutes if it stopped. The service runs the print loop as a separate process and restarts it when
  it exits.
- **Updates:** once a day the program checks
  `https://api.github.com/repos/yunruog/xordering-printer/releases/latest`; a newer
  `XOrderingPrinter.exe` is downloaded, checked against `XOrderingPrinter.exe.sha256`, swapped in
  and started; if it doesn't come up, the previous version is put back.
- **Log:** `%APPDATA%\XOrderingPrinter\logs\printer.log` (rotating, ~5 MB). Tokens and keys are
  never written to it.

| File | |
| --- | --- |
| `xordering_printer/worker.py` | poll → print → report loop |
| `xordering_printer/jobs.py` | local ticket queue (SQLite) |
| `xordering_printer/ticket.py`, `escpos.py` | ticket layout; ESC/POS text (GBK) and picture |
| `xordering_printer/printer.py` | TCP 9100 with status checks |
| `xordering_printer/api.py`, `setup.py` | server calls; connecting the computer |
| `xordering_printer/service.py`, `install.py`, `updater.py` | background service, autostart, self-update |
| `xordering_printer/gui.py` | setup and status window (tkinter) |
| `tools/mock_printer.py` | a pretend printer for testing |
| `e2e/run_e2e.py` | the whole flow against XOrdering's local dev server |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -t .           # unit tests
.venv/bin/python tools/mock_printer.py --port 9100             # a pretend printer
# End to end (needs the XOrdering repository next to this one, with `npm install` done):
.venv/bin/python e2e/run_e2e.py --xordering ../xordering
.venv/bin/python -m xordering_printer --site http://127.0.0.1:8787/   # the window, against a dev server
```

`XORDERING_PRINTER_HOME` moves the data folder (tests use it); `XORDERING_PRINTER_PORT` the
service's local control port (default 47291).

**Release:** bump `VERSION` in `xordering_printer/__init__.py`, commit, tag `vX.Y.Z` and push the
tag. GitHub Actions (`.github/workflows/release.yml`) tests, builds the exe on Windows with
PyInstaller, smoke-tests it and publishes it with its SHA-256 to Releases. Installed copies pick it
up within a day.

MIT License.
