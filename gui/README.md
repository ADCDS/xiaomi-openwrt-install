# Web wizard (gui/)

The wizard runs the command-line tools and shows their progress, live output,
Wi-Fi prompts, and recovery manifest path. The interface follows your browser
language. Use the button at the top right to switch between English, Russian,
Simplified Chinese, and Brazilian Portuguese.

## Run it

From the installer directory:

```sh
python3 gui/app.py
```

The wizard opens the session URL in your browser. Keep the terminal open and
press `Ctrl-C` to stop the server. It listens on `127.0.0.1`, authenticates API
requests with a session token, and runs one operation at a time. Keep the URL private.

| Environment variable | Effect |
|---|---|
| `RD03V2_GUI_PORT` | Select a fixed port. By default the OS assigns a free port. |
| `RD03V2_GUI_TOKEN` | Use a fixed session token. It appears in the URL and terminal output. |
| `RD03V2_GUI_NO_BROWSER=1` | Print the session URL without opening the browser. |

## Requirements

The first screen checks for Python 3.9+ and the following platform dependencies.

- **Linux** requires `sshpass`, OpenSSL, OpenSSH (`ssh` and `scp`), and `iproute2`.
- **Windows 10/11** requires PowerShell and the packages in [requirements-windows.txt](../requirements-windows.txt).

## What each mode asks for

| Mode | Fields |
|---|---|
| Install | Interface, release, and an optional root password for OpenWrt. Final verification over Wi-Fi also requires the permanent SSID, Wi-Fi password, and country code. |
| Update | Interface, release, current root password if set, and whether to keep settings. Use Ethernet when clearing settings. To resume from RAM with saved settings, select the configuration archive. |
| Return to stock | interface, release, and the path to an approved stock image |

The wizard offers a dry run first. To start a real run, type the confirmation
word shown in the selected language. Passwords reach the command-line tools
through environment variables.

## What protects what

- **Dry run.** Installation and update check images without contacting the router. Stock restoration also reads the router’s state. All three skip firmware writes.
- **Installation.** The original stock slot provides a fallback until the final `sysupgrade`. See [NOTICE, section 3](../NOTICE).
- **Update and stock restoration.** OpenWrt has one installed kernel. Keep power stable during writes. Recovery after an interrupted write may require UART or stock TFTP. See the [maintenance guide](../docs/maintenance.md) and [tested scenarios](../docs/technical.md#hardware-validation).

## Adding text

Keep the same keys under `en`, `ru`, `zh-CN`, and `pt-BR` in [strings.json](static/strings.json).
The language names live in [app.js](static/app.js) and the page title in [index.html](static/index.html).
Run [selftest.py](../selftest.py) to check translation coverage. The command-line
tools run in English so the wizard can recognize progress messages.

## Files

| File | Purpose |
|---|---|
| [app.py](app.py) | Serves the page, JSON API, and live log stream. |
| [runner.py](runner.py) | Runs the command-line tools and identifies their stages. |
| [prereqs.py](prereqs.py) | Checks the host environment for the first screen. |
| [static/](static/) | Contains the browser interface and translations. |
