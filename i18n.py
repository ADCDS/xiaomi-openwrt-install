"""Translations for the messages an operator has to read or act on.

Deliberately small: prompts, the instructions to switch networks or leave the
power alone, stage banners, and the failures people actually hit. Low-level
diagnostics stay in English, since that is what gets pasted into bug reports.

The English template is the catalogue key, so anything missing falls back to
English rather than failing, and an unconfigured process (a library caller, the
test suites) always gets English. What must be typed stays literal in every
language: a confirmation is still YES or RESTORE, in Latin letters.

Only the languages that have a catalogue appear in LANGUAGES beyond "auto" and
"en". set_language() also accepts the aliases "cn" and "pt", so a locale or
XIAOMI_INSTALL_LANG may say either; the command-line parsers list only the
canonical codes, because argparse matches --lang choices exactly.
test_i18n.py keeps each catalogue honest: every key must still occur
in the source, with the same placeholders, leading newline and trailing space
as the English template it replaces.
"""

import os
import sys

LANGUAGES = ("auto", "en", "ru", "zh-CN", "pt-BR")

# Every language that has a catalogue. English needs none: it is the key.
TRANSLATED = ("ru", "zh-CN", "pt-BR")

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

    # ---- updater.py ---------------------------------------------------------
    "{host} refused the root password offered. Pass the current one with --root-password. For a router set to SSH keys only (PasswordAuth off), use --ssh-key-auth. Nothing was written.":
        "{host} отклонил указанный пароль root. Укажите текущий через --root-password. Для роутера с входом только по ключам (PasswordAuth off) используйте --ssh-key-auth. Ничего не записано.",
    "only {available} KiB of memory is available and {image} needs {needed} KiB. Free some first, for example by stopping services you can spare, then run this again.":
        "доступно только {available} КиБ памяти, а для {image} нужно {needed} КиБ. Освободите память, например остановив необязательные службы, и запустите снова.",
    "\n=== stage 3: flash (sysupgrade from the installed system) ===":
        "\n=== этап 3: прошивка (sysupgrade из установленной системы) ===",
    "update {host} in place to {tag} with {image}?":
        "Обновить {host} на месте до {tag} образом {image}?",
    "[3] the running release refuses an in-place sysupgrade, as v2.0.0 to v2.1.2 do. That is its only objection to this image, so this update passes -F.":
        "[3] запущенный релиз отказывается выполнять sysupgrade на месте, как v2.0.0–v2.1.2. Других возражений к этому образу у него нет, поэтому обновление идёт с -F.",
    "[3] sysupgrade writes the new release in place and reboots":
        "[3] sysupgrade записывает новый релиз на месте и перезагружает роутер",
    "a clean install leaves Wi-Fi disabled, so it cannot be verified over Wi-Fi. Connect a cable and pass --verify-interface, or keep the settings.":
        "после чистой установки Wi-Fi выключен, поэтому проверить её по Wi-Fi нельзя. Подключите кабель и укажите --verify-interface либо сохраните настройки.",
    "the router is in the RAM installer, whose wireless config is the installer's, not yours. Pass --wifi-ssid, or --config-backup with the archive the earlier run saved.":
        'роутер находится в RAM-установщике, и его настройки Wi-Fi -- это настройки установщика, а не ваши. Укажите --wifi-ssid или --config-backup с архивом, сохранённым прошлым запуском.',
    'the config archive does not enable a Wi-Fi network, so the update cannot be verified over Wi-Fi. Pass --verify-interface to verify over a cable.':
        'архив настроек не включает сеть Wi-Fi, поэтому обновление нельзя проверить по Wi-Fi. Укажите --verify-interface, чтобы проверить по кабелю.',
    "cannot tell which SSID to rejoin after the update; pass it with --wifi-ssid, or verify over a cable with --verify-interface.":
        "неизвестно, к какой сети подключаться после обновления; укажите её через --wifi-ssid или проверяйте по кабелю с --verify-interface.",
    "The installed kernel has been replaced by the RAM installer, so the router boots into that now. Reach it, then run this again with {keep} to continue; do not skip this step.":
        "Ядро установленной системы заменено RAM-установщиком, и теперь роутер загружается в него. Подключитесь к нему и запустите снова с {keep}, чтобы продолжить; не пропускайте этот шаг.",
    "The pivot did not finish. ubi_kernel held the installed kernel, so nothing bootable is left there: do NOT reboot or power-cycle. Re-run the same command with {keep} to retry the write.":
        "Переход не завершён. В ubi_kernel было ядро установленной системы, поэтому загружаться там больше нечему: НЕ перезагружайте роутер и не отключайте питание. Повторите ту же команду с {keep}, чтобы повторить запись.",
    "this is the RAM installer, so its config is not yours. Pass --config-backup with the config-backup.tar.gz the earlier run saved, or --no-keep-settings to install clean.":
        "это RAM-установщик, и его настройки не ваши. Укажите --config-backup с файлом config-backup.tar.gz из предыдущего запуска или --no-keep-settings для чистой установки.",
    "[!] this config turns off password login, and the final check logs in by password. It will report that it could not verify the router even when the update worked: when it starts waiting, check with your SSH key instead.":
        "[!] в этих настройках вход по паролю отключён, а финальная проверка входит по паролю. Она сообщит, что не смогла проверить роутер, даже если обновление прошло успешно: когда она начнёт ждать, проверьте роутер своим SSH-ключом.",
    "\n=== stage 2: pivot into the RAM installer ===":
        "\n=== этап 2: переход в RAM-установщик ===",
    "[2] there is no A/B fallback on an installed system: this overwrites the installed kernel. Leave the power alone from here.":
        "[2] на установленной системе нет A/B-резерва: ядро установленной системы будет перезаписано. С этого момента не отключайте питание.",
    "[2] when this network disappears, join {ssid!r} with key {key!r}; the RAM system has a passwordless root account, so keep the link isolated":
        "[2] когда эта сеть пропадёт, подключитесь к {ssid!r}, пароль {key!r}; у RAM-системы root без пароля, поэтому не допускайте к сети посторонних",
    "overwrite ubi_kernel with {image} and reboot? From here the only way out is finishing the update, or UART.":
        "Перезаписать ubi_kernel образом {image} и перезагрузить? После этого останется только завершить обновление либо UART.",
    "[+] update complete: your settings were restored.":
        "[+] обновление завершено: настройки восстановлены.",
    "[+] update complete: the new release is running with default settings.":
        "[+] обновление завершено: новый релиз работает с настройками по умолчанию.",

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

