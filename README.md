# XOrdering Printer · 后厨小票打印

顾客在 XOrdering 下单后，订单会自动从店里后厨的小票机打印出来。这个程序装在店里的 Windows 电脑上，
每几秒向 XOrdering 拉取新订单，发送到网线连接的小票机（端口 9100），打印完回报“已打印”。
支持 Star TSP100 / TSP143III futurePRNT 这类没有字库的光栅打印机（Star Graphic Mode），也支持普通
ESC/POS 打印机（GBK 文字或图片）。所有餐厅共用这一个程序。

When a customer orders on XOrdering, the ticket prints by itself on the kitchen's receipt printers.
This program runs on the restaurant's Windows computer: every few seconds it asks XOrdering for new
tickets, sends them to the network printers (port 9100) and reports them printed: Star TSP100 /
TSP143III futurePRNT raster printers (Star Graphic Mode, no fonts on board) and ESC/POS printers
(GBK text or a picture). One program for every restaurant.

**下载 Download:** [XOrderingPrinter.exe](https://github.com/yunruog/xordering-printer/releases/latest/download/XOrderingPrinter.exe)
(Windows 10/11, 不需要安装其他软件 nothing else to install)

---

## 到店安装步骤（约 10 分钟）

**准备：** 店里的 Windows 电脑、小票机都插好网线并开机，电脑和打印机连在同一个路由器上。
在后台 设置 → 后厨打印机 → “生成设备密钥”（手机上也行：复制，或扫码）。不需要短信验证码。

1. **（可选）查打印机的 IP 地址。** 程序里可以直接“扫描打印机”。也可以关掉打印机，按住 **FEED（走纸）**
   键再打开电源，打印机会打出自检页，上面写着 IP 地址（例如 `192.168.1.100`）。
2. **下载程序。** 在电脑上打开上面的下载链接，下载 `XOrderingPrinter.exe`。
3. **打开程序。** 双击下载好的文件。如果出现蓝色的“Windows 已保护你的电脑”，点 **更多信息** →
   **仍要运行**。如果防火墙询问，点 **允许**。
4. **连接这台电脑：** 把 `xpk_` 开头的设备密钥粘贴到“设备密钥”（有“粘贴”按钮，整段消息粘进来也行），
   点“连接”。（短信开通的话，也可以用“手机号登录”。）
5. **填打印机。** 点“添加”（后台已经添加的会自动显示，点“修改”核对）：填名称，点 **“扫描打印机”**，
   在找到的 IP 里点一下即可填入，并自动 **“测试连接”**：绿色“已连接”就好；红色会写原因（超时：没开机 /
   IP 不对；拒绝连接：端口不对或打印机正忙）。**打印机类型**：Star TSP100 / TSP143III 选 **“Star 光栅”**；
   其他 ESC/POS 打印机选“文字”，测试页中文乱码就改“图片”。保存。
   **建议在路由器上给打印机设固定 IP（DHCP 保留）**，以免 IP 变了打不出来。
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
| 状态显示“忙 / 拒绝连接” | 打印机正在给店内 POS 出单：订单会排队、自动重试，不用处理 |
| 某台打印机显示错误 / 后台显示“打印失败” | 检查打印机电源、网线、纸、盖子；IP 是否变了（重新打自检页核对，在程序里“修改”） |
| 打印机 IP 经常变 | 请在路由器里给打印机设置固定 IP（DHCP 保留） |
| 中文乱码 | 把这台打印机的“中文打印方式”改成“图片” |
| 想看详细记录 | 程序里点“打开日志” |

---

## Setup in the shop (English)

1. Download `XOrderingPrinter.exe` on the shop's Windows computer and double-click it
   (SmartScreen: **More info → Run anyway**; firewall prompt: **Allow**).
2. Connect: paste a device key from the dashboard (Settings → Kitchen printers → Create device key;
   copy it or scan its QR code). Signing in with the owner's phone works too when SMS is set up.
3. Add or check each printer: **Scan** lists the devices with port 9100 open (click one to use it),
   **Test connection** turns green or names the reason (timed out / refused). Type: **Star raster**
   for Star TSP100 / TSP143III, otherwise Text (GBK), or Picture when Chinese comes out garbled.
   Give the printer a fixed IP on the router (DHCP reservation).
4. **Test page** prints the sample order as an ASAP and as a scheduled ticket; **Preview** shows the
   same picture on screen.
5. Close the window. Printing continues in the background and starts with Windows; it updates
   itself daily from this repository's releases.

---

## How it works

```
XOrdering (Supabase)  <── HTTPS, every 4 s ──  XOrderingPrinter (shop PC)  ── TCP 9100 ──> printers
  print_device_poll      tickets for this computer          jobs.db (SQLite)             Star Graphic Mode raster
  print_device_report    printed / failed                    retries, dedupe              or ESC/POS text (GBK) / picture
  print_device_status    printer states (paper, cover)
  print_device_preview   the sample tickets as PNG
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
- **Shared with the POS:** a printer serves one connection at a time. While the restaurant's POS is
  printing, port 9100 refuses or drops us: the ticket waits in the queue and is retried with a
  back-off (2 s … 30 s, a little random), in order; once every byte was accepted it counts as
  printed, so it is never printed twice.
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
| `xordering_printer/ticket.py` | the ticket's lines, the sample order (test page, preview) |
| `xordering_printer/render.py` | the ticket as a 576-dot picture (bundled font) |
| `xordering_printer/star.py`, `escpos.py` | Star Graphic Mode raster; ESC/POS text (GBK) and picture |
| `xordering_printer/printer.py` | TCP 9100 with status checks, test connection, network scan |
| `xordering_printer/api.py`, `setup.py` | server calls; connecting the computer |
| `xordering_printer/service.py`, `install.py`, `updater.py` | background service, autostart, self-update |
| `xordering_printer/gui.py` | setup and status window (tkinter) |
| `tools/mock_printer.py` | a pretend printer for testing: ESC/POS, or `--star` a TSP100 that checks every byte against the Star Graphic Mode spec |
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
