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
            gui.PrinterDialog(app, config.load()["printers"][0]).top.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