ZH_CN = {
    # ---- install.py: prompts ------------------------------------------------
    "[?] {question} -- assumed yes (--yes)":
        "[?] {question} —— 已按“是”处理（--yes）",
    "[?] {question} [type YES to continue] ":
        "[?] {question} [请输入 YES（拉丁字母）以继续] ",
    "declined":
        "已取消",
    "write {image} to {target} (mtd{index}) and boot it?":
        "将 {image} 写入 {target}（mtd{index}）并从中启动？",
    "reboot into the RAM initramfs now?":
        "现在重启进入 RAM 临时系统？",
    "write {image} to NAND? This is the point of no return.":
        "将 {image} 写入 NAND？此后将无法回头。",
    "\nInstalled-system configuration (root password and SSID may be blank)":
        "\n待安装系统的配置（root 密码和 SSID 可以留空）",
    "OpenWrt root password: ":
        "OpenWrt root 密码: ",
    "OpenWrt Wi-Fi SSID: ":
        "OpenWrt Wi-Fi SSID: ",
    "OpenWrt Wi-Fi password: ":
        "OpenWrt Wi-Fi 密码: ",
    "Wi-Fi country code (required when SSID is set, for example BR): ":
        "Wi-Fi 国家/地区代码（设置 SSID 时必填，例如 BR）: ",
    "Wi-Fi country code (for example BR): ":
        "Wi-Fi 国家/地区代码（例如 BR）: ",
    "\nWi-Fi installation requires permanent OpenWrt Wi-Fi for final verification.":
        "\n通过 Wi-Fi 安装时，最终验证需要 OpenWrt 提供常驻 Wi-Fi。",
    "Permanent OpenWrt SSID contained in the config archive: ":
        "配置归档中的 OpenWrt 常驻 SSID: ",
    "OpenWrt root password used by the original run: ":
        "原始运行时使用的 OpenWrt root 密码: ",

    # ---- install.py: stages and instructions --------------------------------
    "\n=== stage 1: pre-flight (nothing is written) ===":
        "\n=== 阶段 1：预检（不写入任何内容）===",
    "\n=== backup ===":
        "\n=== 备份 ===",
    "\n=== stage 2: pivot (writes the idle slot and the boot flags) ===":
        "\n=== 阶段 2：切换（写入空闲分区和启动标志）===",
    "\n=== stage 3: flash (sysupgrade from the RAM system) ===":
        "\n=== 阶段 3：刷机（从 RAM 系统执行 sysupgrade）===",
    "[2] Wi-Fi-only: when the stock network disappears, join {ssid!r} with key {key!r}":
        "[2] 仅 Wi-Fi：当原厂网络消失后，请连接到 {ssid!r}（密码 {key!r}）",
    "[2] rebooting -- stock is still intact in {slot}; if the pivot does not come up, the bootloader falls back to it":
        "[2] 重启中 —— 原厂固件仍完好保存在 {slot}；如果切换后无法启动，引导程序会回退到它",
    "[3] after the RAM installer network disappears, join permanent SSID {ssid!r}; final verification will wait on {iface} (the password is not printed)":
        "[3] RAM 安装系统的网络消失后，请连接常驻 SSID {ssid!r}；最终验证会在 {iface} 上等待（不会打印密码）",
    "[3] after the RAM installer network disappears, connect the router to {iface}; final verification requires that wired interface":
        "[3] RAM 安装系统的网络消失后，请把路由器接到 {iface}；最终验证需要该有线接口",
    "[3] do not touch the power":
        "[3] 请勿断开电源",
    "[3] it reformats both UBIs, writes kernel+rootfs, sets the boot flags and reboots":
        "[3] 它将重新格式化两个 UBI 分区，写入 kernel+rootfs，设置启动标志并重启",
    "[!] sysupgrade was launched; do not run the flash stage again":
        "[!] sysupgrade 已启动；请勿再次运行刷机阶段",
    "[!] verification-only continuation:":
        "[!] 仅继续执行验证：",
    "[3] waiting up to {seconds}s for permanent OpenWrt on {where}":
        "[3] 在 {where} 上等待常驻 OpenWrt，最多 {seconds} 秒",
    "[+] OpenWrt is installed on NAND.":
        "[+] OpenWrt 已安装到 NAND。",
    "[*] after the RAM pivot, a Wi-Fi-only laptop must join {ssid} (key: {key}); this process waits for the router on the selected interface":
        "[*] 切换到 RAM 后，只有 Wi-Fi 的笔记本必须连接到 {ssid}（密码：{key}）；本进程会在所选接口上等待路由器",
    "[*] join permanent SSID {ssid!r} on {iface}; verification will wait without printing its password":
        "[*] 在 {iface} 上连接常驻 SSID {ssid!r}；验证会继续等待，且不会打印密码",
    "[+] selected release downloaded and verified":
        "[+] 已下载并校验所选版本",
    "[dry-run] router was not contacted":
        "[试运行] 未连接路由器",
    "\n[+] pre-flight passed. Nothing was written.":
        "\n[+] 预检通过。未写入任何内容。",
    "\n[+] pivot done. When the RAM system is reachable, run:":
        "\n[+] 切换完成。当 RAM 系统可访问时，请运行：",

    # ---- install.py: failures people act on ---------------------------------
    "[!] to continue this install, run: {command}\n    The router reconnects by itself; after a lost link that can take up to 30 minutes.":
        "[!] 要继续本次安装，请运行：{command}\n    路由器会自行重新连接；断链后最多可能需要 30 分钟。",
    "[!] the router is in the RAM installer; to continue, run: {command}":
        "[!] 路由器当前处于 RAM 安装系统；要继续，请运行：{command}",
    "no root shell; the AP may not have come back":
        "未获得 root shell；AP 可能尚未恢复",
    "no root shell; the AP may not have come back. If it did, Windows Firewall may be blocking the router's connection back to this computer: allow python.exe inbound on TCP 8000, 4444 and 4445 from the router, or see the Windows section of the README.":
        "未获得 root shell；AP 可能尚未恢复。如果已经恢复，可能是 Windows 防火墙阻止了路由器回连本机：请允许 python.exe 接收来自路由器的 TCP 8000、4444 和 4445 入站连接，或参阅 README 的 Windows 章节。",
    "cannot determine the router-facing interface; pass --interface IFACE":
        "无法确定连接路由器的接口；请指定 --interface 接口名",
    "--interface {interface} does not match the kernel route to {host} via {routed}. Disconnect the competing link or correct the route before consuming the one-shot trigger.":
        "--interface {interface} 与内核经 {routed} 到 {host} 的路由不一致。请先断开冲突的连接或修正路由，再消耗一次性触发。",
    "Wi-Fi SSID must be 1-32 UTF-8 bytes":
        "Wi-Fi SSID 必须为 1-32 个 UTF-8 字节",
    "WPA2 passphrase must contain printable ASCII characters":
        "WPA2 密码只能包含可打印的 ASCII 字符",
    "WPA2 passphrase must be 8-63 printable ASCII characters":
        "WPA2 密码必须为 8-63 个可打印 ASCII 字符",
    "Wi-Fi country must be a two-letter uppercase code":
        "Wi-Fi 国家/地区代码必须是两个大写字母",
    "sysupgrade was already launched; do not run the flash stage again. Use the read-only continuation: {command}":
        "sysupgrade 已经启动；请勿再次运行刷机阶段。请改用只读的继续命令：{command}",

    # ---- updater.py ---------------------------------------------------------
    "{host} refused the root password offered. Pass the current one with --root-password. For a router set to SSH keys only (PasswordAuth off), use --ssh-key-auth. Nothing was written.":
        "{host} 拒绝了所给的 root 密码。请用 --root-password 传入当前密码。仅允许 SSH 密钥登录（PasswordAuth off）的路由器请使用 --ssh-key-auth。未写入任何内容。",
    "only {available} KiB of memory is available and {image} needs {needed} KiB. Free some first, for example by stopping services you can spare, then run this again.":
        "当前仅剩 {available} KiB 内存，而 {image} 需要 {needed} KiB。请先释放一些内存（例如停止可以暂时停用的服务），然后重新运行。",
    "\n=== stage 3: flash (sysupgrade from the installed system) ===":
        "\n=== 阶段 3：刷机（从已安装的系统执行 sysupgrade）===",
    "update {host} in place to {tag} with {image}?":
        "要用 {image} 将 {host} 就地更新到 {tag} 吗？",
    "[3] the running release refuses an in-place sysupgrade, as v2.0.0 to v2.1.2 do. That is its only objection to this image, so this update passes -F.":
        "[3] 当前运行的版本拒绝就地 sysupgrade（v2.0.0 至 v2.1.2 均如此）。这是它对该镜像唯一的异议，因此本次更新使用 -F。",
    "[3] sysupgrade writes the new release in place and reboots":
        "[3] sysupgrade 将就地写入新版本并重启",
    "a clean install leaves Wi-Fi disabled, so it cannot be verified over Wi-Fi. Connect a cable and pass --verify-interface, or keep the settings.":
        "全新安装会关闭 Wi-Fi，因此无法通过 Wi-Fi 验证。请连接网线并指定 --verify-interface，或保留原有设置。",
    "the router is in the RAM installer, whose wireless config is the installer's, not yours. Pass --wifi-ssid, or --config-backup with the archive the earlier run saved.":
        '路由器当前处于 RAM 安装系统，其无线配置是安装系统的，不是你的。请指定 --wifi-ssid，或用 --config-backup 指定先前运行保存的归档。',
    'the config archive does not enable a Wi-Fi network, so the update cannot be verified over Wi-Fi. Pass --verify-interface to verify over a cable.':
        '该配置归档未启用 Wi-Fi 网络，因此无法通过 Wi-Fi 验证更新。请指定 --verify-interface 通过网线验证。',
    "cannot tell which SSID to rejoin after the update; pass it with --wifi-ssid, or verify over a cable with --verify-interface.":
        "无法确定更新后应重新连接哪个 SSID；请用 --wifi-ssid 指定，或用网线配合 --verify-interface 进行验证。",
    "The installed kernel has been replaced by the RAM installer, so the router boots into that now. Reach it, then run this again with {keep} to continue; do not skip this step.":
        "已安装系统的内核已被 RAM 安装系统替换，路由器现在会启动进入它。请连接到它，然后带上 {keep} 重新运行以继续；不要跳过这一步。",
    "The pivot did not finish. ubi_kernel held the installed kernel, so nothing bootable is left there: do NOT reboot or power-cycle. Re-run the same command with {keep} to retry the write.":
        "切换未完成。ubi_kernel 中原本是已安装系统的内核，那里已没有可启动的内容：请勿重启或断电。请带上 {keep} 重新执行同一命令，以重试写入。",
    "this is the RAM installer, so its config is not yours. Pass --config-backup with the config-backup.tar.gz the earlier run saved, or --no-keep-settings to install clean.":
        "这是 RAM 安装系统，其配置并非你的配置。请用 --config-backup 指定先前运行保存的 config-backup.tar.gz，或用 --no-keep-settings 进行全新安装。",
    "[!] this config turns off password login, and the final check logs in by password. It will report that it could not verify the router even when the update worked: when it starts waiting, check with your SSH key instead.":
        "[!] 该配置关闭了密码登录，而最终检查通过密码登录。即使更新成功，它也会报告无法验证路由器：当它开始等待时，请改用 SSH 密钥检查。",
    "\n=== stage 2: pivot into the RAM installer ===":
        "\n=== 阶段 2：切换到 RAM 安装系统 ===",
    "[2] there is no A/B fallback on an installed system: this overwrites the installed kernel. Leave the power alone from here.":
        "[2] 已安装的系统没有 A/B 回退：这会覆盖已安装的内核。此后请勿断开电源。",
    "[2] when this network disappears, join {ssid!r} with key {key!r}; the RAM system has a passwordless root account, so keep the link isolated":
        "[2] 当此网络消失后，请连接到 {ssid!r}（密码 {key!r}）；RAM 系统的 root 账户没有密码，因此请保持该链路隔离",
    "overwrite ubi_kernel with {image} and reboot? From here the only way out is finishing the update, or UART.":
        "用 {image} 覆盖 ubi_kernel 并重启？此后唯一的出路是完成更新，或使用 UART。",
    "[+] update complete: your settings were restored.":
        "[+] 更新完成：你的设置已恢复。",
    "[+] update complete: the new release is running with default settings.":
        "[+] 更新完成：新版本正以默认设置运行。",

    # ---- revert.py ----------------------------------------------------------
    "\n=== step 1: pivot into the RAM initramfs ===":
        "\n=== 步骤 1：切换到 RAM 临时系统 ===",
    "[1] when the installed network disappears, join {ssid!r} with key {key!r}; this temporary RAM system has a passwordless root account, so keep the link isolated":
        "[1] 当已安装系统的网络消失后，请连接到 {ssid!r}（密码 {key!r}）；该临时 RAM 系统的 root 账户没有密码，因此请保持该链路隔离",
    "[?] write {image} to ubi_kernel and reboot? [type YES] ":
        "[?] 将 {image} 写入 ubi_kernel 并重启？[请输入 YES（拉丁字母）] ",
    "[1] rebooting into the RAM initramfs":
        "[1] 正在重启进入 RAM 临时系统",
    "\n=== step 2: restore stock ===":
        "\n=== 步骤 2：恢复原厂固件 ===",
    "[?] erase OpenWrt and write stock? [type RESTORE] ":
        "[?] 擦除 OpenWrt 并写入原厂固件？[请输入 RESTORE（拉丁字母）] ",
    "\n=== step 3: wait for stock ===":
        "\n=== 步骤 3：等待原厂固件 ===",
    "[+] factory defaults -- the setup wizard is waiting":
        "[+] 已恢复出厂设置 —— 设置向导正在等待",
    "[!] stock did not answer on {host} within 7 minutes. It may still be booting; check that this host has an address on that subnet.":
        "[!] 原厂固件在 7 分钟内未在 {host} 上响应。它可能仍在启动；请确认本机在该子网上有地址。",
}

