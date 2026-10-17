"""Phone agent core. Disabled-by-default gate plus command dispatch.

The agent NEVER touches the network, GPS or keyring unless
`config.is_operational()` (owner enabled it in Settings AND paired it).
Live side effects (HTTP, GeoClue, sound, lock screen) stay behind the
injected callables below, so unit tests exercise the gate with fakes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from xx_find import config as config_mod
from xx_find import protocol

STATUS_DISABLED = "disabled"
STATUS_UNPAIRED = "unpaired"
STATUS_OK = "ok"
STATUS_TRANSPORT_ERROR = "transport-error"


@dataclass
class AgentDeps:
    now: object = field(default_factory=lambda: time.time)
    fetch_commands: object = None
    send_report: object = None
    read_fix: object = None
    ring: object = None
    show_message: object = None
    lock_now: object = None
    erase_user_data: object = None
    on_privacy_event: object = None
    save_config: object = None


class Agent:
    def __init__(self, cfg: config_mod.AgentConfig,
                 deps: AgentDeps | None = None) -> None:
        self.cfg = cfg
        self.deps = deps or AgentDeps()

    def gate(self) -> str:
        if not self.cfg.enabled:
            return STATUS_DISABLED
        if not self.cfg.is_paired():
            return STATUS_UNPAIRED
        return STATUS_OK

    def poll_once(self) -> dict:
        """One poll cycle. Returns a summary; performs zero I/O unless the
        gate passes."""
        status = self.gate()
        if status != STATUS_OK:
            return {"status": status, "acted": []}
        acted = []
        fetch = self.deps.fetch_commands
        if not callable(fetch):
            return {"status": STATUS_TRANSPORT_ERROR, "acted": []}
        try:
            envelopes = fetch(self.cfg)
        except TypeError:
            envelopes = fetch()
        except Exception:
            return {"status": STATUS_TRANSPORT_ERROR, "acted": []}
        for envelope in envelopes or []:
            outcome = self.handle_command(envelope)
            acted.append(outcome)
        return {"status": STATUS_OK, "acted": acted}

    def handle_command(self, envelope: dict) -> dict:
        now = int(self.deps.now())
        try:
            cmd = protocol.validate_command(
                envelope, self.cfg.owner_public_key_hex,
                self.cfg.last_counter, now)
        except protocol.ProtocolError as exc:
            return {"ok": False, "error": str(exc)}
        if cmd["device_id"] != self.cfg.device_id:
            return {"ok": False, "error": "wrong device"}
        prev_counter = self.cfg.last_counter
        self.cfg.last_counter = int(cmd["counter"])
        saver = self.deps.save_config
        if saver is not None:
            try:
                saver(self.cfg)
            except Exception as exc:
                self.cfg.last_counter = prev_counter
                return {"ok": False,
                        "error": "persist failed: %s" % exc}
        if cmd["type"] == "erase" and not self._erase_confirmed(cmd):
            return {"ok": False, "error": "erase not confirmed"}
        handler = {"locate": self._do_locate, "ring": self._do_ring,
                   "message": self._do_message, "lock": self._do_lock,
                   "erase": self._do_erase}.get(cmd["type"])
        if handler is None:
            return {"ok": False, "error": "unsupported command"}
        return handler(cmd)

    def _erase_confirmed(self, cmd: dict) -> bool:
        if not self.cfg.erase_enabled:
            return False
        args = cmd.get("args") or {}
        return args.get("confirmation") == self.cfg.device_id

    def _note_location_use(self) -> None:
        hook = self.deps.on_privacy_event
        if hook is not None:
            try:
                hook("location", "xx-find-agent")
            except Exception:
                pass

    def _do_locate(self, cmd: dict) -> dict:
        del cmd
        if self.deps.read_fix is None or self.deps.send_report is None:
            return {"ok": False, "error": "no location path"}
        try:
            fix = self.deps.read_fix()
        except Exception as exc:
            return {"ok": False, "error": "fix failed: %s" % exc}
        if not fix:
            return {"ok": False, "error": "no fix"}
        self._note_location_use()
        try:
            self.deps.send_report(self.cfg, fix)
        except Exception as exc:
            return {"ok": False, "error": "report failed: %s" % exc}
        return {"ok": True, "action": "locate"}

    def _do_ring(self, cmd: dict) -> dict:
        del cmd
        if self.deps.ring is None:
            return {"ok": False, "error": "no ring path"}
        try:
            self.deps.ring(full_volume=True)
        except Exception as exc:
            return {"ok": False, "error": "ring failed: %s" % exc}
        return {"ok": True, "action": "ring"}

    def _do_message(self, cmd: dict) -> dict:
        if self.deps.show_message is None:
            return {"ok": False, "error": "no message path"}
        args = cmd.get("args") or {}
        try:
            self.deps.show_message(str(args.get("text", "")),
                                   str(args.get("callback", "")))
        except Exception as exc:
            return {"ok": False, "error": "message failed: %s" % exc}
        return {"ok": True, "action": "message"}

    def _do_lock(self, cmd: dict) -> dict:
        del cmd
        if self.deps.lock_now is None:
            return {"ok": False, "error": "no lock path"}
        try:
            self.deps.lock_now()
        except Exception as exc:
            return {"ok": False, "error": "lock failed: %s" % exc}
        return {"ok": True, "action": "lock"}

    def _do_erase(self, cmd: dict) -> dict:
        del cmd
        if self.deps.erase_user_data is None:
            return {"ok": False, "error": "no erase path"}
        try:
            self.deps.erase_user_data()
        except Exception as exc:
            return {"ok": False, "error": "erase failed: %s" % exc}
        return {"ok": True, "action": "erase"}


def main(argv: list[str] | None = None) -> int:
    """Service entry: refuses to run until enabled AND paired."""
    import sys
    args = sys.argv[1:] if argv is None else argv
    _ = args
    cfg = config_mod.load()
    agent = Agent(cfg, AgentDeps(
        save_config=lambda c: config_mod.save(c)))
    status = agent.gate()
    if status != STATUS_OK:
        print("xx-find-agent: not running (%s). Enable in Settings "
              "and pair with a server first." % status, file=sys.stderr)
        return 0
    summary = agent.poll_once()
    print("xx-find-agent: poll finished: %s" % summary["status"])
    return 0
