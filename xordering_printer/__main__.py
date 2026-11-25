"""XOrderingPrinter.exe

    (no arguments)   install / upgrade (Windows), make sure the service runs, open the setup window
    --service        the background service (started at sign-in; one per computer)
    --worker         the print loop (run by the service)
    --site URL       use another XOrdering site (testing); saved in the config
    --uninstall      stop and remove the sign-in start and the watchdog task (data is kept)
"""
import argparse
import sys

from . import VERSION, logs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="XOrderingPrinter", add_help=True)
    p.add_argument("--service", action="store_true")
    p.add_argument("--after-update", action="store_true")
    p.add_argument("--worker", action="store_true")
    p.add_argument("--site")
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--version", action="version", version=VERSION)
    args, _ = p.parse_known_args(argv)

    if args.site:
        from . import config

        config.update(site=args.site, supabaseUrl="", publishableKey="")
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


if __name__ == "__main__":
    sys.exit(main())