PT_BR = {
    # ---- install.py: prompts ------------------------------------------------
    "[?] {question} -- assumed yes (--yes)":
        "[?] {question} -- assumido como sim (--yes)",
    "[?] {question} [type YES to continue] ":
        "[?] {question} [digite YES para continuar] ",
    "declined":
        "recusado",
    "write {image} to {target} (mtd{index}) and boot it?":
        "gravar {image} em {target} (mtd{index}) e iniciar a partir dele?",
    "reboot into the RAM initramfs now?":
        "reiniciar agora no initramfs em RAM?",
    "write {image} to NAND? This is the point of no return.":
        "gravar {image} na NAND? Este é o ponto sem retorno.",
    "\nInstalled-system configuration (root password and SSID may be blank)":
        "\nConfiguração do sistema a instalar (a senha de root e o SSID podem ficar em branco)",
    "OpenWrt root password: ":
        "Senha de root do OpenWrt: ",
    "OpenWrt Wi-Fi SSID: ":
        "SSID do Wi-Fi do OpenWrt: ",
    "OpenWrt Wi-Fi password: ":
        "Senha do Wi-Fi do OpenWrt: ",
    "Wi-Fi country code (required when SSID is set, for example BR): ":
        "Código de país do Wi-Fi (obrigatório quando o SSID é definido, por exemplo BR): ",
    "Wi-Fi country code (for example BR): ":
        "Código de país do Wi-Fi (por exemplo BR): ",
    "\nWi-Fi installation requires permanent OpenWrt Wi-Fi for final verification.":
        "\nA instalação por Wi-Fi exige um Wi-Fi permanente no OpenWrt para a verificação final.",
    "Permanent OpenWrt SSID contained in the config archive: ":
        "SSID permanente do OpenWrt contido no arquivo de configuração: ",
    "OpenWrt root password used by the original run: ":
        "Senha de root do OpenWrt usada na execução original: ",

    # ---- install.py: stages and instructions --------------------------------
    "\n=== stage 1: pre-flight (nothing is written) ===":
        "\n=== etapa 1: pré-verificação (nada é gravado) ===",
    "\n=== backup ===":
        "\n=== cópia de segurança ===",
    "\n=== stage 2: pivot (writes the idle slot and the boot flags) ===":
        "\n=== etapa 2: transição (grava o slot ocioso e os sinalizadores de boot) ===",
    "\n=== stage 3: flash (sysupgrade from the RAM system) ===":
        "\n=== etapa 3: gravação (sysupgrade a partir do sistema em RAM) ===",
    "[2] Wi-Fi-only: when the stock network disappears, join {ssid!r} with key {key!r}":
        "[2] Somente Wi-Fi: quando a rede de fábrica desaparecer, conecte-se a {ssid!r} com a chave {key!r}",
    "[2] rebooting -- stock is still intact in {slot}; if the pivot does not come up, the bootloader falls back to it":
        "[2] reiniciando -- o firmware de fábrica continua intacto em {slot}; se a transição não subir, o bootloader volta para ele",
    "[3] after the RAM installer network disappears, join permanent SSID {ssid!r}; final verification will wait on {iface} (the password is not printed)":
        "[3] depois que a rede do instalador em RAM desaparecer, conecte-se ao SSID permanente {ssid!r}; a verificação final aguardará em {iface} (a senha não é exibida)",
    "[3] after the RAM installer network disappears, connect the router to {iface}; final verification requires that wired interface":
        "[3] depois que a rede do instalador em RAM desaparecer, conecte o roteador a {iface}; a verificação final exige essa interface com fio",
    "[3] do not touch the power":
        "[3] não mexa na alimentação",
    "[3] it reformats both UBIs, writes kernel+rootfs, sets the boot flags and reboots":
        "[3] ele reformata as duas UBIs, grava kernel+rootfs, define os sinalizadores de boot e reinicia",
    "[!] sysupgrade was launched; do not run the flash stage again":
        "[!] o sysupgrade já foi iniciado; não execute a etapa de gravação novamente",
    "[!] verification-only continuation:":
        "[!] continuação apenas para verificação:",
    "[3] waiting up to {seconds}s for permanent OpenWrt on {where}":
        "[3] aguardando até {seconds}s pelo OpenWrt permanente em {where}",
    "[+] OpenWrt is installed on NAND.":
        "[+] O OpenWrt está instalado na NAND.",
    "[*] after the RAM pivot, a Wi-Fi-only laptop must join {ssid} (key: {key}); this process waits for the router on the selected interface":
        "[*] após a transição para a RAM, um notebook somente Wi-Fi precisa se conectar a {ssid} (chave: {key}); este processo aguarda o roteador na interface selecionada",
    "[*] join permanent SSID {ssid!r} on {iface}; verification will wait without printing its password":
        "[*] conecte-se ao SSID permanente {ssid!r} em {iface}; a verificação aguardará sem exibir a senha",
    "[+] selected release downloaded and verified":
        "[+] versão selecionada baixada e verificada",
    "[dry-run] router was not contacted":
        "[simulação] o roteador não foi contatado",
    "\n[+] pre-flight passed. Nothing was written.":
        "\n[+] pré-verificação aprovada. Nada foi gravado.",
    "\n[+] pivot done. When the RAM system is reachable, run:":
        "\n[+] transição concluída. Quando o sistema em RAM estiver acessível, execute:",

    # ---- install.py: failures people act on ---------------------------------
    "[!] to continue this install, run: {command}\n    The router reconnects by itself; after a lost link that can take up to 30 minutes.":
        "[!] para continuar esta instalação, execute: {command}\n    O roteador se reconecta sozinho; depois de perder o link, isso pode levar até 30 minutos.",
    "[!] the router is in the RAM installer; to continue, run: {command}":
        "[!] o roteador está no instalador em RAM; para continuar, execute: {command}",
    "no root shell; the AP may not have come back":
        "sem shell de root; o AP pode não ter voltado",
    "no root shell; the AP may not have come back. If it did, Windows Firewall may be blocking the router's connection back to this computer: allow python.exe inbound on TCP 8000, 4444 and 4445 from the router, or see the Windows section of the README.":
        "sem shell de root; o AP pode não ter voltado. Se voltou, o Firewall do Windows pode estar bloqueando a conexão de retorno do roteador para este computador: permita a entrada do python.exe nas portas TCP 8000, 4444 e 4445 vindas do roteador, ou consulte a seção do Windows no README.",
    "cannot determine the router-facing interface; pass --interface IFACE":
        "não foi possível determinar a interface voltada ao roteador; informe --interface IFACE",
    "--interface {interface} does not match the kernel route to {host} via {routed}. Disconnect the competing link or correct the route before consuming the one-shot trigger.":
        "--interface {interface} não corresponde à rota do kernel para {host} via {routed}. Desconecte o link concorrente ou corrija a rota antes de consumir o gatilho de uso único.",
    "Wi-Fi SSID must be 1-32 UTF-8 bytes":
        "o SSID do Wi-Fi deve ter de 1 a 32 bytes em UTF-8",
    "WPA2 passphrase must contain printable ASCII characters":
        "a senha do WPA2 deve conter apenas caracteres ASCII imprimíveis",
    "WPA2 passphrase must be 8-63 printable ASCII characters":
        "a senha do WPA2 deve ter de 8 a 63 caracteres ASCII imprimíveis",
    "Wi-Fi country must be a two-letter uppercase code":
        "o país do Wi-Fi deve ser um código de duas letras maiúsculas",
    "sysupgrade was already launched; do not run the flash stage again. Use the read-only continuation: {command}":
        "o sysupgrade já foi iniciado; não execute a etapa de gravação novamente. Use a continuação somente leitura: {command}",

    # ---- updater.py ---------------------------------------------------------
    "{host} refused the root password offered. Pass the current one with --root-password. For a router set to SSH keys only (PasswordAuth off), use --ssh-key-auth. Nothing was written.":
        "{host} recusou a senha de root informada. Passe a atual com --root-password. Para um roteador configurado para apenas chaves SSH (PasswordAuth off), use --ssh-key-auth. Nada foi gravado.",
    "only {available} KiB of memory is available and {image} needs {needed} KiB. Free some first, for example by stopping services you can spare, then run this again.":
        "só há {available} KiB de memória disponível e {image} precisa de {needed} KiB. Libere um pouco primeiro, por exemplo parando serviços dispensáveis, e execute isto de novo.",
    "\n=== stage 3: flash (sysupgrade from the installed system) ===":
        "\n=== etapa 3: gravação (sysupgrade a partir do sistema instalado) ===",
    "update {host} in place to {tag} with {image}?":
        "atualizar {host} no lugar para {tag} com {image}?",
    "[3] the running release refuses an in-place sysupgrade, as v2.0.0 to v2.1.2 do. That is its only objection to this image, so this update passes -F.":
        "[3] a versão em execução recusa um sysupgrade no lugar, como as v2.0.0 a v2.1.2. Essa é a única objeção dela a esta imagem, então esta atualização passa -F.",
    "[3] sysupgrade writes the new release in place and reboots":
        "[3] o sysupgrade grava a nova versão no lugar e reinicia",
    "a clean install leaves Wi-Fi disabled, so it cannot be verified over Wi-Fi. Connect a cable and pass --verify-interface, or keep the settings.":
        "uma instalação limpa deixa o Wi-Fi desativado, então não é possível verificá-la por Wi-Fi. Conecte um cabo e passe --verify-interface, ou mantenha as configurações.",
    "the router is in the RAM installer, whose wireless config is the installer's, not yours. Pass --wifi-ssid, or --config-backup with the archive the earlier run saved.":
        'o roteador está no instalador em RAM, e a configuração de Wi-Fi dele é a do instalador, não a sua. Passe --wifi-ssid, ou --config-backup com o arquivo salvo pela execução anterior.',
    'the config archive does not enable a Wi-Fi network, so the update cannot be verified over Wi-Fi. Pass --verify-interface to verify over a cable.':
        'o arquivo de configuração não ativa uma rede Wi-Fi, então a atualização não pode ser verificada por Wi-Fi. Passe --verify-interface para verificar por cabo.',
    "cannot tell which SSID to rejoin after the update; pass it with --wifi-ssid, or verify over a cable with --verify-interface.":
        "não é possível saber a qual SSID se reconectar após a atualização; informe-o com --wifi-ssid, ou verifique por cabo com --verify-interface.",
    "The installed kernel has been replaced by the RAM installer, so the router boots into that now. Reach it, then run this again with {keep} to continue; do not skip this step.":
        "O kernel instalado foi substituído pelo instalador em RAM, então o roteador agora inicializa nele. Acesse-o e execute isto de novo com {keep} para continuar; não pule esta etapa.",
    "The pivot did not finish. ubi_kernel held the installed kernel, so nothing bootable is left there: do NOT reboot or power-cycle. Re-run the same command with {keep} to retry the write.":
        "A transição não terminou. O ubi_kernel continha o kernel do sistema instalado, então não sobrou nada inicializável ali: NÃO reinicie nem corte a energia. Execute o mesmo comando com {keep} para repetir a gravação.",
    "this is the RAM installer, so its config is not yours. Pass --config-backup with the config-backup.tar.gz the earlier run saved, or --no-keep-settings to install clean.":
        "este é o instalador em RAM, então a configuração dele não é a sua. Passe --config-backup com o config-backup.tar.gz salvo pela execução anterior, ou --no-keep-settings para instalar do zero.",
    "[!] this config turns off password login, and the final check logs in by password. It will report that it could not verify the router even when the update worked: when it starts waiting, check with your SSH key instead.":
        "[!] esta configuração desativa o login por senha, e a verificação final entra por senha. Ela vai relatar que não conseguiu verificar o roteador mesmo quando a atualização funcionou: quando começar a esperar, verifique com sua chave SSH.",
    "\n=== stage 2: pivot into the RAM installer ===":
        "\n=== etapa 2: transição para o instalador em RAM ===",
    "[2] there is no A/B fallback on an installed system: this overwrites the installed kernel. Leave the power alone from here.":
        "[2] não existe fallback A/B em um sistema instalado: isto sobrescreve o kernel instalado. Não mexa na alimentação a partir daqui.",
    "[2] when this network disappears, join {ssid!r} with key {key!r}; the RAM system has a passwordless root account, so keep the link isolated":
        "[2] quando esta rede desaparecer, conecte-se a {ssid!r} com a chave {key!r}; o sistema em RAM tem root sem senha, então mantenha o link isolado",
    "overwrite ubi_kernel with {image} and reboot? From here the only way out is finishing the update, or UART.":
        "sobrescrever ubi_kernel com {image} e reiniciar? A partir daqui, a única saída é terminar a atualização ou usar UART.",
    "[+] update complete: your settings were restored.":
        "[+] atualização concluída: suas configurações foram restauradas.",
    "[+] update complete: the new release is running with default settings.":
        "[+] atualização concluída: a nova versão está rodando com as configurações padrão.",

    # ---- revert.py ----------------------------------------------------------
    "\n=== step 1: pivot into the RAM initramfs ===":
        "\n=== passo 1: transição para o initramfs em RAM ===",
    "[1] when the installed network disappears, join {ssid!r} with key {key!r}; this temporary RAM system has a passwordless root account, so keep the link isolated":
        "[1] quando a rede do sistema instalado desaparecer, conecte-se a {ssid!r} com a chave {key!r}; este sistema temporário em RAM tem root sem senha, então mantenha o link isolado",
    "[?] write {image} to ubi_kernel and reboot? [type YES] ":
        "[?] gravar {image} em ubi_kernel e reiniciar? [digite YES] ",
    "[1] rebooting into the RAM initramfs":
        "[1] reiniciando no initramfs em RAM",
    "\n=== step 2: restore stock ===":
        "\n=== passo 2: restaurar o firmware de fábrica ===",
    "[?] erase OpenWrt and write stock? [type RESTORE] ":
        "[?] apagar o OpenWrt e gravar o firmware de fábrica? [digite RESTORE] ",
    "\n=== step 3: wait for stock ===":
        "\n=== passo 3: aguardar o firmware de fábrica ===",
    "[+] factory defaults -- the setup wizard is waiting":
        "[+] padrões de fábrica -- o assistente de configuração está aguardando",
    "[!] stock did not answer on {host} within 7 minutes. It may still be booting; check that this host has an address on that subnet.":
        "[!] o firmware de fábrica não respondeu em {host} dentro de 7 minutos. Ele ainda pode estar inicializando; verifique se este computador tem um endereço nessa sub-rede.",
}

