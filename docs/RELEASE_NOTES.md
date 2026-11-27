XOrdering 后厨小票打印程序 · Kitchen ticket printer for XOrdering (Windows).

下载 `XOrderingPrinter.exe`，双击运行即可（无需安装其他软件）。安装步骤见 [README](https://github.com/yunruog/xordering-printer#readme)。
Download `XOrderingPrinter.exe` and double-click it; nothing else to install. Setup steps: see the README.

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
