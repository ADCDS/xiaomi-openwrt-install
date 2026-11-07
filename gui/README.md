# Web wizard (gui/)

A local wizard over the command-line installer, in English and Russian. It does
**not** reimplement any flashing logic: it builds the right arguments and runs
`install.py`, `updater.py` or `revert.py`, streaming their output into a
step-by-step UI with a stage tracker, a live terminal, the Wi-Fi join banner, and
the resume-manifest path for recovery.

## Run it

From the installer directory:

```sh
python3 gui/app.py
```

It prints a `http://127.0.0.1:<port>/?t=<token>` URL and opens your browser. Keep
the terminal open; `Ctrl-C` stops the server. The server binds `127.0.0.1` only,
and every API call carries a per-session token: it is a root-capable flashing
driver and must not be exposed on the network. Only one operation can run at a
time.

The language follows the browser. The button at the top right switches it.

Options (environment variables):

- `RD03V2_GUI_PORT`: fixed port (default: a free one chosen by the OS)
- `RD03V2_GUI_NO_BROWSER=1`: do not open a browser automatically

## Requirements

Same as the CLI: Linux, Python 3.9+, `sshpass`, `openssl`, OpenSSH, `iproute2`.
The first screen checks these for you. WSL2's network usually blocks the router's
connection back to the installer, so prefer a real Linux computer.

## What protects what

Start with a **dry run** (the wizard offers it first): it downloads and verifies
the images without touching the router.

- **Install** is the only path with a fallback. Until the final `sysupgrade`, stock
  stays intact in its own slot, and the bootloader returns to it if the RAM system
  does not come up. That `sysupgrade` is the point of no return.
- **Update** and **return to stock** start from OpenWrt, where the A/B pair no
  longer exists ([NOTICE](../NOTICE) §3). The way into the RAM installer overwrites
  the installed kernel. Both verify that write by read-back before rebooting, and
  the recovery net for a torn write is UART or TFTP. Leave the power alone.

The update has run on a real router only once so far (see the README), and the
wizard says so.

## Adding text

All wording lives in `static/strings.json`, with the same keys under `en` and `ru`.
`selftest.py` fails if the two languages diverge or a key the page uses is missing.
The tools themselves always run in English here, because `runner.py` parses their
output.

## Files

| File | Purpose |
|---|---|
| `app.py` | stdlib HTTP server: static files + JSON API + SSE log stream |
| `runner.py` | builds argv for the CLI tools, runs them, parses stages |
| `prereqs.py` | environment checks for the first screen |
| `static/` | the single-page wizard and its translations |