# Read-only SSH key verification.
ZH_CN.update({
    "Key verification failed: {error}. Check the key/ssh-agent and trust this exact address with a manual SSH login first.": "密钥验证失败：{error}。请检查密钥或 ssh-agent，并先手动 SSH 登录同一地址以确认并信任主机密钥。",
    "--verify-ssh-key and --verify-host require --verify-only": "--verify-ssh-key 和 --verify-host 必须与 --verify-only 一起使用",
    "--verify-host requires --verify-ssh-key": "--verify-host 必须与 --verify-ssh-key 一起使用",
    "Key verification requires OpenSSH (ssh)": "密钥验证需要安装 OpenSSH（ssh）",
    "SSH private key file does not exist": "SSH 私钥文件不存在",
    "--verify-host must be a numeric IP address": "--verify-host 必须是数字形式的 IP 地址",
})
RU.update({
    "Key verification failed: {error}. Check the key/ssh-agent and trust this exact address with a manual SSH login first.": "Ошибка проверки с ключом: {error}. Проверьте ключ/ssh-agent и сначала подтвердите ключ хоста, подключившись вручную по SSH к этому же адресу.",
    "--verify-ssh-key and --verify-host require --verify-only": "--verify-ssh-key и --verify-host требуют --verify-only",
    "--verify-host requires --verify-ssh-key": "--verify-host требует --verify-ssh-key",
    "Key verification requires OpenSSH (ssh)": "Для проверки с ключом нужен OpenSSH (ssh)",
    "SSH private key file does not exist": "Файл закрытого ключа SSH не существует",
    "--verify-host must be a numeric IP address": "--verify-host должен быть числовым IP-адресом",
})
PT_BR.update({
    "Key verification failed: {error}. Check the key/ssh-agent and trust this exact address with a manual SSH login first.": "Falha na verificação com chave: {error}. Confira a chave/ssh-agent e confirme primeiro a chave do host conectando-se manualmente por SSH a esse mesmo endereço.",
    "--verify-ssh-key and --verify-host require --verify-only": "--verify-ssh-key e --verify-host exigem --verify-only",
    "--verify-host requires --verify-ssh-key": "--verify-host exige --verify-ssh-key",
    "Key verification requires OpenSSH (ssh)": "A verificação com chave exige OpenSSH (ssh)",
    "SSH private key file does not exist": "O arquivo da chave privada SSH não existe",
    "--verify-host must be a numeric IP address": "--verify-host deve ser um endereço IP numérico",
})

