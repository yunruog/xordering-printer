"""The print loop, every few seconds: poll the server, print what's due, report back.

    poll    print_device_poll: the restaurant, its printers (cached for offline use) and the
            tickets for this computer. New ones are stored in jobs.db before anything else.
    print   each due ticket on its printer. A printer that fails is skipped for the rest of the
            round; its tickets wait (back-off up to 30 s) and print when it is back.
    report  printed tickets (until the server has it), failures (at most once a minute per ticket).

No internet: tickets already received still print, reports wait, and orders placed meanwhile come
in with the next successful poll. Printer offline: tickets wait in jobs.db. Neither loses a ticket.

Each printer is reached the way this computer was set up for it (deliver.py: straight to IP:9100,
or through a Windows printer). A printer shared with the POS (qMenu …) refuses us while the POS
holds it: the error names that program, the ticket waits and is retried (2 s, 5 s, 10 s … 30 s).
Every ticket goes through pipeline.print_tickets() (the same build, check, send and log as the test
page). A ticket that fails the check is never sent; one that may have partly printed is tried at most
jobs.MAX_TRIES times. A second "new" ticket for an order already printed on that printer is skipped.
Paused (this window or the dashboard): tickets wait; on resume they print, or are skipped.

A printer that keeps failing for STUCK_AFTER seconds is flagged `stuck` (the window shows a red
warning and suggests Windows printer mode) and the printers are looked for again in the background
(discovery.json), once per REDISCOVER_EVERY. Every CONNECTION_SAMPLE seconds the worker notes which
programs are connected to printer ports (seen-connections.json): a POS connects only while it prints.
"""
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

import threading

from . import VERSION, api, config, discover, escpos, pipeline, render, ticket
from .jobs import MAX_TRIES, JobStore
from .logs import get as get_log
from .paths import data_dir, jobs_db_path, status_path
from .printer import PrinterError, send

UPDATE_EXIT_CODE = 42
STUCK_AFTER = 180.0
STUCK_ATTEMPTS = 3
REDISCOVER_EVERY = 1800.0
CONNECTION_SAMPLE = 15.0
# Failures after which part of the ticket may have printed: these use up one of the job's MAX_TRIES.
# The others (printer off, busy, in use by the POS, out of paper, reset before reading) only wait.
MAYBE_PRINTED = ("partial", "lost", "driver")


def describe_printer(printer: Dict[str, Any]) -> str:
    from .deliver import describe

    return describe(printer)


def discovery_path():
    return data_dir() / "discovery.json"


