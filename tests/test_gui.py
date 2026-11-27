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


def setup_report(res):
    from xordering_printer import setup

    return setup.diagnostics(dict(res, version="t", at=0, computer="PC", errors={}, interfaces=[], networks=[]))


if __name__ == "__main__":
    unittest.main()
