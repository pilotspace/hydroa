#!/usr/bin/env python3
"""Reusable, TEST-ONLY PTY harness that drives the npm clack interactive installer (cli.js).

Why a PTY: runClackPreamble short-circuits to `cancelled` when `process.stdin.isTTY` is false
(cli.js:322), so clack's raw-mode keystroke flow can only be exercised through a REAL pseudo-
terminal. A subprocess pipe (test_installer_prompts._run_node) is never a TTY, so it can only
reach the non-interactive / branch-reachability paths. This helper allocates a stdlib pty, spawns
`node bin/cli.js <args>` under the documented force-seam, drives keystrokes by waiting for each
prompt to RENDER before sending input (never fixed sleeps), and returns the rendered output +
the child exit code so a test can assert the file side-effects.

Stdlib only (pty/os/select/termios via os.openpty) — NO new npm/pip dependency. The installer
behavior is UNCHANGED: this drives the existing frozen flow, it does not modify it.

Contract: TASK pty-clack-harness §3 (FROZEN @ v1).
"""
import fcntl
import os
import select
import shutil
import signal
import struct
import subprocess
import termios
import time
from collections import namedtuple
from pathlib import Path

# --- where the real installer entry point lives (repo bin/cli.js) -------------
CLI_JS = Path(__file__).resolve().parent.parent / "bin" / "cli.js"

# --- capability probes (tests guard on these -> honest skip, never a false pass) ----
NODE = shutil.which("node")                                   # None -> skip "node_unavailable"
PTY_SUPPORTED = (os.name == "posix") and hasattr(os, "openpty")  # False -> skip "pty_unavailable"

# --- keystrokes (raw terminal byte sequences) ---------------------------------
ENTER = b"\r"          # confirm a text prompt / accept a select default
DOWN = b"\x1b[B"       # move a clack select down one option
UP = b"\x1b[A"         # move a clack select up one option
CANCEL = b"\x03"       # Ctrl-C — clack reads this as isCancel() in raw mode

# --- return shape -------------------------------------------------------------
PtyRun = namedtuple("PtyRun", ["output", "exit_code"])


class PtyTimeout(Exception):
    """A wait exceeded its deadline. `.code` is "prompt_timeout" | "child_timeout"
    (the §3 rejection codes). The child is always terminated before this is raised."""

    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(code if not detail else f"{code}: {detail}")


def _build_env(env_extra):
    env = dict(os.environ)
    env.pop("CI", None)                                  # a parent CI var must not override the seam
    env["ADD_INSTALLER_FORCE_INTERACTIVE"] = "1"         # force the interactive branch
    if env_extra:
        env.update(env_extra)
    return env


def _terminate(proc):
    """Kill the child process group on every exit path — a leaked node would hang CI."""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def drive_clack(args, steps, *, cwd, env_extra=None, read_timeout=10.0, exit_timeout=15.0):
    """Spawn `node CLI_JS *args` under a PTY and drive the clack flow.

    steps: list of (marker, keys) — wait until `marker` (a substring) renders, then write `keys`.
    Returns PtyRun(output, exit_code). Raises PtyTimeout("prompt_timeout"|"child_timeout").
    """
    if not NODE:
        raise PtyTimeout("node_unavailable", "node not on PATH")
    if not PTY_SUPPORTED:
        raise PtyTimeout("pty_unavailable", "no POSIX pty")

    master, slave = os.openpty()
    # Default openpty() winsize is 0x0 — clack then wraps after EVERY character, so a prompt
    # message never appears as a contiguous substring. Give it a real 80x24 terminal.
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    except OSError:
        pass
    proc = None
    output = bytearray()
    try:
        proc = subprocess.Popen(
            [NODE, str(CLI_JS), *args],
            stdin=slave, stdout=slave, stderr=slave,
            cwd=str(cwd), env=_build_env(env_extra),
            start_new_session=True,                      # own process group -> killpg cleans the whole tree
        )
        os.close(slave)                                  # parent keeps only the master end
        slave = -1

        # --- per step: read until the prompt renders, then send the keystrokes ---
        for marker, keys in steps:
            window = ""                                  # sliding view since the previous match (clack redraws)
            deadline = time.monotonic() + read_timeout
            while marker not in window:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PtyTimeout("prompt_timeout", marker)
                rlist, _, _ = select.select([master], [], [], min(remaining, 0.5))
                if master not in rlist:
                    continue
                try:
                    chunk = os.read(master, 4096)
                except OSError:                          # EIO on Linux when the child has exited
                    chunk = b""
                if not chunk:                            # EOF: child exited before the prompt rendered
                    raise PtyTimeout("prompt_timeout", marker)
                output.extend(chunk)
                window += chunk.decode("utf-8", "replace")
            os.write(master, keys)

        # --- drain output and wait for the child to exit ---
        deadline = time.monotonic() + exit_timeout
        while True:
            rlist, _, _ = select.select([master], [], [], 0.2)
            if master in rlist:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    output.extend(chunk)
            if proc.poll() is not None:
                # one final non-blocking drain so the last frame isn't lost
                while True:
                    rlist, _, _ = select.select([master], [], [], 0.0)
                    if master not in rlist:
                        break
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        break
                    output.extend(chunk)
                break
            if time.monotonic() > deadline:
                raise PtyTimeout("child_timeout")
        return PtyRun(output.decode("utf-8", "replace"), proc.returncode)
    finally:
        _terminate(proc)
        if slave != -1:
            try:
                os.close(slave)
            except OSError:
                pass
        try:
            os.close(master)
        except OSError:
            pass
