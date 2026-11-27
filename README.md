# XOrdering Printer · 后厨小票打印

顾客在 XOrdering 下单后，订单会自动从店里后厨的小票机打印出来。这个程序装在店里的 Windows 电脑上，
每几秒向 XOrdering 拉取新订单，发送到小票机（网络 IP 直连端口 9100，或者通过 Windows 已安装的打印机：USB、网络、蓝牙都行），
打印完回报“已打印”。打印机由程序自己查找，不需要懂网络。
支持 Star TSP100 / TSP143III futurePRNT 这类没有字库的光栅打印机（Star Graphic Mode），也支持普通
ESC/POS 打印机（GBK 文字或图片）。所有餐厅共用这一个程序。

When a customer orders on XOrdering, the ticket prints by itself on the kitchen's receipt printers.
This program runs on the restaurant's Windows computer: every few seconds it asks XOrdering for new
tickets, sends them to the printers (straight to IP:9100, or through a printer installed in Windows:
USB, network or Bluetooth; the program finds them by itself) and reports them printed: Star TSP100 /
TSP143III futurePRNT raster printers (Star Graphic Mode, no fonts on board) and ESC/POS printers
(GBK text or a picture). One program for every restaurant.

**下载 Download:** [XOrderingPrinter.exe](https://github.com/yunruog/xordering-printer/releases/latest/download/XOrderingPrinter.exe)
(Windows 10/11, 不需要安装其他软件 nothing else to install)

---

## 到店安装步骤（约 10 分钟）

**准备：** 店里的 Windows 电脑、小票机都插好网线并开机，电脑和打印机连在同一个路由器上。
**推荐：** 在后台 Settings > Kitchen printers（设置 → 后厨打印机）点 “生成设备密钥”（手机上也行：复制，或扫码），粘贴到程序里。

1. **下载程序。** 在电脑上打开上面的下载链接，下载 `XOrderingPrinter.exe`。
2. **打开程序。** 双击下载好的文件。如果出现蓝色的“Windows 已保护你的电脑”，点 **更多信息** →
   **仍要运行**。如果防火墙询问，点 **允许**。
3. **连接这台电脑：** 把 `xpk_` 开头的设备密钥粘贴到“设备密钥”（有“粘贴”按钮，整段消息粘进来也行），
   点“连接”。（也可以用“邮箱登录”：输入老板登录后台的邮箱，填入邮件里的 6 位验证码；没收到请看垃圾邮件文件夹。）
4. **打印机：自动查找。** 还没有打印机时，连接后会自动打开打印机窗口，并在后台 **“正在查找打印机…”**（约 10 秒，显示每一步）：
   Windows 已安装的打印机（USB / 网络 / 蓝牙）、其他程序（比如 qMenu）正在用的打印机、局域网里的打印机、USB 设备，
   并识别 Star / Epson 型号。**只找到一台小票机时会自动选好、填好类型和纸宽**，只需点 **“测试连接”**（会真的打出一张测试小票）
   再点 **“保存”**。找到多台时最像小票机的排在最前面，点选一台即可。
   - **连接方式**：“Windows 已安装的打印机”（推荐和 POS 共用打印机时用，走 POS 用的同一个驱动）或“网络 IP 直连”。
     Windows 打印机是 TCP/IP 端口时，可以点“改为直连 IP”。
   - **打印机类型**：Star TSP100 / TSP143III 选 **“Star 光栅”**（自动判断）；Epson 等 ESC/POS 打印机选“图片”（自动判断），
     中文正常也可以用“文字”。
   - 找不到？点 **“诊断报告”** → **“一键复制”**，把内容发给我们远程排查（不含密钥）。“重新侦测”可以再找一次。
   - 网络打印机建议在路由器上设固定 IP（DHCP 保留）。
5. 以后打开“添加 / 修改”也会自动查找；配置好之后程序启动不再重新查找，只有一直打印失败时才自动重新查找并提示。
6. **打印测试页。** 选中打印机，点“打印测试页”：会打出两张示例单（普通单、预约单），有长菜名和中文备注。
   “预览小票”可以在电脑上直接看图片。后台 设置 → 后厨打印机 也有“打印测试页”和“预览小票”。
7. **完成。** 关掉窗口即可，程序会在后台继续打印，电脑重启后也会自动运行。
   可以在顾客页面下一个测试订单，看两台打印机是否都出单，后台订单上会显示“已打印”。

**以后：** 不用管它。程序每天自动检查更新；断网、打印机没纸 / 开盖 / 没开机、或者正在给店里的 POS 出单时，
订单不会丢，恢复后会自动补打，同一张单不会重复打印。后台订单列表会显示每张单“待打印 / 已打印 / 打印失败”，
超过 2 分钟没打出来会标红并发邮件提醒；打印电脑离线 5 分钟且有待打印订单也会发邮件。后台能看到电脑是否
在线、打印机是否缺纸 / 开盖。电脑丢了或不用了，在后台“后厨打印机”里把这台电脑“注销”即可。

**常见问题**

| 现象 | 处理 |
| --- | --- |
| 程序显示“网络断开” | 检查电脑能否上网；恢复后订单会自动补打 |
| Star TSP100 打不出来 | 打印机类型要选“Star 光栅”（它不认 ESC/POS 和文字） |
| 状态显示“打印机正被 qMenu.exe 使用” | 打印机正在给店内 POS 出单：订单会排队、自动重试，不用处理；一直这样会红色提醒，建议改用“Windows 已安装的打印机” |
| 状态显示“忙 / 拒绝连接” | 打印机正在给店内 POS 出单：订单会排队、自动重试，不用处理 |
| 窗口红色提醒“已经 N 分钟打不出来” | 订单在排队不会丢；点“修复”，按提示改用 Windows 打印机或选重新找到的打印机 |
| 找不到打印机 | 点“诊断报告” → “一键复制”，发给我们 |
| 某台打印机显示错误 / 后台显示“打印失败” | 检查打印机电源、网线、纸、盖子；IP 是否变了（重新打自检页核对，在程序里“修改”） |
| 打印机 IP 经常变 | 请在路由器里给打印机设置固定 IP（DHCP 保留） |
| 中文乱码 | 把这台打印机的“中文打印方式”改成“图片” |
| 想看详细记录 | 程序里点“打开日志” |

---

## Setup in the shop (English)

1. Download `XOrderingPrinter.exe` on the shop's Windows computer and double-click it
   (SmartScreen: **More info → Run anyway**; firewall prompt: **Allow**).
2. Connect: paste a device key from the dashboard (Settings → Kitchen printers → Create device key;
   copy it or scan its QR code): the recommended way. Or **Email sign-in**: the owner's dashboard
   email and the 6-digit code it receives (check the spam folder if it doesn't arrive). No SMS.
3. Printers: with none set up yet, the printer window opens by itself and **looks for printers** in
   the background (about 10 s, each step shown): printers installed in Windows (USB, network,
   Bluetooth), printers other programs such as qMenu are connected to, the network of every network
   card, make and model (web page / SNMP), USB devices. **One receipt printer found: it is picked
   and filled in** (connection, type, paper): press **Test connection** (it prints a short ticket)
   and **Save**. Several: the most receipt-printer-like first; click one. Connection: **Windows
   printer** (through the Windows queue, the POS's own driver: best when sharing with the POS) or
   **Network IP** (straight to IP:9100; a Windows printer on a TCP/IP port switches with one click).
   Not found: **Diagnostics → Copy** and send it to us. Give network printers a fixed IP.
4. **Test page** prints the sample order as an ASAP and as a scheduled ticket; **Preview** shows the
   same picture on screen.
5. Close the window. Printing continues in the background and starts with Windows; it updates
   itself daily from this repository's releases.

---

## How it works

```
XOrdering (Supabase)  <── HTTPS, every 4 s ──  XOrderingPrinter (shop PC)  ── TCP 9100 or the Windows queue ──> printers
  print_device_poll      tickets for this computer          jobs.db (SQLite)             Star Graphic Mode raster
  print_device_report    printed / failed                    retries, dedupe              or ESC/POS text (GBK) / picture
  print_device_status    printer states (paper, cover)
  print_device_preview   the sample tickets as PNG
```

- **Server details** aren't in the code: the program reads the XOrdering site's
  `printer-config.json` (the server URL and the publishable key the website itself uses).
- **Device token:** connecting (device key, or the owner's email sign-in, used once and signed out)
  gives this computer a long-lived token. Only its hash is on the server; the dashboard can sign
  the computer out. Stored in `%APPDATA%\XOrderingPrinter\config.json` with the printers, outside
  the program, so updates and reinstalls keep it; `config.backup.json` keeps the last good copy
  in case the file is ever damaged.
- **No lost or double tickets:** each ticket (one order on one printer) is saved in
  `%APPDATA%\XOrderingPrinter\jobs.db` as soon as it arrives and printed once; a lost "printed"
  report is sent again without printing again. Printer offline: the ticket waits and retries
  (back-off up to 30 s; reported as failed on the dashboard). Internet down: tickets already
  received print, and orders placed meanwhile arrive with the next poll. A reprint from the
  dashboard is a new ticket. Orders rejected or cancelled before printing are not printed.
- **The ticket** (ticket.py): one per online order, in English. The pickup time comes first in the
  biggest type (`PICKUP: ASAP (~4:10 PM)`, placed + the restaurant's preparation time; or, white on
  black, `★ SCHEDULED 6:30 PM ★`), then the store name, order number and time, customer, dishes
  (quantity, wrapped name, unit price, amount on the right, options, notes), Subtotal / Tax / TOTAL
  and `NOT PAID · PAY AT PICKUP`. A scheduled order prints again 20 minutes before pickup (the server
  queues it) with `READY BY 6:30 PM · REPRINT` on top; a dashboard reprint says `REPRINT`.
- **Printer types:** *Star raster* (star.py) for Star TSP100 / TSP143III futurePRNT, which have no
  fonts and ignore ESC/POS: the ticket is drawn as a 576-dot picture (render.py) and sent in Star
  Graphic Mode (`ESC * r R`, `ESC * r A`, `ESC * r P 0`, `ESC * r F 13`, one `b n1 n2 …` per line,
  `ESC * r Y n` for blank lines, `ESC FF NUL` to feed and cut, `ESC * r B`). *Picture* sends the same
  picture as ESC/POS `GS v 0`; *Text* sends ESC/POS text with Chinese in GBK (`FS &`). The pictures use
  the font shipped with the program (Noto Sans SC SemiBold, `xordering_printer/fonts/`, SIL OFL), so
  Chinese customer notes never print as boxes, on any computer.
- **Printer checks:** before a ticket the printer is asked for its status (ESC/POS `DLE EOT`; Star
  `ESC ACK SOH`, its automatic status): cover open, paper out and offline fail the ticket instead of
  losing it; paper running low is shown on the dashboard. Printers that don't answer are printed to
  anyway. A status query after the ticket confirms the printer read it.
- **Finding printers** (discover.py, run when the printer window opens; never blocks the window):
  (a) Windows printers: name, driver, port; TCP/IP ports resolved to IP:port from the port
  monitor's registry settings; PDF / XPS / OneNote / Fax left out. (b) Other programs' TCP
  connections to printer ports (9100-9102, 515, 631, 8008, 8043) with the program's name (psutil,
  or `netstat -ano` + `tasklist`); the worker samples them every 15 s into
  `seen-connections.json`, since a POS connects only while it prints. (c) `arp -a` on the same
  networks, MAC maker (Star Micronics, Seiko Epson) as a hint. (d) A scan of each real network
  card's network (/24 at most; virtual cards, loopback and 169.254 skipped) on 9100-9102 and 515.
  (e) HTTP page title and SNMP sysDescr / sysName for make and model. (f) USB devices (Star
  VID_0519, Epson VID_04B8). Merged per printer, typed (Star / TSP → Star raster; Epson, TM-, POS,
  Thermal … → ESC/POS picture; 58 mm from the name) and ranked. One receipt printer: filled in by
  itself. **Diagnostics** turns all of it into text to copy.
- **Connections** (deliver.py): *Network IP* straight to IP:port, or *Windows printer*
  (winprint.py, pywin32): the same printer bytes as a RAW job through the Windows queue, or, when
  a driver refuses RAW jobs, the ticket picture drawn through the driver (remembered per printer).
  The job is watched in the Windows queue; one that is stuck (offline, paper, paused, busy) is
  taken out again and the ticket retried by us, so it is never lost and never printed later by
  Windows on its own. The server keeps each printer's name, address, type and paper (the dashboard
  shows them); how *this* computer reaches it lives in `config.json` → `printerLocal` (a Windows
  printer without an IP shows the computer's name as its address on the dashboard).
- **Shared with the POS:** a network printer serves one connection at a time. While the
  restaurant's POS holds it, port 9100 refuses or drops us: the error names the program ("打印机正被
  qMenu.exe 使用"), the ticket waits in the queue and is retried with a back-off (2 s … 30 s, a
  little random), in order, and our connection is closed right after each ticket. Once every byte
  was accepted it counts as printed, so it is never printed twice. A printer failing for 3 minutes
  is flagged: the window shows a red warning suggesting the Windows printer connection, and the
  printers are looked for again in the background (`discovery.json`, at most every 30 min).
- **Always running:** first run copies the program to `%LOCALAPPDATA%\XOrderingPrinter\bin\`, adds
  a shortcut to the Startup folder (`--service`) and a scheduled task that starts it again every 5
  minutes if it stopped. The service runs the print loop as a separate process and restarts it when
  it exits.
- **Updates:** every 3 hours the program checks
  `https://api.github.com/repos/yunruog/xordering-printer/releases/latest`; a newer
  `XOrderingPrinter.exe` is downloaded, checked against `XOrderingPrinter.exe.sha256`, swapped in
  and started; if it doesn't come up, the previous version is put back.
- **One pipeline, checked:** every ticket (orders, the dashboard's test page, the window's test page
  and connection test, Reprint last order) goes through `pipeline.print_tickets()`: drawn at the
  printer's paper width, made black and white, turned into the type's commands, then **checked**
  before anything is sent: the type's start command (ESC @ / ESC * r R), every picture row exactly
  the paper's width (576 dots for 80 mm, 384 for 58 mm), at most 3x the rows the content needs, and
  no Star raster to a printer whose Windows driver is an ESC/POS one (that printed metres of text
  garbage at Tsunami Sushi on 2026-11-27). A ticket that fails is not sent: the window shows it in
  red, and the dashboard lists it under Not printed.
- **Once:** a job id prints once; a second "new" job for an order already printed on that printer is
  skipped (reported `skipped`); a failure after which part of the ticket may have printed (bytes cut
  short, connection lost) is tried at most 3 times, 10 s+ apart, then given up (reprint by hand); a
  printer that is off or busy doesn't use up tries. A Windows job taken back after it started
  printing counts as a try too. One print loop per computer (a local port
  lock, 47292).
- **Pause printing:** the window's switch and the dashboard's are one (`print_device_set_paused` /
  `set_printing_paused`): tickets wait; resuming prints them or skips them. **Reprint last order**
  prints the newest order again (REPRINT) on the selected printer, without the server.
- **Log:** `%APPDATA%\XOrderingPrinter\logs\printer.log` (rotating, ~5 MB) and
  `print-jobs.jsonl`: one `PRINTJOB` line per attempt with source, order, job, attempt, type, paper,
  connection, API and datatype (`winspool WritePrinter` / RAW, `GDI StartDoc` / EMF, TCP socket),
  byte count, first 64 and last 32 bytes in hex, result. **Copy log** in the window copies the
  printers, the last print jobs and the log's tail. Tokens and keys are never written to it.

| File | |
| --- | --- |
| `xordering_printer/worker.py` | poll → print → report loop, pause |
| `xordering_printer/pipeline.py` | the one way tickets print: build, check, send, log; Copy log |
| `xordering_printer/jobs.py` | local ticket queue (SQLite) |
| `xordering_printer/ticket.py` | the ticket's lines, the sample order (test page, preview) |
| `xordering_printer/render.py` | the ticket as a 576-dot picture (bundled font) |
| `xordering_printer/star.py`, `escpos.py` | Star Graphic Mode raster; ESC/POS text (GBK) and picture |
| `xordering_printer/printer.py` | TCP 9100 with status checks, test connection |
| `xordering_printer/discover.py` | finding printers (Windows, connections, ARP, scan, HTTP/SNMP, USB), ranking, the diagnostics report |
| `xordering_printer/winprint.py`, `deliver.py` | Windows printers (list, RAW / driver jobs); printing over either connection |
| `xordering_printer/api.py`, `setup.py` | server calls; connecting the computer |
| `xordering_printer/service.py`, `install.py`, `updater.py` | background service, autostart, self-update |
| `xordering_printer/gui.py` | setup and status window (tkinter) |
| `tools/mock_printer.py` | a pretend printer for testing: ESC/POS, or `--star` a TSP100 that checks every byte against the Star Graphic Mode spec; `--exclusive` takes one connection at a time like a Star LAN printer |
| `e2e/gui_screens.py` | screenshots of the printer windows (Windows CI artifact `screens`) |
| `e2e/run_e2e.py` | the whole flow against XOrdering's local dev server |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -t .           # unit tests
# Windows printer tests (tests/test_winprint.py) need a printer installed for them: CI adds
# "Star TSP143 (XOP test)" (Generic / Text Only on a TCP/IP port to 127.0.0.1:9187), see test.yml.
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
up within 3 hours (0.3.0 and older: within a day).

MIT License.
