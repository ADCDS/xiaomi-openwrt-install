# xiaomi-openwrt-install

[English](README.md) · [Русский](README.ru.md) · **简体中文** · [Português (Brasil)](README.pt-BR.md) · [技术文档（英文）](docs/technical.md)

在受支持的 Xiaomi 路由器上安装 OpenWrt，无需 UART，也无需焊接；同时可以把已安装 OpenWrt 的路由器恢复到经批准的原厂固件。可在 Linux 以及 Windows 10/11 上运行，无需 WSL。

安装器通过[已公开的 `cab_meshd` 漏洞利用链](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure#combined-v1--v2-capuci-exploit)获取 root，校验主板、NAND 和分区布局，下载适用于你的设备的 OpenWrt 镜像，引导临时的 RAM 系统，然后写入永久固件。

> 请阅读 [NOTICE](NOTICE)。只在你自己的路由器上使用本安装器。

## 支持的设备

仅支持小米 AX3000T **RD03v2**（Qualcomm IPQ5018）。已在原厂固件 **2.0.28** 和 **2.0.12** 上验证安装 OpenWrt 和恢复原厂固件。详见[设备配置](devices.py)。

## 已验证情况

按操作系统、连接方式和固件版本查看[实机测试结果](docs/maintenance.zh-CN.md#已验证情况)。

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

在 Windows 中使用 `py -3` 代替 `python3`。请将示例中的 `12` 换成自己的网卡索引。保持 IPv6 启用，以便安装器在重启后重新找到路由器。

## 2. 镜像与互联网

默认使用带 NSS 硬件加速的 [RD03v2 移植版](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2)的最新正式版本。最后一个不带 NSS 的 `standard` 版本是 v1.14。

请在有互联网连接时使用 `--dry-run` 下载并校验镜像。使用 `--offline` 时，需用 `--release TAG` 指定版本标签，并提前缓存适合所选连接方式的镜像。详见[镜像校验说明（英文）](docs/technical.md#verifying-openwrt-releases)。

以下 Windows 示例下载 Wi-Fi 所需的镜像。如果要校验有线连接使用的镜像，请将 `--transport wifi` 换为 `--transport wired`。

```powershell
py -3 install.py --transport wifi --dry-run
```

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

如果最终通过 Wi-Fi 校验，请设置永久网络的 SSID、由 8 至 63 个可打印 ASCII 字符组成的密码，以及实际使用地的国家或地区代码。若要保持 Wi-Fi 关闭，可用 `--verify-interface` 指定有线网卡进行校验。root 密码单独设置。

如需通过环境变量传递密码，请参阅[首次启动配置（英文）](docs/technical.md#first-boot-configuration)。

提示默认使用系统语言。可用 `--lang zh-CN` 选择简体中文。底层诊断信息使用英文。

## 5. 重启：三个不同的 Wi-Fi 网络

请保持终端开启。按安装器的提示从原厂 Wi-Fi 切换到临时网络，写入完成后再连接到自己设置的永久网络。

如果运行在预检之后被中断，安装器会输出用于继续的命令。在此之前的失败不会输出：此时尚未写入固件，只要一次性触发器未被消耗，从头开始是安全的。仅凭日志无法判断它是否已被消耗；如果已经消耗，漏洞利用会被拒绝，只有恢复出厂设置才能重新获得。断开连接后，路由器最多可能需要 30 分钟才能再次连上。

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

如果已经开始写入，请使用本次运行的 `resume.json` [仅执行校验](docs/maintenance.zh-CN.md#写入后校验)。

### 忘记了永久 Wi-Fi 网络的密码

用网线连接路由器后，可以[在 LuCI 中修改 Wi-Fi 密码](docs/maintenance.zh-CN.md#忘记了永久-wi-fi-网络的密码)。

## 7. 安装之后

如果管理员密码为空，请设置密码。分区备份和 `resume.json` 应保存在私密目录中。详见[安装后的设置与备份](docs/maintenance.zh-CN.md#安装之后)。

## 更新已安装的系统

`updater.py` 默认保留现有设置。详见[更新 OpenWrt 以及继续中断的更新](docs/maintenance.zh-CN.md#更新已安装的系统)。

## 网页向导

```sh
python3 gui/app.py
```

一个本地分步向导，支持英语、俄语、简体中文和巴西葡萄牙语，提供试运行、阶段跟踪和实时日志。与 `updater.py` 一样，它同时支持 Windows 10/11 和 Linux。参见 [gui/README.md](gui/README.md)。

## 恢复原厂固件

请使用 `revert.py` 和适用于本机型的已批准原厂镜像。详见[恢复原厂固件的步骤](docs/maintenance.zh-CN.md#恢复原厂固件)。

## 其他工具

镜像检查和本地测试命令见[诊断与测试（英文）](docs/technical.md#diagnostics-and-tests)。

## 许可证

GPL-2.0-only。参见 [LICENSE](LICENSE)。
