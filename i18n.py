"""Russian text for the messages an operator has to read or act on.

Deliberately small: prompts, the instructions to switch networks or leave the
power alone, stage banners, and the failures people actually hit. Low-level
diagnostics stay in English, since that is what gets pasted into bug reports.

The English template is the catalogue key, so anything missing falls back to
English rather than failing, and an unconfigured process (a library caller, the
test suites) always gets English. What must be typed stays literal in both
languages: a confirmation is still YES or RESTORE, in Latin letters.

test_i18n.py keeps this catalogue honest: every key must still occur in the
source, with the same placeholders on both sides.
"""

import os
import sys

LANGUAGES = ("auto", "en", "ru")

_language = "en"

RU = {
    # ---- install.py: prompts ------------------------------------------------
    "[?] {question} -- assumed yes (--yes)":
        "[?] {question} -- подтверждено автоматически (--yes)",
    "[?] {question} [type YES to continue] ":
        "[?] {question} [введите YES латиницей, чтобы продолжить] ",
    "declined":
        "отменено",
    "write {image} to {target} (mtd{index}) and boot it?":
        "Записать {image} в {target} (mtd{index}) и загрузиться с него?",
    "reboot into the RAM initramfs now?":
        "Перезагрузить роутер во временную RAM-систему?",
    "write {image} to NAND? This is the point of no return.":
        "Записать {image} в NAND? После этого вернуться уже нельзя.",
    "\nInstalled-system configuration (root password and SSID may be blank)":
        "\nНастройка устанавливаемой системы (пароль root и SSID можно оставить пустыми)",
    "OpenWrt root password: ":
        "Пароль root для OpenWrt: ",
    "OpenWrt Wi-Fi SSID: ":
        "Имя сети Wi-Fi (SSID) для OpenWrt: ",
    "OpenWrt Wi-Fi password: ":
        "Пароль Wi-Fi для OpenWrt: ",
    "Wi-Fi country code (required when SSID is set, for example BR): ":
        "Код страны для Wi-Fi (обязателен, если задан SSID, например RU): ",
    "Wi-Fi country code (for example BR): ":
        "Код страны для Wi-Fi (например RU): ",
    "\nWi-Fi installation requires permanent OpenWrt Wi-Fi for final verification.":
        "\nПри установке по Wi-Fi для финальной проверки нужна постоянная сеть Wi-Fi в OpenWrt.",
    "Permanent OpenWrt SSID contained in the config archive: ":
        "SSID постоянной сети OpenWrt из архива настроек: ",
    "OpenWrt root password used by the original run: ":
        "Пароль root для OpenWrt, заданный при исходном запуске: ",

    # ---- install.py: stages and instructions --------------------------------
    "\n=== stage 1: pre-flight (nothing is written) ===":
        "\n=== этап 1: предварительные проверки (ничего не записывается) ===",
    "\n=== backup ===":
        "\n=== резервная копия ===",
    "\n=== stage 2: pivot (writes the idle slot and the boot flags) ===":
        "\n=== этап 2: переход в RAM (запись в свободный слот и флагов загрузки) ===",
    "\n=== stage 3: flash (sysupgrade from the RAM system) ===":
        "\n=== этап 3: прошивка (sysupgrade из RAM-системы) ===",
    "[2] Wi-Fi-only: when the stock network disappears, join {ssid!r} with key {key!r}":
        "[2] Установка по Wi-Fi: когда заводская сеть пропадёт, подключитесь к {ssid!r}, пароль {key!r}",
    "[2] rebooting -- stock is still intact in {slot}; if the pivot does not come up, the bootloader falls back to it":
        "[2] перезагрузка -- заводская прошивка цела в {slot}; если переход не удастся, загрузчик вернётся к ней",
    "[3] after the RAM installer network disappears, join permanent SSID {ssid!r}; final verification will wait on {iface} (the password is not printed)":
        "[3] когда временная сеть пропадёт, подключитесь к постоянной сети {ssid!r}; финальная проверка будет ждать на {iface} (пароль не выводится)",
    "[3] after the RAM installer network disappears, connect the router to {iface}; final verification requires that wired interface":
        "[3] когда временная сеть пропадёт, подключите роутер к {iface}; для финальной проверки нужен этот проводной интерфейс",
    "[3] do not touch the power":
        "[3] не отключайте питание",
    "[3] it reformats both UBIs, writes kernel+rootfs, sets the boot flags and reboots":
        "[3] роутер переформатирует оба раздела UBI, запишет ядро и rootfs, выставит флаги загрузки и перезагрузится",
    "[!] sysupgrade was launched; do not run the flash stage again":
        "[!] sysupgrade уже запущен; не запускайте этап прошивки повторно",
    "[!] verification-only continuation:":
        "[!] продолжить только проверкой:",
    "[3] waiting up to {seconds}s for permanent OpenWrt on {where}":
        "[3] ожидание постоянного OpenWrt на {where}, до {seconds} с",
    "[+] OpenWrt is installed on NAND.":
        "[+] OpenWrt установлен в NAND.",
    "[*] after the RAM pivot, a Wi-Fi-only laptop must join {ssid} (key: {key}); this process waits for the router on the selected interface":
        "[*] после перехода в RAM компьютер без кабеля должен подключиться к {ssid} (пароль: {key}); установщик ждёт роутер на выбранном интерфейсе",
    "[*] join permanent SSID {ssid!r} on {iface}; verification will wait without printing its password":
        "[*] подключитесь к постоянной сети {ssid!r} на {iface}; проверка подождёт, пароль не выводится",
    "[+] selected release downloaded and verified":
        "[+] выбранный релиз скачан и проверен",
    "[dry-run] router was not contacted":
        "[пробный запуск] к роутеру не обращались",
    "\n[+] pre-flight passed. Nothing was written.":
        "\n[+] предварительные проверки пройдены. Ничего не записано.",
    "\n[+] pivot done. When the RAM system is reachable, run:":
        "\n[+] переход выполнен. Когда RAM-система станет доступна, выполните:",

    # ---- install.py: failures people act on ---------------------------------
    "[!] to continue this install, run: {command}\n    The router reconnects by itself; after a lost link that can take up to 30 minutes.":
        "[!] чтобы продолжить установку, выполните: {command}\n    Роутер подключится сам; после потери связи это может занять до 30 минут.",
    "[!] the router is in the RAM installer; to continue, run: {command}":
        "[!] роутер находится в RAM-установщике; чтобы продолжить, выполните: {command}",
    "no root shell; the AP may not have come back":
        "root-доступ не получен; возможно, точка доступа не поднялась",
    "no root shell; the AP may not have come back. If it did, Windows Firewall may be blocking the router's connection back to this computer: allow python.exe inbound on TCP 8000, 4444 and 4445 from the router, or see the Windows section of the README.":
        "root-доступ не получен; возможно, точка доступа не поднялась. Если она работает, обратное подключение роутера мог заблокировать брандмауэр Windows: разрешите python.exe входящие TCP 8000, 4444 и 4445 от роутера (см. раздел про брандмауэр в README.ru.md).",
    "cannot determine the router-facing interface; pass --interface IFACE":
        "не удалось определить интерфейс к роутеру; укажите --interface ИМЯ",
    "--interface {interface} does not match the kernel route to {host} via {routed}. Disconnect the competing link or correct the route before consuming the one-shot trigger.":
        "--interface {interface} не совпадает с маршрутом к {host} через {routed}. Отключите конкурирующее подключение или исправьте маршрут, прежде чем расходовать одноразовый триггер.",
    "Wi-Fi SSID must be 1-32 UTF-8 bytes":
        "SSID должен занимать от 1 до 32 байт в UTF-8",
    "WPA2 passphrase must contain printable ASCII characters":
        "Пароль WPA2 может содержать только печатные символы ASCII (латиница, цифры, знаки)",
    "WPA2 passphrase must be 8-63 printable ASCII characters":
        "Пароль WPA2 должен состоять из 8-63 печатных символов ASCII",
    "Wi-Fi country must be a two-letter uppercase code":
        "Код страны должен состоять из двух заглавных латинских букв, например RU",
    "sysupgrade was already launched; do not run the flash stage again. Use the read-only continuation: {command}":
        "sysupgrade уже был запущен; не запускайте этап прошивки повторно. Продолжите только проверкой: {command}",

    # ---- revert.py ----------------------------------------------------------
    "\n=== step 1: pivot into the RAM initramfs ===":
        "\n=== шаг 1: переход во временную RAM-систему ===",
    "[1] when the installed network disappears, join {ssid!r} with key {key!r}; this temporary RAM system has a passwordless root account, so keep the link isolated":
        "[1] когда сеть роутера пропадёт, подключитесь к {ssid!r}, пароль {key!r}; у временной RAM-системы root без пароля, поэтому не допускайте к сети посторонних",
    "[?] write {image} to ubi_kernel and reboot? [type YES] ":
        "[?] Записать {image} в ubi_kernel и перезагрузить? [введите YES латиницей] ",
    "[1] rebooting into the RAM initramfs":
        "[1] перезагрузка во временную RAM-систему",
    "\n=== step 2: restore stock ===":
        "\n=== шаг 2: восстановление заводской прошивки ===",
    "[?] erase OpenWrt and write stock? [type RESTORE] ":
        "[?] Стереть OpenWrt и записать заводскую прошивку? [введите RESTORE латиницей] ",
    "\n=== step 3: wait for stock ===":
        "\n=== шаг 3: ожидание заводской прошивки ===",
    "[+] factory defaults -- the setup wizard is waiting":
        "[+] заводские настройки -- ожидается мастер настройки",
    "[!] stock did not answer on {host} within 7 minutes. It may still be booting; check that this host has an address on that subnet.":
        "[!] заводская прошивка не ответила на {host} за 7 минут. Возможно, она ещё загружается; проверьте, что у компьютера есть адрес в этой подсети.",
}


def _system_language():
    """The operator's language from their environment; only Russian is special."""
    if sys.platform == "win32":
        try:
            import ctypes
            # LANG_RUSSIAN is primary language 0x19 of the UI language.
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "ru" if lang_id & 0x3FF == 0x19 else "en"
        except (AttributeError, ImportError, OSError):
            return "en"
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value:
            return "ru" if value.lower().startswith("ru") else "en"
    return "en"


def set_language(language):
    """Select a language; 'auto' honours XIAOMI_INSTALL_LANG, then the system."""
    global _language
    if language == "auto":
        forced = os.environ.get("XIAOMI_INSTALL_LANG", "").lower()
        language = forced if forced in ("en", "ru") else _system_language()
    if language not in ("en", "ru"):
        raise ValueError(f"unsupported language {language!r}")
    _language = language


def tolerate_console_encoding():
    """Never let a log line kill a run halfway through a flash.

    Python talks UTF-16 to a real Windows console, but a pipe (`| Tee-Object`)
    or a legacy locale gets a code page that cannot encode everything a router
    or a Russian message may contain.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def t(template, **values):
    """`template` in the selected language, formatted with `values`."""
    text = RU.get(template, template) if _language == "ru" else template
    return text.format(**values) if values else text