class Worker:
    def __init__(self, store: Optional[JobStore] = None, sender: Callable[..., Dict[str, Any]] = send, client_factory: Callable[[Dict[str, Any]], api.Client] = api.client_for,
                 who_uses: Optional[Callable[[str], str]] = None, rediscover: Optional[Callable[[], Dict[str, Any]]] = None):
        self.store = store or JobStore(str(jobs_db_path()))
        self.send = sender
        self.who_uses = who_uses
        self.rediscover_fn = rediscover or (lambda: discover.run())
        self.failing: Dict[str, Dict[str, float]] = {}
        self.rediscovered_at = 0.0
        self.rediscovering = False
        self.last_sample = 0.0
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
        return {p["id"]: p for p in config.effective_printers(self.cfg)}

    # --- one round ---------------------------------------------------------------------------

    def step(self) -> None:
        self.reload_config()
        self.sample_connections()
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
        if not online:
            self.sync_pause(None, token, None)
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
        self.sync_pause(client, token, res.get("paused") if "paused" in res else None)
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
        if self.cfg.get("paused"):
            return  # tickets wait in jobs.db until printing is resumed
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
            if reason == "new" and self.store.printed_order(order.get("id"), pid, row["id"]):
                self.store.skip(row["id"], "duplicate: this order already printed on this printer")
                self.log.warning("order #%s already printed on %s: second ticket %s skipped (use Reprint to print it again)", order.get("number"), printer.get("name"), row["id"])
                continue
            try:
                tickets = self.ticket_lines(job, printer, restaurant)
                status = pipeline.print_tickets(printer, tickets, source="order" if order else "test (dashboard)", job_id=row["id"], order=order,
                                                attempt=int(row["attempts"]) + 1, sender=self.send, who_uses=self.who_uses,
                                                title=f"XOrdering #{order.get('number') or 'test'}")
            except pipeline.BadTicket as e:
                self.store.give_up(row["id"], f"not sent: {e}")
                self.log.error("order #%s NOT sent to %s: %s", order.get("number"), printer.get("name"), e)
                self.printer_state[pid] = dict(self.printer_state.get(pid) or {}, ok=False, error=str(e), code="bad_ticket", at=time.time(), stuck=False)
                continue
            except PrinterError as e:
                maybe = e.code in MAYBE_PRINTED
                attempts = self.store.mark_failed(row["id"], str(e), maybe_printed=maybe)
                down.add(pid)
                if self.printer_state.get(pid, {}).get("ok", True) or attempts == 1:
                    self.log.warning("printer %s (%s): %s; order #%s waits (attempt %s)", printer.get("name"), describe_printer(printer), e, order.get("number"), attempts)
                if (self.store.get(row["id"]) or {"state": ""})["state"] == "failed":
                    self.log.error("order #%s on %s given up after %s tries (part of it may have printed): reprint it by hand", order.get("number"), printer.get("name"), MAX_TRIES)
                self.printer_failed(pid, printer, e)
                continue
            except Exception as e:  # a ticket that can't be laid out must not stop the others
                self.log.exception("ticket for order #%s failed", order.get("number"))
                self.store.give_up(row["id"], f"ticket error: {e}")
                continue
            self.store.mark_printed(row["id"])
            if (status or {}).get("fallback") and printer.get("windowsMethod") != "driver":
                self.log.info("printer %s: the driver refuses raw jobs (%s); printing pictures through the driver from now on", printer.get("name"), status["fallback"])
                self.cfg = config.set_local(pid, windowsMethod="driver")
                self.cfg_mtime = config.mtime()
            was_down = not self.printer_state.get(pid, {}).get("ok", True)
            self.failing.pop(pid, None)
            self.printer_state[pid] = {"ok": True, "error": None, "at": time.time(), "paperLow": bool((status or {}).get("paperLow")), "method": (status or {}).get("method")}
            self.log.info("printed %s on %s%s", f"order #{order.get('number')}" if order else "the test page", printer.get("name"), "" if reason == "new" else f" ({reason})")
            if was_down:
                self.log.info("printer %s is back", printer.get("name"))
                self.store.retry_printer_now(pid)

    @staticmethod
    def ticket_lines(job: Dict[str, Any], printer: Dict[str, Any], restaurant: Dict[str, Any]) -> List[List[ticket.Line]]:
        reason = job.get("reason") or "new"
        order = job.get("order")
        if reason == "test" or not order:
            return ticket.test_tickets(restaurant)
        return [ticket.build(order, restaurant, reason=reason)]

    @classmethod
    def ticket_payload(cls, job: Dict[str, Any], printer: Dict[str, Any], restaurant: Dict[str, Any]) -> bytes:
        return pipeline.build(printer, cls.ticket_lines(job, printer, restaurant))

    # --- pause -------------------------------------------------------------------------------

    def sync_pause(self, client: Optional[api.Client], token: str, server_paused: Optional[bool]) -> None:
        """One pause for the restaurant: the window's switch (pausePending, pushed to the server)
        or the dashboard's (server_paused). Resuming with skip skips the tickets that waited."""
        pending = self.cfg.get("pausePending")
        if isinstance(pending, dict):
            if pending.get("skip") and not pending.get("skippedHere"):
                n = self.store.skip_queued(float(pending.get("at") or time.time()))
                self.log.info("printing resumed; %s waiting ticket(s) skipped", n)
                pending = dict(pending, skippedHere=True)
                self._save(pausePending=pending)
            if client is None:
                return
            try:
                client.set_paused(token, bool(pending.get("paused")), bool(pending.get("skip")))
            except api.ApiError as e:
                self.log.warning("pause not sent to the server: %s", e.code)
                if e.code not in ("invalid_input",) and "function" not in e.code:
                    return
            except api.NetworkError:
                return
            self._save(pausePending=None)
            return
        if server_paused is not None and bool(server_paused) != bool(self.cfg.get("paused")):
            self.log.info("printing %s on the dashboard", "paused" if server_paused else "resumed")
            self._save(paused=bool(server_paused))

    # --- printers that keep failing --------------------------------------------------------

    def printer_failed(self, pid: str, printer: Dict[str, Any], e: PrinterError) -> None:
        now = time.time()
        f = self.failing.setdefault(pid, {"since": now, "count": 0})
        f["count"] += 1
        stuck = now - f["since"] >= STUCK_AFTER and f["count"] >= STUCK_ATTEMPTS
        was_stuck = bool(self.printer_state.get(pid, {}).get("stuck"))
        self.printer_state[pid] = {"ok": False, "error": str(e), "code": getattr(e, "code", "error"), "at": now,
                                   "failingSince": f["since"], "attempts": int(f["count"]), "stuck": stuck, "connection": printer.get("connection", "tcp")}
        if stuck and not was_stuck:
            self.log.warning("printer %s has failed for %d min (%s); looking for printers again", printer.get("name"), (now - f["since"]) // 60, e)
            self.start_rediscovery()

    def start_rediscovery(self, force: bool = False) -> bool:
        """Looks for the printers again in the background (discovery.json), at most every 30 min."""
        if self.rediscovering or (not force and time.time() - self.rediscovered_at < REDISCOVER_EVERY):
            return False
        self.rediscovering = True
        self.rediscovered_at = time.time()

        def work() -> None:
            try:
                res = self.rediscover_fn()
                res["reason"] = "printing kept failing"
                tmp = str(discovery_path()) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(res, fh, ensure_ascii=False)
                os.replace(tmp, discovery_path())
                top = [f"{c['name']} ({discover.address_text(c)}, {discover.source_text(c)})" for c in discover.receipt_printers(res.get("candidates") or [])[:3]]
                self.log.info("printers found again: %s", "; ".join(top) or "none")
            except Exception:
                self.log.exception("looking for printers again failed")
            finally:
                self.rediscovering = False

        threading.Thread(target=work, daemon=True).start()
        return True

    def sample_connections(self) -> None:
        """Notes which programs connect to printer ports (the POS prints only now and then)."""
        if time.time() - self.last_sample < CONNECTION_SAMPLE:
            return
        self.last_sample = time.time()
        try:
            conns = discover.printer_connections()
            if conns:
                discover.remember_connections(conns)
        except Exception as e:
            self.log.debug("connection sample failed: %s", e)

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
            printers[pid] = {"ok": bool(st.get("ok")), "error": st.get("error"), "code": st.get("code"), "paperLow": bool(st.get("paperLow")), "stuck": bool(st.get("stuck"))}
        problems = [{"order": r["order_number"], "printerId": r["printer_id"], "error": (r["last_error"] or "")[:200]} for r in self.store.problems(time.time() - 86400)[:3]]
        return {"version": VERSION, "printers": printers, "queue": self.store.counts(), "paused": bool(self.cfg.get("paused")), "problems": problems}

    def send_status(self, client: api.Client, token: str) -> None:
        """Printer states (paper out, cover open, …) for the dashboard: on a change, else every minute."""
        st = self.status_payload()
        key = json.dumps({"printers": st["printers"], "queued": st["queue"].get("queued"), "paused": st["paused"], "problems": st["problems"]}, sort_keys=True)
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
            for row in self.store.unreported_skipped():
                try:
                    client.report(token, row["id"], "skipped", row["last_error"])
                except api.ApiError as e:
                    if e.code not in ("not_found", "invalid_input"):
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
            "paused": bool(self.cfg.get("paused")),
            "problems": [dict(r) for r in self.store.problems(time.time() - 86400)],
            "rediscovery": {"running": self.rediscovering, "at": self.rediscovered_at or None},
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
