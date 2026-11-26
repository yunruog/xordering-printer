"""XOrderingPrinter.exe

    (no arguments)   install / upgrade (Windows), make sure the service runs, open the setup window
    --service        the background service (started at sign-in; one per computer)
    --worker         the print loop (run by the service)
    --site URL       use another XOrdering site (testing); saved in the config
    --uninstall      stop and remove the sign-in start and the watchdog task (data is kept)
    --self-check     draw the test page with the bundled font and encode it (exit 1 on a problem;
                     writes self-check.json in the data folder): the release checks the exe with it
"""
import argparse
import json
import sys

from . import VERSION, logs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="XOrderingPrinter", add_help=True)
    p.add_argument("--service", action="store_true")
    p.add_argument("--after-update", action="store_true")
    p.add_argument("--worker", action="store_true")
    p.add_argument("--site")
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--version", action="version", version=VERSION)
    args, _ = p.parse_known_args(argv)

    if args.site:
        from . import config

        config.update(site=args.site, supabaseUrl="", publishableKey="")
    if args.self_check:
        return self_check()
    if args.worker:
        logs.setup("worker")
        from .service import run_worker

        return run_worker()
    if args.service:
        logs.setup("service")
        from .service import run_service

        return run_service(after_update=args.after_update)
    logs.setup("window")
    if args.uninstall:
        from .install import uninstall

        uninstall()
        return 0
    log = logs.get()
    try:
        from .install import ensure_installed, ensure_running

        ensure_installed()
        ensure_running()
    except Exception:
        log.exception("install/start failed")
    from . import gui

    return gui.run()


def self_check() -> int:
    """The packaged program can draw tickets: bundled font found, Chinese drawn, Star bytes made."""
    from . import escpos, render, star, ticket
    from .paths import data_dir

    result = {"version": VERSION, "font": render.font_path(), "bundledFont": render.FONT_FILE.exists()}
    try:
        imgs = render.render_many(ticket.test_tickets({"name": {"en": "Self check"}}), 80)
        from PIL import Image, ImageDraw

        glyph = Image.new("L", (40, 40), 255)
        ImageDraw.Draw(glyph).text((4, 4), "谢", font=render.font(25), fill=0)
        data = escpos.tickets_bytes(ticket.test_tickets({}), {"render": "star"})
        result.update(sizes=[list(i.size) for i in imgs], chineseInk=sum(1 for v in glyph.tobytes() if v < 128),
                      starBytes=len(data), starStart=data[:4].hex(), ok=True)
        result["ok"] = bool(result["bundledFont"] and result["chineseInk"] > 20 and data.startswith(star.INIT) and all(i.size[0] == 576 for i in imgs))
    except Exception as e:  # reported, not raised: the release step reads the file
        result.update(ok=False, error=f"{type(e).__name__}: {e}")
    (data_dir() / "self-check.json").write_text(json.dumps(result, indent=1), "utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
