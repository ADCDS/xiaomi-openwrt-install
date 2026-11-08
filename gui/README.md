# Web wizard (gui/)

A local wizard over the command-line installer, in English, Russian, Simplified
Chinese and Brazilian Portuguese. It does **not** reimplement any flashing
logic: it builds the right arguments and runs `install.py`, `updater.py` or
`revert.py`, streaming their output into a step-by-step UI with a stage tracker,
a live terminal, the Wi-Fi join banner, and the resume-manifest path for
recovery.

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

The language follows the browser and can be switched with the button at the top
right, which cycles through English, Russian, Simplified Chinese and Brazilian
Portuguese, each named in its own script.

Options (environment variables):

- `RD03V2_GUI_PORT`: fixed port (default: a free one chosen by the OS)
- `RD03V2_GUI_TOKEN`: pin the session token instead of generating one; it still
  appears in the URL and in every API call, so a pinned value is only as private
  as the terminal you print it in
- `RD03V2_GUI_NO_BROWSER=1`: do not open a browser automatically

## Requirements

Same as the CLI, per platform. On Linux: Python 3.9+, `sshpass`, `openssl`,
OpenSSH and `iproute2`. On Windows 10/11: Python 3.9+, PowerShell, and the
packages in `requirements-windows.txt` (`paramiko`, `passlib`). The wizard asks
for `openssl` even when no root password is set, because the field is offered;
the CLI only requires it when it has to hash one. The first screen checks these
for you. WSL2's network usually blocks the router's
connection back to the installer, so prefer a real Linux computer.

## What each mode asks for

| Mode | Fields |
|---|---|
| Install | interface (or automatic), release, and an optional block for the installed system: Wi-Fi SSID, key and country, plus its root password |
| Update | interface, release, the installed system's current root password, the keep-settings switch, and — to continue a run that stopped in the RAM installer — the saved config archive |
| Return to stock | interface, release, and the path to an approved stock image |
| Every mode | a dry-run switch at the end; a real run needs the typed `FLASH` confirmation |

Values are checked before anything runs: interface and release names are
restricted to a safe character set, a leading dash is refused where a value could
be read as a flag, and an update refuses a clean install over Wi-Fi because the
new system would come back with its radios off. Secrets travel to the child
process in its environment, never on its command line.

## What protects what

Start with a **dry run** (the wizard offers it first): it writes nothing. An
install or update dry run never contacts the router; a revert dry run logs in and
reads the router's state, but writes nothing.

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

All translated UI strings live in `static/strings.json`, with the same keys
under every language it ships (`en`, `ru`, `zh-CN`, `pt-BR`). Two strings are
deliberately not there: the language names in `static/app.js`, because each is
written in its own script, and the static page title in `static/index.html`. `selftest.py` fails if the
languages diverge, if a key the page uses is missing, or if the language switch
offers a language with no catalogue.
The tools themselves always run in English here, because `runner.py` parses their
output.

## Files

| File | Purpose |
|---|---|
| `app.py` | stdlib HTTP server: static files + JSON API + SSE log stream |
| `runner.py` | builds argv for the CLI tools, runs them, parses stages |
| `prereqs.py` | environment checks for the first screen |
| `static/` | the single-page wizard and its translations |
