"""XOrdering Printer: prints XOrdering orders on the kitchen's network receipt printers.

One program for every restaurant. It holds no server address, key or restaurant data in its code:
the server details come from the XOrdering site's printer-config.json, and the device token and
printers are stored on this computer only (see paths.py).
"""

VERSION = "0.4.1"
APP_NAME = "XOrderingPrinter"
APP_TITLE = "XOrdering 打印 Printer"
# Public, no sign-in: where updates come from.
GITHUB_REPO = "yunruog/xordering-printer"
# The XOrdering site; its printer-config.json names the server (URL and publishable key).
DEFAULT_SITE = "https://yunruog.github.io/xordering/"
