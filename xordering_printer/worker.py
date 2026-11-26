"""The print loop, every few seconds: poll the server, print what's due, report back.

    poll    print_device_poll: the restaurant, its printers (cached for offline use) and the
            tickets for this computer. New ones are stored in jobs.db before anything else.
    print   each due ticket on its printer. A printer that fails is skipped for the rest of the
            round; its tickets wait (back-off up to 30 s) and print when it is back.
    report  printed tickets (until the server has it), failures (at most once a minute per ticket).

No internet: tickets already received still print, reports wait, and orders placed meanwhile come
in with the next successful poll. Printer offline: tickets wait in jobs.db. Neither loses a ticket.
"""
import json
import os
import time
from typing import Any, Callable, Dict, Optional

from . import VERSION, api, config, escpos, render, ticket
from .jobs import JobStore
from .logs import get as get_log
from .paths import jobs_db_path, status_path
from .printer import PrinterError, send

UPDATE_EXIT_CODE = 42


class Worker:
    def __init__(self, store: Optional[JobStore] = None, sender: Callable[[str, int, bytes], None] = send, client_factory: Callable[[Dict[str, Any]], api.Client] = api.client_for):
        self.store = store or JobStore(str(jobs_db_path()))
        self.send = sender
        self.client_factory = client_factory
        self.log = get_log()
        self.cfg = config.load()
        self.cfg_mtime = config.mtime()
        self.state = "setup"
        self.last_poll_ok: Optional[float] = None
        self.last_error: Optional[str] = None
        self.printer_state: Dict[str, Dict[str, Any]] = {}
        self.revoked_token: Optional[str] = None
        self.preview_done: Optional[str] = None
        self.status_sent: Optional[str] = None
        self.status_sent_at = 0.0

    # --- config ----------------------------------------------------------------------------

    def reload_config(self) -> None:
        m = config.mtime()
        if m != self.cfg_mtime:
            self.cfg = config.load()
            self.cfg_mtime = m

    def _save(self, **changes: Any) -> None:
        self.cfg = config.update(**changes)
        self.cfg_mtime = config.mtime()

    def printers(self) -> Dict[str, Dict[str, Any]]:
        return {p["id"]: p for p in self.cfg.get("printers") or []}

    # --- one round ---------------------------------------------------------------------------

    def step(self) -> None:
        self.reload_config()
        token = self.cfg.get("deviceToken")
        if token and not self.cfg.get("supabaseUrl"):
            try:  # a new --site, or a config from an older version: read the server details again
                from .setup import connect_server

                self.cfg = connect_server()
                self.cfg_mtime = config.mtime()
            except (api.ApiError, api.NetworkError) as e:
                self.state, self.last_error = "offline", f"server config: {e}"
                self.print_due()
                return
        if not config.signed_in(self.cfg):
            self.state = "setup"
            return
        if token == self.revoked_token:
            self.state = "revoked"
            return
        client = self.client_factory(self.cfg)
        online = self.poll(client, token)
        if self.state == "revoked":
            return
        self.print_due()
        if online:
            self.report(client, token)
            self.send_status(client, token)

    def poll(self, client: api.Client, token: str) -> bool:
        try:
            res = client.poll(token)
        except api.ApiError as e:
            if e.code == "device_revoked":
                self.log.warning("this computer was signed out on the dashboard; sign in again in the setup window")
                self.revoked_token = token
                self.state = "revoked"
                self.last_error = "device_revoked"
                return False
            self.state = "offline"
            self.last_error = f"server: {e.code}"
            self.log.warning("poll failed: %s", e.code)
            return False
        except api.NetworkError as e:
            if self.state != "offline":
                self.log.warning("server unreachable, printing what's queued and retrying: %s", e)
            self.state = "offline"
            self.last_error = f"network: {e}"
            return False
        if self.state == "offline":
            self.log.info("server reachable again")
        self.state = "ok"
        self.last_error = None
        self.last_poll_ok = time.time()
        changes: Dict[str, Any] = {}
        if res.get("printers") != self.cfg.get("printers"):
            changes["printers"] = res.get("printers") or []
        restaurant = res.get("restaurant")
        if restaurant and restaurant != self.cfg.get("restaurant"):
            changes["restaurant"] = restaurant
        device = res.get("device") or {}
        if device.get("name") and device.get("name") != self.cfg.get("deviceName"):
            changes["deviceName"] = device["name"]
        if changes:
            self._save(**changes)
        preview = res.get("previewRequestedAt")
        if preview and preview != self.preview_done:
            self.preview_done = preview
            self.upload_preview(client, token)
        jobs = res.get("jobs") or []
        listed = set()
        for job in jobs:
            listed.add(job["id"])
            if self.store.add(job):
                self.log.info("ticket received: order #%s on %s (%s)", (job.get("order") or {}).get("number"), self.printers().get(job["printerId"], {}).get("name", job["printerId"]), job.get("reason"))
            else:
                row = self.store.get(job["id"])
                if row is not None and row["state"] == "printed" and row["reported"]:
                    # Printed here, but the server didn't get the report: send it again, don't print again.
                    self.store.db.execute("update jobs set reported = 0 where id = ?", (job["id"],))
        if len(jobs) < 50:
            # Queued here but no longer on the server: the order was rejected or cancelled before it
            # printed, or its printer was removed. Don't print it.
            for row in self.store.due(now=float("inf")):
                if row["id"] not in listed:
                    self.log.info("ticket withdrawn by the server: order #%s", row["order_number"])
                    self.store.drop(row["id"])
        return True

    def print_due(self) -> None:
        printers = self.printers()
        restaurant = self.cfg.get("restaurant") or {}
        down = set()
        for row in self.store.due():
            pid = row["printer_id"]
            if pid in down:
                continue
            printer = printers.get(pid)
            if not printer:
                continue  # its printer isn't known here (yet): wait for the next poll
            job = json.loads(row["payload"])
            order = job.get("order") or {}
            reason = job.get("reason") or "new"
            try:
                payload = self.ticket_payload(job, printer, restaurant)
                status = self.send(printer["host"], int(printer.get("port") or 9100), payload, kind="star" if escpos.kind(printer) == "star" else "escpos")
            except PrinterError as e:
                attempts = self.store.mark_failed(row["id"], str(e))
                down.add(pid)
                if self.printer_state.get(pid, {}).get("ok", True) or attempts == 1:
                    self.log.warning("printer %s (%s): %s; order #%s waits (attempt %s)", printer.get("name"), printer.get("host"), e, order.get("number"), attempts)
                self.printer_state[pid] = {"ok": False, "error": str(e), "code": getattr(e, "code", "error"), "at": time.time()}
                continue
            except Exception as e:  # a ticket that can't be laid out must not stop the others
                self.log.exception("ticket for order #%s failed", order.get("number"))
                self.store.mark_failed(row["id"], f"ticket error: {e}")
                continue
            self.store.mark_printed(row["id"])
            was_down = not self.printer_state.get(pid, {}).get("ok", True)
            self.printer_state[pid] = {"ok": True, "error": None, "at": time.time(), "paperLow": bool((status or {}).get("paperLow"))}
            self.log.info("printed %s on %s%s", f"order #{order.get('number')}" if order else "the test page", printer.get("name"), "" if reason == "new" else f" ({reason})")
            if was_down:
                self.log.info("printer %s is back", printer.get("name"))
                self.store.retry_printer_now(pid)

    @staticmethod
    def ticket_payload(job: Dict[str, Any], printer: Dict[str, Any], restaurant: Dict[str, Any]) -> bytes:
        reason = job.get("reason") or "new"
        order = job.get("order")
        if reason == "test" or not order:
            return escpos.tickets_bytes(ticket.test_tickets(restaurant), printer)
        lines = ticket.build(order, restaurant, reason=reason)
        return escpos.ticket_bytes(lines, printer)

    def upload_preview(self, client: api.Client, token: str) -> None:
        """The dashboard asked for a preview: the sample tickets, drawn exactly as they print."""
        import base64

        try:
            restaurant = self.cfg.get("restaurant") or {}
            images = [base64.b64encode(render.png_bytes(img)).decode("ascii") for img in render.render_many(ticket.test_tickets(restaurant), 80)]
            client.preview(token, images)
            self.log.info("preview sent to the dashboard")
        except (api.ApiError, api.NetworkError) as e:
            self.preview_done = None  # try again next round
            self.log.warning("preview upload failed: %s", e)
        except Exception:
            self.log.exception("preview failed")

    def status_payload(self) -> Dict[str, Any]:
        printers = {}
        for pid, st in self.printer_state.items():
            printers[pid] = {"ok": bool(st.get("ok")), "error": st.get("error"), "code": st.get("code"), "paperLow": bool(st.get("paperLow"))}
        return {"version": VERSION, "printers": printers, "queue": self.store.counts()}

    def send_status(self, client: api.Client, token: str) -> None:
        """Printer states (paper out, cover open, …) for the dashboard: on a change, else every minute."""
        st = self.status_payload()
        key = json.dumps({"printers": st["printers"], "queued": st["queue"].get("queued")}, sort_keys=True)
        if key == self.status_sent and time.time() - self.status_sent_at < 60:
            return
        try:
            client.status(token, st)
            self.status_sent, self.status_sent_at = key, time.time()
        except api.ApiError as e:
            self.status_sent, self.status_sent_at = key, time.time()  # an older server: don't hammer it
            self.log.debug("status not sent: %s", e.code)
        except api.NetworkError:
            pass

    def report(self, client: api.Client, token: str) -> None:
        try:
            for row in self.store.unreported_printed():
                try:
                    client.report(token, row["id"], "printed")
                except api.ApiError as e:
                    if e.code not in ("not_found",):
                        raise
                self.store.mark_reported(row["id"])
            for row in self.store.failures_to_report():
                try:
                    client.report(token, row["id"], "failed", row["last_error"])
                except api.ApiError as e:
                    if e.code not in ("not_found",):
                        raise
                self.store.mark_failure_reported(row["id"])
        except api.NetworkError:
            pass  # next round
        except api.ApiError as e:
            self.log.warning("report failed: %s", e.code)

    # --- status for the setup window -----------------------------------------------------

    def write_status(self) -> None:
        status = {
            "pid": os.getpid(),
            "version": VERSION,
            "updatedAt": time.time(),
            "state": self.state,
            "lastPollOk": self.last_poll_ok,
            "lastError": self.last_error,
            "printers": self.printer_state,
            "queue": self.store.counts(),
        }
        tmp = str(status_path()) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(status, f)
            os.replace(tmp, status_path())
        except OSError:
            pass

    def run_forever(self, update_check: Optional[Callable[[], bool]] = None) -> int:
        self.log.info("worker %s started (pid %s)", VERSION, os.getpid())
        last_prune = 0.0
        while True:
            started = time.time()
            try:
                self.step()
            except Exception:  # never die on one bad round: log it and go on
                self.log.exception("print round failed")
            self.write_status()
            if time.time() - last_prune > 3600:
                last_prune = time.time()
                try:
                    self.store.prune()
                except Exception:
                    self.log.exception("prune failed")
            if update_check is not None:
                try:
                    if self.store.counts()["queued"] == 0 and update_check():
                        self.log.info("update downloaded; restarting into it")
                        return UPDATE_EXIT_CODE
                except Exception:
                    self.log.exception("update check failed")
            time.sleep(max(0.2, float(self.cfg.get("pollSeconds") or 4) - (time.time() - started)))
