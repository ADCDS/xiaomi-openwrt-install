"""Translations for the messages an operator has to read or act on.

Deliberately small: prompts, the instructions to switch networks or leave the
power alone, stage banners, and the failures people actually hit. Low-level
diagnostics stay in English, since that is what gets pasted into bug reports.

The English template is the catalogue key, so anything missing falls back to
English rather than failing, and an unconfigured process (a library caller, the
test suites) always gets English. What must be typed stays literal in every
language: a confirmation is still YES or RESTORE, in Latin letters.

Only the languages that have a catalogue appear in LANGUAGES beyond "auto" and
"en"; "cn" and "pt" are accepted on the command line as aliases of "zh-CN" and
"pt-BR". test_i18n.py keeps each catalogue honest: every key must still occur
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
    "{host} refused the root password offered. Pass the current one with --root-password. A router set to SSH keys only (PasswordAuth off) is not supported: nothing was written.":
        "{host} отклонил указанный пароль root. Укажите текущий через --root-password. Роутеры с входом только по ключам (PasswordAuth off) не поддерживаются: ничего не записано.",
    "only {available} KiB of memory is available and the RAM image needs {needed} KiB. Free some first, for example by stopping services you can spare, then run this again.":
        "доступно только {available} КиБ памяти, а образу RAM нужно {needed} КиБ. Освободите память, например остановив необязательные службы, и запустите снова.",
    "a clean install leaves Wi-Fi disabled, so it cannot be verified over Wi-Fi. Connect a cable and pass --verify-interface, or keep the settings.":
        "после чистой установки Wi-Fi выключен, поэтому проверить её по Wi-Fi нельзя. Подключите кабель и укажите --verify-interface либо сохраните настройки.",
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
        "[2] 仅 Wi-Fi：当原厂网络消失后，请连接到 {ssid}（密码 {key}）",
    "[2] rebooting -- stock is still intact in {slot}; if the pivot does not come up, the bootloader falls back to it":
        "[2] 重启中 —— 原厂固件仍完好保存在 {slot}；如果切换后无法启动，引导程序会回退到它",
    "[3] after the RAM installer network disappears, join permanent SSID {ssid!r}; final verification will wait on {iface} (the password is not printed)":
        "[3] RAM 安装系统的网络消失后，请连接常驻 SSID {ssid}；最终验证会在 {iface} 上等待（不会打印密码）",
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
        "[*] 在 {iface} 上连接常驻 SSID {ssid}；验证会继续等待，且不会打印密码",
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
    "{host} refused the root password offered. Pass the current one with --root-password. A router set to SSH keys only (PasswordAuth off) is not supported: nothing was written.":
        "{host} 拒绝了所给的 root 密码。请用 --root-password 传入当前密码。不支持仅允许 SSH 密钥登录（PasswordAuth off）的路由器：未写入任何内容。",
    "only {available} KiB of memory is available and the RAM image needs {needed} KiB. Free some first, for example by stopping services you can spare, then run this again.":
        "当前仅剩 {available} KiB 内存，而 RAM 镜像需要 {needed} KiB。请先释放一些内存（例如停止可以暂时停用的服务），然后重新运行。",
    "a clean install leaves Wi-Fi disabled, so it cannot be verified over Wi-Fi. Connect a cable and pass --verify-interface, or keep the settings.":
        "全新安装会关闭 Wi-Fi，因此无法通过 Wi-Fi 验证。请连接网线并指定 --verify-interface，或保留原有设置。",
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
        "[2] 当此网络消失后，请连接到 {ssid}（密码 {key}）；RAM 系统的 root 账户没有密码，因此请保持该链路隔离",
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
        "[1] 当已安装系统的网络消失后，请连接到 {ssid}（密码 {key}）；该临时 RAM 系统的 root 账户没有密码，因此请保持该链路隔离",
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
        "[2] Somente Wi-Fi: quando a rede de fábrica desaparecer, conecte-se a {ssid} com a chave {key}",
    "[2] rebooting -- stock is still intact in {slot}; if the pivot does not come up, the bootloader falls back to it":
        "[2] reiniciando -- o firmware de fábrica continua intacto em {slot}; se a transição não subir, o bootloader volta para ele",
    "[3] after the RAM installer network disappears, join permanent SSID {ssid!r}; final verification will wait on {iface} (the password is not printed)":
        "[3] depois que a rede do instalador em RAM desaparecer, conecte-se ao SSID permanente {ssid}; a verificação final aguardará em {iface} (a senha não é exibida)",
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
        "[*] conecte-se ao SSID permanente {ssid} em {iface}; a verificação aguardará sem exibir a senha",
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
    "{host} refused the root password offered. Pass the current one with --root-password. A router set to SSH keys only (PasswordAuth off) is not supported: nothing was written.":
        "{host} recusou a senha de root informada. Passe a atual com --root-password. Um roteador configurado para apenas chaves SSH (PasswordAuth off) não é suportado: nada foi gravado.",
    "only {available} KiB of memory is available and the RAM image needs {needed} KiB. Free some first, for example by stopping services you can spare, then run this again.":
        "só há {available} KiB de memória disponível e a imagem em RAM precisa de {needed} KiB. Libere um pouco primeiro, por exemplo parando serviços dispensáveis, e execute isto de novo.",
    "a clean install leaves Wi-Fi disabled, so it cannot be verified over Wi-Fi. Connect a cable and pass --verify-interface, or keep the settings.":
        "uma instalação limpa deixa o Wi-Fi desativado, então não é possível verificá-la por Wi-Fi. Conecte um cabo e passe --verify-interface, ou mantenha as configurações.",
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
        "[2] quando esta rede desaparecer, conecte-se a {ssid} com a chave {key}; o sistema em RAM tem root sem senha, então mantenha o link isolado",
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
        "[1] quando a rede do sistema instalado desaparecer, conecte-se a {ssid} com a chave {key}; este sistema temporário em RAM tem root sem senha, então mantenha o link isolado",
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
        # Traditional Chinese would get Simplified text; say so in the README
        # rather than inventing a catalogue we do not have.
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
