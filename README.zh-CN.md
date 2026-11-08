# xiaomi-openwrt-install

[English](README.md) · [Русский](README.ru.md) · **简体中文** · [Português (Brasil)](README.pt-BR.md) · [技术文档（英文）](docs/technical.md)

在受支持的 Xiaomi 路由器上安装 OpenWrt，无需 UART，也无需焊接；同时可以把已安装 OpenWrt 的路由器恢复到经批准的原厂固件。可在 Linux 以及 Windows 10/11 上运行，无需 WSL。

安装器通过[已公开的 `cab_meshd` 漏洞利用链](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure#combined-v1--v2-capuci-exploit)获取 root，校验主板、NAND 和分区布局，下载适用于你的设备的 OpenWrt 镜像，引导临时的 RAM 系统，然后写入永久固件。

> 请阅读 [NOTICE](NOTICE)。只在你自己的路由器上使用本安装器。

安装器的提示和说明会跟随系统语言显示，共支持四种语言：英语、俄语、简体中文和巴西葡萄牙语。也可以用 `--lang en`、`--lang ru`、`--lang zh-CN` 或 `--lang pt-BR` 显式指定。底层诊断信息保持英文：这样更方便把它附在错误报告里。

## 已验证情况

| 场景 | 状态 |
|---|---|
| Linux，原厂固件 2.0.28 和 2.0.12 | 主要场景，已在真实路由器上验证 |
| Windows 10、Wi-Fi、原厂固件 2.0.28、发布版 v1.14 `standard` | 已于 2026 年 10 月 11 日在真实路由器上验证（[alexsys87](https://github.com/alexsys87/xiaomi-openwrt-install)），基于较早版本的代码 |
| 虚拟机中的 Windows 11 25H2、网线、原厂固件 2.0.28、发布版 v2.1.2、设置了 root 密码 | 已于 2026 年 11 月 7 日在此版本代码上于真实路由器验证，包括继续被中断的运行 |
| `updater.py`，从 Linux 恢复已保存的配置 | 于 2026 年 11 月 7 日运行过一次。所有写入阶段均成功；最终密码登录未能完成，因为恢复的配置关闭了密码登录 |
| 从 Windows 运行 `updater.py`，以及更新仍允许密码登录的路由器 | 自动化测试；**尚未在真实路由器上验证** |
| Windows、Wi-Fi，此版本代码 | 自动化测试；**尚未在真实路由器上验证** |
| Windows，恢复原厂固件（`revert.py`） | 自动化测试；**尚未在真实路由器上验证** |

如果从 Windows 安装，请在手边准备一台 Linux 电脑或一个 USB-UART 适配器，以备出现故障。请把结果反馈到 [Issues](https://github.com/ADCDS/xiaomi-openwrt-install/issues)，尤其是成功的情况：这有助于更新上表。

仅支持 Xiaomi AX3000T **RD03v2**（Qualcomm IPQ5018）。安装器会拒绝其他型号。请不要把本说明套用到这些型号上。

## 1. 准备工作

- 确保路由器供电稳定。写入过程中不要断电。
- 保存需要的原厂设置：复位会删除配置和密码。
- 隔离连接：不要让无关设备接入原厂网络和临时网络。临时网络的密码是公开的，而且临时 RAM 系统中的 root 没有密码。
- 关闭 VPN，以及地址可能与路由器地址冲突的其他网络。

下载仓库并在其目录中打开终端：

```sh
git clone https://github.com/ADCDS/xiaomi-openwrt-install.git
cd xiaomi-openwrt-install
```

### Linux

需要 Python 3.9 或更新版本：

```sh
sudo apt install sshpass openssl
```

### Windows 10/11

从 [python.org](https://www.python.org/downloads/windows/) 安装 Python 3.9 或更新版本，并包含 `py` 启动器。也可以下载仓库的 ZIP 包来代替 `git clone`。在包含 `install.py` 的目录中打开 PowerShell，并**在有互联网访问时**安装依赖：

```powershell
py -3 -m pip install -r requirements-windows.txt
py -3 install.py --list-interfaces
```

`--list-interfaces` 会显示每个适配器的索引、名称和类型。请在下方的命令中用自己的索引或名称替换 `12`。如果名称中包含西里尔字母或空格，用索引会更简单；适配器变动之后，索引也可能改变。

无需 WSL、`sshpass`、`nmcli`，也无需单独安装 OpenSSL。不要禁用所选适配器上的 IPv6：路由器重启后的重新发现要使用 IPv6 link-local 地址。

在虚拟机中，请将网卡以桥接或 macvtap 方式接入。测试时，对 RTL8153 USB 适配器做 USB 直通会在持续传输时卡死，而安装依赖这种持续传输，缺了它就无法完成。

请以普通用户身份运行安装器；只有设置防火墙规则时才需要管理员权限（见[第 6 节](#回调连接未能建立windows-防火墙)）。在 Windows 中请用 `py -3` 代替 `python3`。在命令提示符（cmd）中可以用相同的参数运行 `install.cmd`；在 PowerShell 中则写作：`.\install.cmd`。

## 2. 镜像与互联网

Xiaomi 原厂 Wi-Fi 网络可能无法访问互联网，因此请**提前**下载并校验镜像。该命令不会访问路由器：

```powershell
py -3 install.py --transport wifi --dry-run
```

在 Linux 中命令相同：`python3 install.py --transport wifi --dry-run`。若通过网线安装，请去掉 `--transport wifi`。

默认会选择带 NSS 硬件加速的 [RD03v2 移植版](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2)的最新发布版。输出中会出现 `selected ... from release vX.Y.Z` 这样一行：请记住这个标签。要在没有互联网的情况下工作，需要显式指定标签并使用已下载的镜像：

```powershell
py -3 install.py --release vX.Y.Z --transport wifi --offline --dry-run
```

不带 NSS 的构建只存在于 v1.x 系列的最新发布版中：`py -3 install.py standard --release v1.14`。

`--root-password 密码` 可在不使用 `--configure` 交互提问的情况下设置新系统的 root 密码；`--attacker 地址` 用于在多网卡主机上自动选择会挑错回调地址时手动指定。

自动化时可以把机密通过 `RD03V2_ROOT_PASSWORD` 和 `RD03V2_WIFI_KEY` 传入，这样它们不会出现在进程参数列表中：变量在启动时被读取并消费，网页向导正是为此使用的。

## 3. 复位与连接

1. 用 Reset 按钮执行硬件复位。
2. 通过网线连接到 **LAN** 口（不是 WAN 口），或连接到形如 `minet_rd03_XXXX` 的开放原厂 Wi-Fi 网络。
3. **不要完成 Xiaomi 的设置向导**，无论是在浏览器中还是在 Mi Home 中。自动打开的页面可以直接关闭。
4. 关闭 VPN 和其他多余的连接。通过 Wi-Fi 安装时，请拔掉网线，并避免电脑切换到家庭网络。
5. 检查地址：应当是 `192.168.31.x`，并且 `http://192.168.31.1` 能打开。此时出现「无 Internet 访问」的提示是正常的。

## 4. 安装

```sh
python3 install.py
```

在 Windows 中，请指定你的适配器：

```powershell
py -3 install.py --interface 12 --configure
```

没有互联网时，请加上第 2 节中的标签：`--release vX.Y.Z --offline`。

安装器会自动判断连接方式；也可以用 `--transport wired` 或 `--transport wifi` 覆盖该判断，接口则通过 `--interface` 指定。每次进行不可逆写入之前，它都会要求确认。确认时必须用**拉丁字母**输入 `YES`。

使用 `--configure` 时会询问以下内容（通过 Wi-Fi 安装时，即使不加该参数这些也是必填项，root 密码除外）：

| 问题 | 应输入的内容 |
|---|---|
| root 密码 | OpenWrt 新的管理员密码。最好不要留空。 |
| Wi-Fi 网络名称（SSID） | 永久网络的名称，例如 `Xiaomi`。 |
| Wi-Fi 密码 | 8–63 个可打印 ASCII 字符：拉丁字母、数字和普通符号。不要使用西里尔字母和表情符号。 |
| 国家/地区代码 | 实际使用所在的国家/地区，例如 `RU`。 |

输入密码时不会回显。请把它们记下来：Wi-Fi 密码与 root 密码互不相关，而且安装器不会输出 Wi-Fi 密码。

如果希望永久 Wi-Fi 保持关闭，请通过网线完成最终校验：

```sh
python3 install.py --transport wifi --interface WIFI_INTERFACE \
    --verify-interface ETHERNET_INTERFACE
```

## 5. 重启：三个不同的 Wi-Fi 网络

通过 Wi-Fi 安装时，不要关闭终端窗口，也不要再启动一个安装器实例。路由器会两次更换网络，并且需要手动连接到新网络。

1. **Xiaomi 原厂网络。** 初始配置完成后路由器会重启，安装器会输出 `waiting up to 300s for tcp/19553`。这属于正常等待。如果电脑断开了连接，请重新连接到原厂网络。
2. **临时 RAM 系统。** 原厂网络消失后，请连接到：

   ```text
   SSID:   OpenWrt-RD03v2-Installer
   Password: rd03v2install
   ```

3. **永久 OpenWrt。** 写入完成后临时网络会消失。请连接到安装时指定的网络。安装器最多等待路由器 600 秒，并校验主板、永久 overlay 和分区大小。

最后应出现 `[+] OpenWrt 已安装到 NAND。`

如果运行在预检之后被中断，安装器会输出用于继续的命令；在此之前的失败不会输出，因为一次性触发器可能尚未生效。无论哪种情况都不要从头开始：漏洞触发器在每次恢复出厂设置之后只会生效一次。断开连接后，路由器最多可能需要 30 分钟才能再次连上。

看到 `flash-started` 或「不要断开电源」之类的提示之后，**不要断开电源，不要复位路由器，也不要重新执行固件写入阶段**。

## 6. 常见错误

本节大部分内容来自一次真实的 Windows 10 安装经历（[alexsys87](https://github.com/alexsys87/xiaomi-openwrt-install)）。

### 接口名称中的西里尔字母与 `Interface ... not found`

在 PowerShell 中，复制名称时可能会出现乱码。请使用 `--list-interfaces` 中的数字索引，例如 `--interface 12`。

### `--interface ...` 与通往 ... 的路由不一致

系统正通过另一个适配器把流量发往路由器，而 `--interface` 并不会改变路由。安装器会在使用一次性触发器之前停下。请连接到目标路由器，关闭 VPN 和与之竞争的网络，检查地址（`192.168.31.x`），然后选择正确的接口。

### `V2 CAP path is unavailable (netmode=4)` 与 `inited=1`

路由器已经配置过。请执行恢复出厂设置，连接到原厂网络，并**不要完成 Xiaomi 设置向导**。如果在正确复位后错误仍然出现，请保存日志并提交 Issue；不要试图绕过检查。

### GitHub：`The handshake operation timed out`

无法通过 HTTPS 获取版本信息。这与路由器无关：原厂网络通常没有互联网。如果镜像已经提前下载好，请使用第 2 节中的 `--release vX.Y.Z --offline`。单独的 `--offline` 并不能代替下载。不需要因为这个错误再做一次复位。

### WPA2 密码不被接受

请切换到英文键盘布局。需要 8–63 个可打印 ASCII 字符。这项检查在开始写入之前完成，因此路由器不会受到影响。

### 回调连接未能建立：Windows 防火墙

症状：提示 `未获得 root 访问权限` 或 `no root shell`。请先检查网络和路由。如果来自路由器的入站连接被拦截，请只针对原厂地址放行 `python.exe` 和所需端口。Windows 可能把原厂网络判定为公用网络，因此只针对专用网络的规则可能不够。请在**以管理员身份运行**的 PowerShell 中执行：

```powershell
$python = py -3 -c "import sys; print(sys.executable)"
New-NetFirewallRule -DisplayName "Xiaomi OpenWrt installer" -Direction Inbound -Action Allow -Program $python -Protocol TCP -LocalPort 8000,4444,4445 -RemoteAddress 192.168.31.1 -Profile Any
```

如果改过地址（`--host`）或端口，请相应调整规则。不要整体关闭防火墙。安装完成后请删除该例外规则：

```powershell
Get-NetFirewallRule -DisplayName "Xiaomi OpenWrt installer" | Remove-NetFirewallRule
```

### 固件写入已启动，但出现 `permanent OpenWrt was not verified`

出现 `sysupgrade 已启动` 的提示，意味着不能重复写入。没有找到路由器本身并不能证明失败。请连接到永久网络，或用网线连接到 LAN 口，并**只执行校验**，同时指定本次运行自己的 `resume.json`（路径由安装器输出）：

```powershell
py -3 install.py --verify-only ".\install-DATE-TIME\resume.json" --verify-interface 12
```

不要使用其他路由器或其他安装过程的清单文件。

### 忘记了永久 Wi-Fi 网络的密码

用网线连接到 **LAN 口**，自动获取地址，然后打开 `http://192.168.1.1`。在 LuCI 中依次进入 **Network → Wireless → Edit → Wireless Security**，设置新密码，然后点击 **Save & Apply**。无需恢复出厂设置。

## 7. 安装之后

- 如果日志中出现 `root password=unset`，请设置管理员密码：**System → Administration**。
- 检查国家/地区设置和 Wi-Fi 安全设置。
- 保存配置：**System → Backup / Flash Firmware → Generate archive**。
- 把 `install-*` 文件夹保存在私密位置：里面有分区备份、`firstboot.tar.gz`，以及包含密码、会话密钥和设备数据的 `resume.json`。在删除敏感信息之前，不要公开这些文件和完整日志。在 Windows 中，权限是从文件夹继承的，而不是通过 `chmod` 设置，因此请选择没有共享访问权限的文件夹。

## 更新已安装的系统

```sh
python3 updater.py --root-password 'YOUR_OPENWRT_PASSWORD'
```

把已安装的 OpenWrt 更新到该移植版的最新版本，并像 `sysupgrade` 一样保留设置。`--release TAG` 选择具体版本，`--no-keep-settings` 执行全新安装，`--dry-run` 只下载并校验镜像。在这块板子上，常规的 `sysupgrade` 会导致变砖，因此更新通过 RAM 安装器进行。写入之前，配置备份会保存到私密的 `update-*` 文件夹。

这里没有 A/B 冗余：出现故障时的保障是 UART。如果运行被中断，请连接到 RAM 安装器，并带上 `--config-backup` 和之前保存的备份重新执行命令。登录采用密码方式，因此仅允许密钥登录（`PasswordAuth off`）的路由器会在开始写入之前被拒绝。它在 Windows 上与 Linux 一样可用，但尚未从 Windows 对真实路由器执行过更新；哪些场景尚未验证见上表。

常用参数：`--verify-interface 接口` 选择最终检查所用的接口，与 `--no-keep-settings` 一起使用时必须提供（该选项会关闭 Wi-Fi）；`--wifi-ssid 名称` 指定从 RAM 安装器继续时重新连接的网络；`--offline` 只使用已校验的缓存；`--yes` 预先确认所有提示；`--device`、`--images` 和 `--outdir` 分别指定配置、镜像缓存和输出目录；`--recover-stale-lock` 在你检查之后清除被中断的切换状态。

到目前为止，更新在真实路由器上只运行过**一次**（2026 年 11 月 7 日，从 Linux 通过网线，v2.1.2）。所有写入阶段均成功；最终密码登录未能完成，因为恢复的配置关闭了密码登录——工具会事先提示，结果改用 SSH 密钥验证。仍允许密码登录的路由器尚未执行过更新。请在手边备好 UART 适配器，并反馈结果。

## 网页向导

```sh
python3 gui/app.py
```

一个本地分步向导，支持英语、俄语、简体中文和巴西葡萄牙语，提供试运行、阶段跟踪和实时日志。参见 [gui/README.md](gui/README.md)。

## 恢复原厂固件

下载官方原厂镜像，其 SHA-256 必须已在设备配置文件中获得批准（[列表](docs/technical.md#getting-the-images)）。然后在已安装的 OpenWrt 中执行：

```sh
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin --interface IFACE
```

在 Windows 中：`py -3 revert.py ...`，这条路径尚未在真实路由器上验证。其他参数：`--transport wired`、`--root-password 'YOUR_OPENWRT_PASSWORD'`、`--dry-run`。确认擦除时必须用拉丁字母输入 `RESTORE`。

`revert.py` 会校验原厂镜像，下载 RAM 系统镜像，把路由器切换到 RAM，恢复原厂固件，并等待原厂系统响应，报告其硬件、ROM 以及设置向导是否在等待。恢复请使用它，而不要重新执行固件写入：执行 `sysupgrade` 之后，原厂的 A/B 备份已经被擦除（见 [NOTICE](NOTICE)）。

## 支持的设备

| 配置 | 硬件 | 安装来源 | 恢复原厂固件 |
|---|---|---|---|
| `rd03v2` | Xiaomi AX3000T RD03v2，Qualcomm IPQ5018 | 原厂 2.0.28 与 2.0.12，已在真实路由器上验证 | 2.0.28 与 2.0.12，已在真实路由器上验证 |

## 其他工具

| 命令 | 用途 |
|---|---|
| `python3 release.py --device rd03v2` | 显示最新发布版以及所需的文件。 |
| `python3 release.py --device rd03v2 --download --wifi --dest images` | 下载并校验镜像。 |
| `python3 attach.py --bind OPERATOR_ADDRESS --peer ROUTER_ADDRESS --session-token TOKEN 'id'` | 重新连接到被中断运行的回调通道。 |
| `python3 probe.py --device rd03v2` | 采集硬件与闪存布局信息，不写入原始固件分区。会初始化或重启原厂固件、修改其状态，并消耗一次性触发器。 |
| `python3 restore.py --device rd03v2 /path/to/approved-stock-image.bin --discover 接口` | 从已启动的 RAM 系统执行底层原厂固件写入。 |
| `python3 ubiparse.py /path/to/ubi-dump.bin` | 离线解析 UBI 转储。 |
| `python3 selftest.py` | 无需路由器的检查（Linux）。在 Windows 中：`py -3 -m unittest test_hostnet test_windows test_i18n`。 |
| `python3 updater.py --dry-run` | 下载并校验更新的镜像，不接触路由器。 |

`probe.py` 未在 Windows 上验证；它的自动 Wi-Fi 重连只在 Linux 上有效。被中断的运行、各个阶段以及恢复流程在[技术文档](docs/technical.md)中有说明（英文）。

## 许可证

GPL-2.0-only。参见 [LICENSE](LICENSE)。