# updater.py --ssh-key-auth, the carry-over of extra files, and the package
# reinstall.
ZH_CN.update({
    'SSH key login to {host} failed: {output}': '使用 SSH 密钥登录 {host} 失败：{output}',
    'scp with the SSH key to {host} failed: {output}': '使用 SSH 密钥对 {host} 执行 scp 失败：{output}',
    "the SSH host key of {host} is not the one known_hosts holds for it, or it has none there. Nothing was sent to it. If an update or install was interrupted, the router may be in the RAM installer, which has a host key of its own: do not add that key, run this again without SSH key login. Otherwise, check the router's key yourself and update ~/.ssh/known_hosts only then; this tool does not relax the check.\n{output}": '{host} 的 SSH 主机密钥与 known_hosts 中记录的不一致，或 known_hosts 中没有它的记录。没有向它发送任何内容。如果之前的更新或安装被中断，路由器可能处于 RAM 安装器中，它有自己的主机密钥：不要添加那个密钥，请不用 SSH 密钥登录再运行一次。否则，请先自行核对路由器的主机密钥，确认后再更新 ~/.ssh/known_hosts；本工具不会放宽这项检查。\n{output}',
    '[3] verification commands failed on {host}: {error}': '[3] {host} 上的验证命令失败：{error}',
    '--verify-ssh-alias requires --verify-host': '--verify-ssh-alias 必须与 --verify-host 一起使用',
    'the SSH alias {alias} connects to {resolved}, not {host}; nothing was contacted': 'SSH 别名 {alias} 连接的是 {resolved}，而不是 {host}；尚未连接任何设备',
    'the SSH alias {alias} logs in as {user}, not root; set "User root" for it in ~/.ssh/config': 'SSH 别名 {alias} 以 {user} 而不是 root 登录；请在 ~/.ssh/config 中为它设置 "User root"',
    '{host} did not accept the SSH key login, and no password is tried in its place: {output}\nCheck the key, ssh-agent (ssh-add) and ~/.ssh/config with a manual `ssh` first. If the router is in the RAM installer (an interrupted update), it has no copy of your key: run this again without --ssh-key-auth. Nothing was written.': '{host} 未接受 SSH 密钥登录，也不会改用任何密码重试：{output}\n请先手动运行 `ssh`，检查密钥、ssh-agent（ssh-add）和 ~/.ssh/config。如果路由器处于 RAM 安装器中（更新曾被中断），其中没有你的公钥：请去掉 --ssh-key-auth 后重新运行。未写入任何内容。',
    '--ssh-identity and --ssh-alias require --ssh-key-auth': '--ssh-identity 和 --ssh-alias 必须与 --ssh-key-auth 一起使用',
    '--ssh-key-auth sends no password; drop --root-password': '--ssh-key-auth 不会发送任何密码；请去掉 --root-password',
    "--ssh-key-auth needs the settings kept: a clean install removes your public key and the router's host key, so the update could not be verified by key": '--ssh-key-auth 要求保留设置：全新安装会删除你的公钥和路由器的主机密钥，更新后就无法用密钥验证',
    "--ssh-alias requires --host with the router's numeric address, which the network checks use": '--ssh-alias 需要用 --host 给出路由器的数字地址，网络检查使用的是这个地址',
    "[*] SSH: key login to the installed system through OpenSSH and ~/.ssh/config; no password is sent, and known_hosts must already hold the router's host key": '[*] SSH：通过 OpenSSH 和 ~/.ssh/config 用密钥登录已安装的系统；不发送任何密码，known_hosts 中必须已有路由器的主机密钥',
    '[!] this update goes through the RAM installer, which has no copy of your SSH key. It is reached with its empty root password, as without --ssh-key-auth (so sshpass is needed); the final check logs in to {host} by key again.': '[!] 本次更新要经过 RAM 安装器，其中没有你的 SSH 公钥。连接它时使用其空的 root 密码，与不加 --ssh-key-auth 时相同（因此需要 sshpass）；最终验证会再次用密钥登录 {host}。',
    '[!] the update is complete, but reinstalling packages stopped: {error}. Run this again with --reinstall-only to retry.': '[!] 更新已完成，但重装软件包中途停止：{error}。可加上 --reinstall-only 重新运行以重试。',
    '--extra-keep needs an absolute path on the router, got {path!r}': '--extra-keep 需要路由器上的绝对路径，收到的是 {path!r}',
    "[c] /overlay/upper ({size} KiB) does not fit in the router's free memory; overlay-full.tar.gz was not saved": '[c] /overlay/upper（{size} KiB）超出路由器的可用内存；未保存 overlay-full.tar.gz',
    '\n=== carry-over: extra files and packages ===': '\n=== 迁移：额外文件和软件包 ===',
    '[c] files no package owns and sysupgrade would not keep:': '[c] 不属于任何软件包、sysupgrade 也不会保留的文件：',
    'add these {count} path(s) to {conf}?': '将这 {count} 个路径追加到 {conf}？',
    '[c] no extra files found that sysupgrade would drop': '[c] 没有发现 sysupgrade 会丢弃的额外文件',
    '[c] not added: the update goes on without these files': '[c] 未追加：更新将在不保留这些文件的情况下继续',
    '[c] the carry-over changes were undone: {conf} and {world} are as they were': '[c] 已撤销迁移所做的修改：{conf} 和 {world} 已恢复原样',
    '[!] could not undo the carry-over changes on {host} ({error}). By hand, remove these lines from {conf}: {lines}; and remove {world} if you did not have one before.': '[!] 无法撤销在 {host} 上所做的迁移修改（{error}）。请手动从 {conf} 中删除这些行：{lines}；如果你之前没有 {world}，也请删除它。',
    '[c] {count} package(s) to reinstall after the update -> {path}': '[c] 更新后需重装 {count} 个软件包 -> {path}',
    '[p] waiting up to {seconds}s for the router to reach its package repositories': '[p] 最多等待 {seconds} 秒，直到路由器能访问软件包仓库',
    '\n=== packages: reinstall what the update dropped ===': '\n=== 软件包：重装更新后缺失的软件包 ===',
    '[p] no package list on the router or in {outdir}; nothing to reinstall': '[p] 路由器上和 {outdir} 中都没有软件包列表；无需重装',
    '[p] the package list is empty; nothing to reinstall': '[p] 软件包列表为空；无需重装',
    '[p] kernel modules (kmod-*) install only from the repository built for the running firmware; a kmod that it lacks fails and is listed below': '[p] 内核模块（kmod-*）只能从当前固件对应的仓库安装；仓库里没有的 kmod 会安装失败，并列在下方',
    '[p] packages: {installed} installed, {present} already present, {skipped} skipped, {failed} failed -> {path}': '[p] 软件包：已安装 {installed} 个，原已存在 {present} 个，跳过 {skipped} 个，失败 {failed} 个 -> {path}',
    '[p] not reinstalled, do it by hand: {name} ({why})': '[p] 未自动重装，请手动处理：{name}（{why}）',
    '[p] failed: {name} ({why})': '[p] 失败：{name}（{why}）',
    '[p] a kmod that failed is not in the repository of the running firmware; do not install one built for another kernel': '[p] 安装失败的 kmod 不在当前固件对应的仓库中；不要安装为其他内核编译的版本',
})
RU.update({
    'SSH key login to {host} failed: {output}': 'Не удалось войти на {host} по SSH-ключу: {output}',
    'scp with the SSH key to {host} failed: {output}': 'Ошибка scp с SSH-ключом для {host}: {output}',
    "the SSH host key of {host} is not the one known_hosts holds for it, or it has none there. Nothing was sent to it. If an update or install was interrupted, the router may be in the RAM installer, which has a host key of its own: do not add that key, run this again without SSH key login. Otherwise, check the router's key yourself and update ~/.ssh/known_hosts only then; this tool does not relax the check.\n{output}": 'Ключ хоста SSH {host} не совпадает с записанным в known_hosts, или записи нет. Ему ничего не отправлено. Если обновление или установка были прерваны, роутер может быть в RAM-установщике, у которого свой ключ хоста: не добавляйте этот ключ, запустите программу снова без входа по SSH-ключу. Иначе сами проверьте ключ роутера и только потом обновите ~/.ssh/known_hosts; эта программа не ослабляет проверку.\n{output}',
    '[3] verification commands failed on {host}: {error}': '[3] Команды проверки на {host} завершились с ошибкой: {error}',
    '--verify-ssh-alias requires --verify-host': '--verify-ssh-alias требует --verify-host',
    'the SSH alias {alias} connects to {resolved}, not {host}; nothing was contacted': 'SSH-псевдоним {alias} подключается к {resolved}, а не к {host}; ни к чему не подключались',
    'the SSH alias {alias} logs in as {user}, not root; set "User root" for it in ~/.ssh/config': 'SSH-псевдоним {alias} входит как {user}, а не root; укажите для него "User root" в ~/.ssh/config',
    '{host} did not accept the SSH key login, and no password is tried in its place: {output}\nCheck the key, ssh-agent (ssh-add) and ~/.ssh/config with a manual `ssh` first. If the router is in the RAM installer (an interrupted update), it has no copy of your key: run this again without --ssh-key-auth. Nothing was written.': '{host} не принял вход по SSH-ключу, и пароль вместо него не пробуется: {output}\nСначала проверьте ключ, ssh-agent (ssh-add) и ~/.ssh/config, подключившись вручную через `ssh`. Если роутер в RAM-установщике (прерванное обновление), вашего ключа там нет: запустите снова без --ssh-key-auth. Ничего не записано.',
    '--ssh-identity and --ssh-alias require --ssh-key-auth': '--ssh-identity и --ssh-alias требуют --ssh-key-auth',
    '--ssh-key-auth sends no password; drop --root-password': '--ssh-key-auth не отправляет пароль; уберите --root-password',
    "--ssh-key-auth needs the settings kept: a clean install removes your public key and the router's host key, so the update could not be verified by key": '--ssh-key-auth требует сохранить настройки: чистая установка удалит ваш открытый ключ и ключ хоста роутера, и обновление нельзя будет проверить по ключу',
    "--ssh-alias requires --host with the router's numeric address, which the network checks use": '--ssh-alias требует --host с числовым адресом роутера, который используют сетевые проверки',
    "[*] SSH: key login to the installed system through OpenSSH and ~/.ssh/config; no password is sent, and known_hosts must already hold the router's host key": '[*] SSH: вход в установленную систему по ключу через OpenSSH и ~/.ssh/config; пароль не отправляется, ключ хоста роутера уже должен быть в known_hosts',
    '[!] this update goes through the RAM installer, which has no copy of your SSH key. It is reached with its empty root password, as without --ssh-key-auth (so sshpass is needed); the final check logs in to {host} by key again.': '[!] Это обновление идёт через RAM-установщик, в котором нет вашего SSH-ключа. К нему подключаются с его пустым паролем root, как без --ssh-key-auth (поэтому нужен sshpass); итоговая проверка снова входит на {host} по ключу.',
    '[!] the update is complete, but reinstalling packages stopped: {error}. Run this again with --reinstall-only to retry.': '[!] Обновление завершено, но переустановка пакетов остановилась: {error}. Чтобы повторить, запустите снова с --reinstall-only.',
    '--extra-keep needs an absolute path on the router, got {path!r}': '--extra-keep требует абсолютный путь на роутере, получено {path!r}',
    "[c] /overlay/upper ({size} KiB) does not fit in the router's free memory; overlay-full.tar.gz was not saved": '[c] /overlay/upper ({size} КиБ) не помещается в свободную память роутера; overlay-full.tar.gz не сохранён',
    '\n=== carry-over: extra files and packages ===': '\n=== перенос: дополнительные файлы и пакеты ===',
    '[c] files no package owns and sysupgrade would not keep:': '[c] файлы, не принадлежащие ни одному пакету и не сохраняемые sysupgrade:',
    'add these {count} path(s) to {conf}?': 'Добавить эти пути ({count}) в {conf}?',
    '[c] no extra files found that sysupgrade would drop': '[c] дополнительных файлов, которые sysupgrade отбросил бы, не найдено',
    '[c] not added: the update goes on without these files': '[c] не добавлено: обновление продолжится без этих файлов',
    '[c] the carry-over changes were undone: {conf} and {world} are as they were': '[c] изменения переноса отменены: {conf} и {world} такие же, как были',
    '[!] could not undo the carry-over changes on {host} ({error}). By hand, remove these lines from {conf}: {lines}; and remove {world} if you did not have one before.': '[!] не удалось отменить изменения переноса на {host} ({error}). Вручную удалите из {conf} эти строки: {lines}; и удалите {world}, если его раньше не было.',
    '[c] {count} package(s) to reinstall after the update -> {path}': '[c] пакетов для переустановки после обновления: {count} -> {path}',
    '[p] waiting up to {seconds}s for the router to reach its package repositories': '[p] ожидание до {seconds} с, пока роутер не получит доступ к репозиториям пакетов',
    '\n=== packages: reinstall what the update dropped ===': '\n=== пакеты: переустановка того, что убрало обновление ===',
    '[p] no package list on the router or in {outdir}; nothing to reinstall': '[p] списка пакетов нет ни на роутере, ни в {outdir}; переустанавливать нечего',
    '[p] the package list is empty; nothing to reinstall': '[p] список пакетов пуст; переустанавливать нечего',
    '[p] kernel modules (kmod-*) install only from the repository built for the running firmware; a kmod that it lacks fails and is listed below': '[p] модули ядра (kmod-*) ставятся только из репозитория, собранного для работающей прошивки; kmod, которого там нет, не установится и будет указан ниже',
    '[p] packages: {installed} installed, {present} already present, {skipped} skipped, {failed} failed -> {path}': '[p] пакеты: установлено {installed}, уже были {present}, пропущено {skipped}, с ошибкой {failed} -> {path}',
    '[p] not reinstalled, do it by hand: {name} ({why})': '[p] не переустановлен, сделайте вручную: {name} ({why})',
    '[p] failed: {name} ({why})': '[p] ошибка: {name} ({why})',
    '[p] a kmod that failed is not in the repository of the running firmware; do not install one built for another kernel': '[p] не установившегося kmod нет в репозитории работающей прошивки; не ставьте сборку для другого ядра',
})
PT_BR.update({
    'SSH key login to {host} failed: {output}': 'Falha no login com chave SSH em {host}: {output}',
    'scp with the SSH key to {host} failed: {output}': 'Falha no scp com chave SSH para {host}: {output}',
    "the SSH host key of {host} is not the one known_hosts holds for it, or it has none there. Nothing was sent to it. If an update or install was interrupted, the router may be in the RAM installer, which has a host key of its own: do not add that key, run this again without SSH key login. Otherwise, check the router's key yourself and update ~/.ssh/known_hosts only then; this tool does not relax the check.\n{output}": 'A chave de host SSH de {host} não é a que o known_hosts tem para ele, ou não há nenhuma lá. Nada foi enviado a ele. Se uma atualização ou instalação foi interrompida, o roteador pode estar no instalador em RAM, que tem uma chave de host própria: não adicione essa chave; rode de novo sem login por chave SSH. Caso contrário, confira você mesmo a chave do roteador e só então atualize ~/.ssh/known_hosts; esta ferramenta não relaxa a verificação.\n{output}',
    '[3] verification commands failed on {host}: {error}': '[3] Os comandos de verificação falharam em {host}: {error}',
    '--verify-ssh-alias requires --verify-host': '--verify-ssh-alias exige --verify-host',
    'the SSH alias {alias} connects to {resolved}, not {host}; nothing was contacted': 'O alias SSH {alias} conecta a {resolved}, não a {host}; nada foi contatado',
    'the SSH alias {alias} logs in as {user}, not root; set "User root" for it in ~/.ssh/config': 'O alias SSH {alias} entra como {user}, não como root; defina "User root" para ele em ~/.ssh/config',
    '{host} did not accept the SSH key login, and no password is tried in its place: {output}\nCheck the key, ssh-agent (ssh-add) and ~/.ssh/config with a manual `ssh` first. If the router is in the RAM installer (an interrupted update), it has no copy of your key: run this again without --ssh-key-auth. Nothing was written.': '{host} não aceitou o login com chave SSH, e nenhuma senha é tentada no lugar: {output}\nConfira antes a chave, o ssh-agent (ssh-add) e o ~/.ssh/config com um `ssh` manual. Se o roteador estiver no instalador em RAM (uma atualização interrompida), ele não tem cópia da sua chave: execute de novo sem --ssh-key-auth. Nada foi gravado.',
    '--ssh-identity and --ssh-alias require --ssh-key-auth': '--ssh-identity e --ssh-alias exigem --ssh-key-auth',
    '--ssh-key-auth sends no password; drop --root-password': '--ssh-key-auth não envia senha; remova --root-password',
    "--ssh-key-auth needs the settings kept: a clean install removes your public key and the router's host key, so the update could not be verified by key": '--ssh-key-auth exige manter as configurações: uma instalação limpa remove sua chave pública e a chave de host do roteador, e a atualização não poderia ser verificada por chave',
    "--ssh-alias requires --host with the router's numeric address, which the network checks use": '--ssh-alias exige --host com o endereço numérico do roteador, usado pelas verificações de rede',
    "[*] SSH: key login to the installed system through OpenSSH and ~/.ssh/config; no password is sent, and known_hosts must already hold the router's host key": '[*] SSH: login com chave no sistema instalado via OpenSSH e ~/.ssh/config; nenhuma senha é enviada, e o known_hosts já deve ter a chave de host do roteador',
    '[!] this update goes through the RAM installer, which has no copy of your SSH key. It is reached with its empty root password, as without --ssh-key-auth (so sshpass is needed); the final check logs in to {host} by key again.': '[!] Esta atualização passa pelo instalador em RAM, que não tem cópia da sua chave SSH. Ele é acessado com sua senha de root vazia, como sem --ssh-key-auth (por isso o sshpass é necessário); a verificação final entra em {host} com a chave de novo.',
    '[!] the update is complete, but reinstalling packages stopped: {error}. Run this again with --reinstall-only to retry.': '[!] A atualização terminou, mas a reinstalação de pacotes parou: {error}. Execute de novo com --reinstall-only para tentar outra vez.',
    '--extra-keep needs an absolute path on the router, got {path!r}': '--extra-keep exige um caminho absoluto no roteador, recebido {path!r}',
    "[c] /overlay/upper ({size} KiB) does not fit in the router's free memory; overlay-full.tar.gz was not saved": '[c] /overlay/upper ({size} KiB) não cabe na memória livre do roteador; overlay-full.tar.gz não foi salvo',
    '\n=== carry-over: extra files and packages ===': '\n=== transferência: arquivos extras e pacotes ===',
    '[c] files no package owns and sysupgrade would not keep:': '[c] arquivos que nenhum pacote possui e que o sysupgrade não manteria:',
    'add these {count} path(s) to {conf}?': 'adicionar estes {count} caminho(s) a {conf}?',
    '[c] no extra files found that sysupgrade would drop': '[c] nenhum arquivo extra que o sysupgrade descartaria foi encontrado',
    '[c] not added: the update goes on without these files': '[c] não adicionados: a atualização continua sem esses arquivos',
    '[c] the carry-over changes were undone: {conf} and {world} are as they were': '[c] as mudanças da transferência foram desfeitas: {conf} e {world} estão como eram',
    '[!] could not undo the carry-over changes on {host} ({error}). By hand, remove these lines from {conf}: {lines}; and remove {world} if you did not have one before.': '[!] não foi possível desfazer as mudanças da transferência em {host} ({error}). À mão, remova estas linhas de {conf}: {lines}; e remova {world} se você não tinha um antes.',
    '[c] {count} package(s) to reinstall after the update -> {path}': '[c] {count} pacote(s) para reinstalar após a atualização -> {path}',
    '[p] waiting up to {seconds}s for the router to reach its package repositories': '[p] aguardando até {seconds}s até o roteador alcançar seus repositórios de pacotes',
    '\n=== packages: reinstall what the update dropped ===': '\n=== pacotes: reinstalar o que a atualização removeu ===',
    '[p] no package list on the router or in {outdir}; nothing to reinstall': '[p] não há lista de pacotes no roteador nem em {outdir}; nada a reinstalar',
    '[p] the package list is empty; nothing to reinstall': '[p] a lista de pacotes está vazia; nada a reinstalar',
    '[p] kernel modules (kmod-*) install only from the repository built for the running firmware; a kmod that it lacks fails and is listed below': '[p] módulos do kernel (kmod-*) só se instalam do repositório feito para o firmware em execução; um kmod que falte lá falha e é listado abaixo',
    '[p] packages: {installed} installed, {present} already present, {skipped} skipped, {failed} failed -> {path}': '[p] pacotes: {installed} instalados, {present} já presentes, {skipped} ignorados, {failed} com falha -> {path}',
    '[p] not reinstalled, do it by hand: {name} ({why})': '[p] não reinstalado, faça à mão: {name} ({why})',
    '[p] failed: {name} ({why})': '[p] falhou: {name} ({why})',
    '[p] a kmod that failed is not in the repository of the running firmware; do not install one built for another kernel': '[p] um kmod que falhou não está no repositório do firmware em execução; não instale um feito para outro kernel',
})

