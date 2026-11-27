import sys
import unittest

from tests.helpers import temp_home


@unittest.skipUnless(sys.platform == "win32", "the window is checked on Windows (CI), where it runs")
class GuiTest(unittest.TestCase):
    def test_both_screens_render(self):
        temp_home()
        import tkinter as tk

        from xordering_printer import config, gui, install

        install.ensure_running = lambda: None  # don't start a service from a test
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root)
            root.update()
            self.assertEqual(app.view, "connect")
            texts = []
            stack = [root]
            while stack:
                w = stack.pop()
                stack.extend(w.winfo_children())
                try:
                    texts.append(str(w.cget("text")))
                except tk.TclError:
                    pass
            self.assertTrue(any("推荐：在后台 Settings > Kitchen printers" in t for t in texts))
            self.assertTrue(any("邮箱 Email" == t for t in texts))
            config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "a" * 64,
                          restaurant={"name": {"zh": "金莲小馆", "en": "Golden Lotus"}},
                          printers=[{"id": "p1", "name": "后厨 1", "host": "192.168.1.201", "port": 9100, "paperWidth": 80, "render": "text", "enabled": True}])
            app.render()
            root.update()
            self.assertEqual(app.view, "status")
            self.assertEqual(len(app.tree.get_children()), 1)
            d = gui.PrinterDialog(app, config.load()["printers"][0], autodetect=False)
            root.update()
            self.assertFalse(any(b.instate(["disabled"]) for b in d.paper_buttons))
            d.render.set("star")
            d.sync_paper()
            self.assertEqual(d.paper.get(), "80")
            self.assertTrue(all(b.instate(["disabled"]) for b in d.paper_buttons))
            d.top.destroy()
            self.assertTrue(hasattr(app, "conn_btn") and hasattr(app, "preview_btn"))
        finally:
            root.destroy()

    def test_found_printers_fill_the_form(self):
        temp_home()
        import tkinter as tk

        from tests.test_discover import tsunami
        from xordering_printer import config, discover, gui, install

        install.ensure_running = lambda: None
        config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "a" * 64, restaurant={"name": {"en": "Tsunami Sushi"}}, printers=[])
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root)
            root.update()
            new = {"name": "后厨 Kitchen 1", "host": "", "port": 9100, "paperWidth": 80, "render": "image", "enabled": True, "connection": "windows"}
            # Only one real receipt printer: picked and filled in by itself.
            res = tsunami()
            one = dict(res, connections=[], candidates=discover.merge(dict(res, connections=[], seen=[], windowsPrinters=res["windowsPrinters"][:1], usb=[], scan={}, arp=[], identify={})))
            d = gui.PrinterDialog(app, dict(new), autodetect=False)
            d.show_found(one, auto=True)
            root.update()
            f = d.form()
            self.assertEqual((f["connection"], f["windowsPrinter"], f["render"], f["paperWidth"]), ("windows", "Star TSP143IIIU", "star", 80))
            self.assertIn("自动选好", d.result.cget("text"))
            d.top.destroy()
            # Several: listed best first, nothing filled in until one is clicked.
            d = gui.PrinterDialog(app, dict(new), autodetect=False)
            full = tsunami()
            d.show_found(full, auto=True)
            root.update()
            self.assertEqual(d.form()["windowsPrinter"], "")
            first = d.found.item(d.found.get_children()[0], "values")
            self.assertIn("qMenu.exe", first[2])
            d.found.selection_set("1")
            d.pick_found()
            root.update()
            self.assertEqual(d.form()["windowsPrinter"], "Star TSP143IIIU")
            d.found.selection_set("0")
            d.pick_found()
            root.update()
            f = d.form()
            self.assertEqual((f["connection"], f["host"], f["port"], f["render"]), ("tcp", "192.168.1.50", 9100, "star"))
            self.assertIn("192.168.1.50", gui.ReportWindow(app, d.top, setup_report(full)).text.get("1.0", "end"))
            d.top.destroy()
        finally:
            root.destroy()

    def test_pause_reprint_copy_log_and_type_guard(self):
        temp_home()
        import json
        import time
        import tkinter as tk
        from unittest import mock

        from xordering_printer import config, gui, install, paths

        install.ensure_running = lambda: None
        printer = {"id": "p1", "name": "后厨 Kitchen 1", "host": "POS-G2", "port": 9100, "paperWidth": 80, "render": "image", "enabled": True}
        config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "a" * 64, restaurant={"name": {"en": "Tsunami Sushi"}},
                      printers=[printer], printerLocal={"p1": {"connection": "windows", "windowsPrinter": "Kitchen"}}, paused=True)
        paths.status_path().write_text(json.dumps({"updatedAt": time.time(), "state": "ok", "queue": {"queued": 2, "printed": 1},
                                                   "problems": [{"order_number": 1003, "printer_id": "p1", "last_error": "not sent: Star raster to an ESC/POS driver"}]}), "utf-8")
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root)
            root.update()
            self.assertIn("恢复打印", app.pause_btn.cget("text"))
            self.assertTrue(app.paused_shown and app.problem_shown)
            self.assertIn("#1003", app.problem_band.cget("text"))
            app.copy_log()
            self.assertIn("type=image paper=80mm connection=windows target=Kitchen", root.clipboard_get())
            self.assertNotIn("xpd_" + "a" * 64, root.clipboard_get())
            with mock.patch.object(gui.messagebox, "askyesnocancel", return_value=False):
                app.toggle_pause()  # resume, skip the 2 waiting
            cfg = config.load()
            self.assertFalse(cfg["paused"])
            self.assertEqual((cfg["pausePending"]["paused"], cfg["pausePending"]["skip"]), (False, True))
            # Saving a type that wasn't tested asks first; Star raster to an ESC/POS driver isn't saved at all.
            d = gui.PrinterDialog(app, config.effective_printers(config.load())[0], autodetect=False)
            d.win_printers = [{"name": "Kitchen", "driver": "POS-80C", "port": "USB001", "ip": "", "tcpPort": None}]
            d.render.set("star")
            with mock.patch.object(gui.setup, "save_printer") as save:
                d.save()
                root.update()
                save.assert_not_called()
            self.assertIn("ESC/POS", d.result.cget("text"))
            d.render.set("text")
            with mock.patch.object(gui.messagebox, "askyesno", return_value=False) as ask, mock.patch.object(gui.setup, "save_printer") as save:
                d.save()
                self.assertTrue(ask.called)
                save.assert_not_called()
            d.top.destroy()
        finally:
            root.destroy()


def setup_report(res):
    from xordering_printer import setup

    return setup.diagnostics(dict(res, version="t", at=0, computer="PC", errors={}, interfaces=[], networks=[]))


if __name__ == "__main__":
    unittest.main()
