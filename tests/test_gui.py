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
            config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "a" * 64,
                          restaurant={"name": {"zh": "金莲小馆", "en": "Golden Lotus"}},
                          printers=[{"id": "p1", "name": "后厨 1", "host": "192.168.1.201", "port": 9100, "paperWidth": 80, "render": "text", "enabled": True}])
            app.render()
            root.update()
            self.assertEqual(app.view, "status")
            self.assertEqual(len(app.tree.get_children()), 1)
            d = gui.PrinterDialog(app, config.load()["printers"][0])
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


if __name__ == "__main__":
    unittest.main()