# updater.py: reviewing configuration files after the update.
ZH_CN.update({
    '\n=== configuration files: new package versions ===': '\n=== 配置文件：软件包带来的新版本 ===',
    '[p] {count} reinstalled package(s) brought a new version of a configuration file you changed; yours is in use': '[p] 有 {count} 个重装的软件包带来了你改过的配置文件的新版本；当前使用的仍是你的版本',
    "[?] {path}: keep your version (k), install the package's new one (n), or decide later (d)? [k/n/d] ": '[?] {path}：保留你的版本（k）、换成软件包的新版本（n），还是稍后决定（d）？[k/n/d] ',
    "[p] kept your {path}; the package's version was removed": '[p] 已保留你的 {path}；软件包的版本已删除',
    "[p] installed the package's {path}; yours is in {old}. Restart the service that reads it, or reboot.": '[p] 已换成软件包的 {path}；你的版本保存在 {old}。请重启使用它的服务，或重启路由器。',
    '[p] left for later: {path} and {new}; the difference is in {diff}': '[p] 留待稍后处理：{path} 和 {new}；差异见 {diff}',
    '\n=== configuration files: image defaults that changed upstream ===': '\n=== 配置文件：上游改动过的镜像默认版本 ===',
    '[r] the new release changed none of the defaults of the files you changed; your versions stay as they are': '[r] 新版本没有改动你改过的那些文件的默认版本；你的版本保持不变',
    '[r] the new release changed the default of {count} file(s) that you changed. Yours were restored and are in use; carry over what you need from the upstream change:': '[r] 新版本改动了 {count} 个你改过的文件的默认版本。你的版本已恢复并在使用中；请从上游的改动里挑出需要的内容加进去：',
    '[r] the new release no longer ships {path}': '[r] 新版本不再包含 {path}',
    '[r] {path}: upstream change in {diff}': '[r] {path}：上游的改动见 {diff}',
})
RU.update({
    '\n=== configuration files: new package versions ===': '\n=== файлы настроек: новые версии из пакетов ===',
    '[p] {count} reinstalled package(s) brought a new version of a configuration file you changed; yours is in use': '[p] переустановленные пакеты ({count}) принесли новую версию изменённого вами файла настроек; используется ваша версия',
    "[?] {path}: keep your version (k), install the package's new one (n), or decide later (d)? [k/n/d] ": '[?] {path}: оставить вашу версию (k), установить новую из пакета (n) или решить позже (d)? [k/n/d] ',
    "[p] kept your {path}; the package's version was removed": '[p] ваш {path} оставлен; версия из пакета удалена',
    "[p] installed the package's {path}; yours is in {old}. Restart the service that reads it, or reboot.": '[p] установлен {path} из пакета; ваша версия сохранена в {old}. Перезапустите службу, которая его читает, или перезагрузите роутер.',
    '[p] left for later: {path} and {new}; the difference is in {diff}': '[p] отложено: {path} и {new}; различия в {diff}',
    '\n=== configuration files: image defaults that changed upstream ===': '\n=== файлы настроек: значения по умолчанию, изменённые в образе ===',
    '[r] the new release changed none of the defaults of the files you changed; your versions stay as they are': '[r] новый выпуск не изменил значения по умолчанию ни одного из изменённых вами файлов; ваши версии остаются как есть',
    '[r] the new release changed the default of {count} file(s) that you changed. Yours were restored and are in use; carry over what you need from the upstream change:': '[r] новый выпуск изменил значения по умолчанию у изменённых вами файлов ({count}). Ваши версии восстановлены и используются; перенесите нужное из изменений образа:',
    '[r] the new release no longer ships {path}': '[r] в новом выпуске больше нет {path}',
    '[r] {path}: upstream change in {diff}': '[r] {path}: изменения образа в {diff}',
})
PT_BR.update({
    '\n=== configuration files: new package versions ===': '\n=== arquivos de configuração: novas versões dos pacotes ===',
    '[p] {count} reinstalled package(s) brought a new version of a configuration file you changed; yours is in use': '[p] {count} pacote(s) reinstalado(s) trouxe(ram) uma nova versão de um arquivo de configuração que você alterou; a sua está em uso',
    "[?] {path}: keep your version (k), install the package's new one (n), or decide later (d)? [k/n/d] ": '[?] {path}: manter a sua versão (k), instalar a nova do pacote (n) ou decidir depois (d)? [k/n/d] ',
    "[p] kept your {path}; the package's version was removed": '[p] o seu {path} foi mantido; a versão do pacote foi removida',
    "[p] installed the package's {path}; yours is in {old}. Restart the service that reads it, or reboot.": '[p] o {path} do pacote foi instalado; o seu está em {old}. Reinicie o serviço que o lê, ou reinicie o roteador.',
    '[p] left for later: {path} and {new}; the difference is in {diff}': '[p] deixado para depois: {path} e {new}; a diferença está em {diff}',
    '\n=== configuration files: image defaults that changed upstream ===': '\n=== arquivos de configuração: padrões da imagem alterados pelo upstream ===',
    '[r] the new release changed none of the defaults of the files you changed; your versions stay as they are': '[r] a nova versão não alterou o padrão de nenhum dos arquivos que você mudou; as suas versões ficam como estão',
    '[r] the new release changed the default of {count} file(s) that you changed. Yours were restored and are in use; carry over what you need from the upstream change:': '[r] a nova versão alterou o padrão de {count} arquivo(s) que você mudou. Os seus foram restaurados e estão em uso; leve deles o que precisar da mudança do upstream:',
    '[r] the new release no longer ships {path}': '[r] a nova versão não inclui mais {path}',
    '[r] {path}: upstream change in {diff}': '[r] {path}: mudança do upstream em {diff}',
})

