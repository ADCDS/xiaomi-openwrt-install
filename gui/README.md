# Web wizard (gui/)

A friendly local wizard over the command-line installer. It does **not**
reimplement any flashing logic — it builds the right arguments and runs the
proven `install.py` / `updater.py` / `revert.py`, streaming their output into a
step-by-step UI with a stage tracker, a live terminal, the Wi-Fi join banner,
and the resume-manifest path for recovery.

## Run it

From the installer directory:

```sh
python3 gui/app.py
```

It prints a `http://127.0.0.1:<port>/?t=<token>` URL and opens your browser.
Keep the terminal open; `Ctrl-C` stops the server. The server binds
`127.0.0.1` only and every API call carries a one-time session token — it is a
root-capable flashing driver and must not be exposed on the network.

Options (environment variables):

- `RD03V2_GUI_PORT` — fixed port (default: a free one chosen by the OS)
- `RD03V2_GUI_NO_BROWSER=1` — do not open a browser automatically

## Requirements

Same as the CLI: Linux, Python 3.9+, `sshpass`, `openssl`, OpenSSH, `iproute2`.
The first screen checks these for you.

**Windows:** the flashing engine is Linux-only (it drives `ip`,
`/sys/class/net`, network namespaces and raw interfaces). Run it under **WSL**:

```powershell
wsl --install
```

then, inside the WSL shell, `sudo apt install sshpass openssl`, clone the
project there, and run `python3 gui/app.py` from WSL. The wizard detects
Windows and shows this guidance instead of letting a flash fail halfway.

## Safety

- Start with a **dry run** (the wizard offers it first): it downloads and
  verifies the image set without touching the router.
- Install and revert use the proven A/B path — stock stays intact in the slot
  it booted from until the new system is verified.
- The update path (installed OpenWrt → new build) verifies the kernel write by
  read-back **before** rebooting, and every irreversible write is behind an
  explicit confirmation. The only recovery net for a torn write is UART, so
  leave power alone during a write.

## Files

| File | Purpose |
|---|---|
| `app.py` | stdlib HTTP server: static files + JSON API + SSE log stream |
| `runner.py` | builds argv for the CLI tools, runs them, parses stages |
| `prereqs.py` | environment checks for the first screen |
| `static/` | the single-page wizard (`index.html`, `app.js`, `styles.css`) |
