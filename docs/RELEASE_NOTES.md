XOrdering 后厨小票打印程序 · Kitchen ticket printer for XOrdering (Windows).

下载 `XOrderingPrinter.exe`，双击运行即可（无需安装其他软件）。安装步骤见 [README](https://github.com/yunruog/xordering-printer#readme)。
Download `XOrderingPrinter.exe` and double-click it; nothing else to install. Setup steps: see the README.

**0.4.0**
- 修复“线上订单打成一长串乱码”：原因是打印机类型被保存成了“Star 光栅”，而打印机其实是 ESC/POS，
  Star 光栅指令被当成文字打印（每行开头的 “bH” 就是 Star 光栅的行头）。现在每张小票发出前都会检查：
  指令开头是否符合打印机类型、图片宽度是否等于纸宽（80mm = 576 点）、长度不超过正常小票 3 倍、类型和 Windows
  驱动是否矛盾（Star 光栅发给 ESC/POS 驱动会被拦截）。不通过就不发送，窗口和后台都会显示原因。
  Fixed online orders printing as a long strip of garbage (an ESC/POS printer saved as Star raster). Every ticket is
  now checked before it is sent: the type's start command, picture width = paper width, at most 3x a normal ticket,
  and type vs. Windows driver. A ticket that fails is not sent; the window and the dashboard say why.
- 所有小票（订单、测试页、测试连接、后台测试页、重打）都走同一条管线：同一个绘制函数、同一个检查、同一个发送函数。
  One pipeline for every ticket: the same drawing, check and sending for orders, test pages and reprints.
- 诊断日志：每张小票记录来源、订单号、第几次尝试、类型、纸宽、连接方式、Windows API 和数据类型、字节数、
  前 64 / 后 32 字节（十六进制）。“复制日志”一键复制。A log line per ticket (bytes in hex …); "Copy log" copies it.
- 防重复、防浪费：同一订单只打一次（除非重打）；可能已经打出一部分的失败最多重试 3 次，每次间隔 10 秒以上；
  打印到一半被撤回的 Windows 任务也算一次重试（不会反复重打）；一台电脑只运行一个打印进程；
  小票底部不再留空白。Each order prints once; at most 3 tries when part of it may have printed; a Windows job taken
  back mid-print counts as a try; one print loop per computer; no blank paper under the ticket.
- “暂停打印”开关（窗口和后台都有）：暂停期间订单排队不打印，恢复时可选择补打或跳过。
  Pause printing (here and on the dashboard): tickets wait; on resume, print them or skip them.
- “重打最近一单”按钮：不用下单就能验证。Reprint last order, to check a printer without placing an order.
- 新加打印机默认类型改为“ESC/POS 图片”；改了类型却没测试就保存会提醒。New printers default to ESC/POS picture;
  saving a type you haven't tested asks first.
- 每 3 小时检查一次更新（以前每天一次）。Checks for updates every 3 hours.

**0.3.0**
- 自动查找打印机：打开打印机窗口就在后台查找（显示每一步进度）：Windows 已安装的打印机（含 USB、TCP/IP 端口的 IP）、
  其他程序（如 qMenu）正在连接的打印机、ARP 表、每张网卡的网段、网页标题 / SNMP 识别品牌型号、USB 设备。
  只找到一台小票机时自动选中并填好类型和纸宽，只需“测试连接”和“保存”。第一次运行自动打开。
  Finds printers by itself (Windows printers, printers other programs such as qMenu are using, ARP, every network card,
  make and model from the web page / SNMP, USB); one receipt printer is picked and filled in; opens by itself on first run.
- 新连接方式“Windows 已安装的打印机”：通过 Windows 打印队列发送（和 POS 用同一个驱动），支持三种类型和 80/58mm；
  TCP/IP 端口可一键改为直连 IP。New connection: the Windows printer (through the Windows queue, the POS's own driver).
- 和 POS 共用打印机：打印机被占用时提示“打印机正被 XXX 使用”，订单排队自动重试，打完立即断开；
  一直打不出来时窗口红色提醒，建议改用 Windows 打印机方式，并在后台重新查找。
  Shared printers: "in use by qMenu.exe", queued and retried; a red warning and a new search when it keeps failing.
- “测试连接”真的打印一张测试小票（店名、时间、连接方式、打印机），失败时写明原因；“诊断报告”一键复制。
  Test connection prints a short ticket and says why when it can't; Diagnostics copies a full report.
- 设置保存在本机（更新不会丢失），设备密钥有备份副本。Settings and the device token are kept across updates.

**0.2.2**
- 登录方式改为邮箱验证码（与网页后台、XInventory 相同），不再使用短信。推荐仍然是设备密钥：程序首页提示
  “推荐：在后台 Settings > Kitchen printers 生成设备密钥，粘贴到这里”。
  Email sign-in (the same 6-digit email code as the dashboard and XInventory) replaces the phone/SMS sign-in.
  The device key stays the recommended way, and the first screen says so.
- 邮箱登录：重新发送有 60 秒冷却，提示检查垃圾邮件文件夹。已连接的电脑不受影响，自动更新即可。
  Resend with a 60 s cooldown and a spam-folder hint. Connected computers keep working and update themselves.

**0.2.1**
- Star TSP100 / TSP143III futurePRNT（Star 光栅）：小票以 576 点位图、Star Graphic Mode 指令发送并切纸。
  Star raster printers: the ticket as a 576-dot picture in Star Graphic Mode, then a cut.
- 新的线上订单小票：取餐时间最大字号在最上面（预约单反白），英文，中文备注用自带字体。
  New online-order ticket: pickup time on top in the biggest type (scheduled orders inverted), English, bundled Chinese font.
- 扫描打印机、测试连接（显示超时 / 拒绝连接）、打印测试页、预览小票；设备密钥可直接粘贴整段文字。
  Scan for printers, test connection with the reason, test page, ticket preview; paste a device key from any text.
- 和店内 POS 共用打印机：端口忙时排队重试，不丢单、不重复。打印机状态（缺纸、开盖）显示在后台。
  Shared with the POS: busy port → queued and retried, never lost or doubled. Printer status (paper, cover) on the dashboard.

`XOrderingPrinter.exe.sha256` is the file's SHA-256; installed copies check it when they update themselves.