# updater.py: the temporary resolver while packages reinstall.
ZH_CN.update({
    "[c] dnsmasq forwards DNS to a resolver on the router itself ({forwarders}). If a package you installed provides it, the router cannot resolve names after the update until that package is reinstalled; the reinstall then uses the WAN's DNS servers for the router itself.": '[c] dnsmasq 把 DNS 转发给路由器本机上的解析器（{forwarders}）。如果它来自你自己安装的软件包，更新后在该软件包重装之前，路由器将无法解析域名；届时重装步骤会让路由器自身临时使用 WAN 口的 DNS 服务器。',
    "[p] the router cannot resolve {name}, and none of the WAN's DNS servers can either. Fix its DNS, then run this again with --reinstall-only.": '[p] 路由器无法解析 {name}，WAN 口的 DNS 服务器也都无法解析。请先修复路由器的 DNS，然后加上 --reinstall-only 重新运行。',
    "[p] the router cannot resolve {name}. The restored dnsmasq config forwards to {forwarders}, probably a resolver from a package that is not reinstalled yet. Until the reinstall ends, the router itself uses the WAN's DNS servers ({servers}); dnsmasq and the LAN are unchanged.": '[p] 路由器无法解析 {name}。恢复的 dnsmasq 配置把 DNS 转发给 {forwarders}，它很可能来自一个尚未重装的软件包。在重装结束之前，路由器自身临时使用 WAN 口的 DNS 服务器（{servers}）；dnsmasq 和局域网的配置不变。',
    "[p] the router's own resolver is back to its configuration": '[p] 路由器自身的 DNS 解析设置已恢复原样',
    '[!] could not restore {conf} on the router ({error}); restart dnsmasq there (service dnsmasq restart) to rebuild it': '[!] 无法恢复路由器上的 {conf}（{error}）；请在路由器上重启 dnsmasq（service dnsmasq restart）以重新生成它',
})
RU.update({
    "[c] dnsmasq forwards DNS to a resolver on the router itself ({forwarders}). If a package you installed provides it, the router cannot resolve names after the update until that package is reinstalled; the reinstall then uses the WAN's DNS servers for the router itself.": '[c] dnsmasq пересылает DNS резолверу на самом роутере ({forwarders}). Если его предоставляет установленный вами пакет, после обновления роутер не сможет разрешать имена, пока пакет не переустановлен; тогда на время переустановки сам роутер будет использовать DNS-серверы WAN.',
    "[p] the router cannot resolve {name}, and none of the WAN's DNS servers can either. Fix its DNS, then run this again with --reinstall-only.": '[p] роутер не может разрешить {name}, и ни один DNS-сервер WAN тоже не может. Исправьте DNS роутера и запустите снова с --reinstall-only.',
    "[p] the router cannot resolve {name}. The restored dnsmasq config forwards to {forwarders}, probably a resolver from a package that is not reinstalled yet. Until the reinstall ends, the router itself uses the WAN's DNS servers ({servers}); dnsmasq and the LAN are unchanged.": '[p] роутер не может разрешить {name}. Восстановленная конфигурация dnsmasq пересылает DNS на {forwarders}, вероятно, резолвер из ещё не переустановленного пакета. До конца переустановки сам роутер использует DNS-серверы WAN ({servers}); dnsmasq и LAN не меняются.',
    "[p] the router's own resolver is back to its configuration": '[p] собственный резолвер роутера возвращён к его настройкам',
    '[!] could not restore {conf} on the router ({error}); restart dnsmasq there (service dnsmasq restart) to rebuild it': '[!] не удалось восстановить {conf} на роутере ({error}); перезапустите там dnsmasq (service dnsmasq restart), чтобы пересоздать его',
})
PT_BR.update({
    "[c] dnsmasq forwards DNS to a resolver on the router itself ({forwarders}). If a package you installed provides it, the router cannot resolve names after the update until that package is reinstalled; the reinstall then uses the WAN's DNS servers for the router itself.": '[c] O dnsmasq encaminha o DNS para um resolvedor no próprio roteador ({forwarders}). Se ele vier de um pacote que você instalou, após a atualização o roteador não conseguirá resolver nomes até esse pacote ser reinstalado; a reinstalação então usa os servidores DNS da WAN para o próprio roteador.',
    "[p] the router cannot resolve {name}, and none of the WAN's DNS servers can either. Fix its DNS, then run this again with --reinstall-only.": '[p] o roteador não consegue resolver {name}, e nenhum servidor DNS da WAN consegue. Corrija o DNS do roteador e execute de novo com --reinstall-only.',
    "[p] the router cannot resolve {name}. The restored dnsmasq config forwards to {forwarders}, probably a resolver from a package that is not reinstalled yet. Until the reinstall ends, the router itself uses the WAN's DNS servers ({servers}); dnsmasq and the LAN are unchanged.": '[p] o roteador não consegue resolver {name}. A configuração restaurada do dnsmasq encaminha para {forwarders}, provavelmente um resolvedor de um pacote ainda não reinstalado. Até o fim da reinstalação, o próprio roteador usa os servidores DNS da WAN ({servers}); o dnsmasq e a LAN não mudam.',
    "[p] the router's own resolver is back to its configuration": '[p] o resolvedor do próprio roteador voltou à sua configuração',
    '[!] could not restore {conf} on the router ({error}); restart dnsmasq there (service dnsmasq restart) to rebuild it': '[!] não foi possível restaurar {conf} no roteador ({error}); reinicie o dnsmasq lá (service dnsmasq restart) para recriá-lo',
})

CATALOGUES = {"ru": RU, "zh-CN": ZH_CN, "pt-BR": PT_BR}

# Endonyms: what each language calls itself, used by the wizard's switch and
# useful when a locale reports something the operator did not expect.
NATIVE_NAMES = {"en": "English", "ru": "Русский", "zh-CN": "中文", "pt-BR": "Português"}


def _canonical(value):
    """The catalogue language for a locale-like tag, or None if unsupported.

    Accepts what an operator is likely to type or what the environment holds:
    `zh_CN.UTF-8`, `zh-Hans`, `pt_br`, `ru_RU`. A missing region picks the only
    catalogue we ship for that language.
    """
    if not value:
        return None
    tag = str(value).strip().replace("_", "-").lower()
    primary = tag.split("-")[0].split(".")[0]
    if primary in ("zh", "cn"):
        # Traditional Chinese gets Simplified text: there is one Chinese
        # catalogue, and a variant tag does not say which script is wanted.
        return "zh-CN"
    if primary == "pt":
        return "pt-BR"
    if primary == "ru":
        return "ru"
    if primary == "en":
        return "en"
    return None


def _system_language():
    """The operator's language from their environment, else English."""
    if sys.platform == "win32":
        try:
            import ctypes
            # Primary language of the UI language: 0x19 Russian, 0x04 Chinese,
            # 0x16 Portuguese.
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return {0x19: "ru", 0x04: "zh-CN", 0x16: "pt-BR"}.get(lang_id, "en")
        except (AttributeError, ImportError, OSError):
            return "en"
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value:
            # The first variable that is set decides, as the C library does: a
            # deliberate LC_ALL=C must not fall through to a Russian LANG.
            return _canonical(value) or "en"
    return "en"


def set_language(language):
    """Select a language; 'auto' honours XIAOMI_INSTALL_LANG, then the system."""
    global _language
    if language == "auto":
        language = (_canonical(os.environ.get("XIAOMI_INSTALL_LANG"))
                    or _system_language())
    else:
        language = _canonical(language) or language
    if language != "en" and language not in CATALOGUES:
        raise ValueError(f"unsupported language {language!r}")
    _language = language


def tolerate_console_encoding():
    """Never let a log line kill a run halfway through a flash.

    Python talks UTF-16 to a real Windows console, but a pipe (`| Tee-Object`)
    or a legacy locale gets a code page that cannot encode everything a router
    or a translated message may contain.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def t(template, **values):
    """`template` in the selected language, formatted with `values`."""
    catalogue = CATALOGUES.get(_language)
    text = catalogue.get(template, template) if catalogue else template
    return text.format(**values) if values else text
