# 小米 AX3000T RD03v2 的 OpenWrt 更新与恢复

[English](maintenance.md) · [Русский](maintenance.ru.md) · **简体中文** · [Português (Brasil)](maintenance.pt-BR.md)

请在电脑上的仓库目录中运行命令。在 Windows 中请用 `py -3` 代替 `python3`。在校验示例中，请将 `12` 换成连接路由器的网卡索引。

[准备工作与系统要求](../README.zh-CN.md#1-准备工作)

## 已验证情况

| 场景 | 状态 |
|---|---|
| Linux，原厂固件 2.0.28 和 2.0.12 | 主要场景，已在真实路由器上验证 |
| Windows 10、Wi-Fi、原厂固件 2.0.28、发布版 v1.14 `standard` | 已于 2026 年 10 月 11 日在真实路由器上验证（[alexsys87](https://github.com/alexsys87/xiaomi-openwrt-install)），基于较早版本的代码 |
| 虚拟机中的 Windows 11 25H2、网线、原厂固件 2.0.28、发布版 v2.1.2、设置了 root 密码 | 已于 2026 年 11 月 7 日在此版本代码上于真实路由器验证，包括继续被中断的运行 |
| `updater.py`，从 Linux 通过 Wi-Fi 就地更新并保留设置：v2.1.3 到 v2.1.2，再从 v2.1.2 到 v2.1.3（工具自行传入 `-F`） | 于 2026 年 11 月 12 日用当前代码运行，指定了 `--host`。两次更新均启动成功，并在新版本的修订号上通过验证。尚未在硬件上运行：通过网线就地更新、使用 `--no-keep-settings` 或 `--config-backup`，以及不指定 `--host` 自动寻找路由器 |
| Linux 经网线使用 RAM 安装器更新，通过 `--config-backup` 恢复配置 | 2026 年 11 月 7 日，v2.1.2 的所有写入阶段均成功。恢复的配置关闭了密码登录，最终使用 SSH 密钥验证了结果。 |
| 从 Windows 运行 `updater.py`，以及通过 RAM 安装器更新仍允许密码登录的路由器 | 自动化测试；**尚未在真实路由器上验证** |
| Windows、Wi-Fi，此版本代码 | 自动化测试；**尚未在真实路由器上验证** |
| Windows，恢复原厂固件（`revert.py`） | 自动化测试；**尚未在真实路由器上验证** |

如果从 Windows 安装，请在手边准备一台 Linux 电脑或一个 USB-UART 适配器，以备出现故障。请把结果反馈到 [Issues](https://github.com/ADCDS/xiaomi-openwrt-install/issues)，尤其是成功的情况：这有助于更新上表。

## 写入后校验

出现 `sysupgrade 已启动` 的提示，意味着不能重复写入。没有找到路由器本身并不能证明失败。请连接到永久网络，或用网线连接到 LAN 口，并**只执行校验**，同时指定本次运行自己的 `resume.json`（路径由安装器输出）：

```powershell
py -3 install.py --verify-only ".\install-DATE-TIME\resume.json" --verify-interface 12
```

不要使用其他路由器或其他安装过程的清单文件。

### 忘记了永久 Wi-Fi 网络的密码

用网线连接到 **LAN 口**，自动获取地址，然后打开 `http://192.168.1.1`。在 LuCI 中依次进入 **Network → Wireless → Edit → Wireless Security**，设置新密码，然后点击 **Save & Apply**。无需恢复出厂设置。

## 安装之后

- 如果日志中出现 `root password=unset`，请设置管理员密码：**System → Administration**。
- 检查国家/地区设置和 Wi-Fi 安全设置。
- 保存配置：**System → Backup / Flash Firmware → Generate archive**。
- 将 `install-*` 保存在私密目录中。`firstboot.tar.gz` 可能含有 Wi-Fi 密码，`resume.json` 则含有设备数据和会话凭据。在 Windows 中请选择未共享的文件夹。公开日志前请先删除敏感信息。

## 更新已安装的系统

```sh
python3 updater.py --root-password 'YOUR_OPENWRT_PASSWORD'
```

`updater.py` 默认先将配置备份到私密的 `update-*` 文件夹，再保留配置更新 OpenWrt。更新程序需要通过 SSH 使用密码登录。`--dry-run` 仅下载并校验镜像，不连接路由器。

v2 版本之间使用已安装系统的 `sysupgrade` 就地更新。v2.0.0 至 v2.1.2 可能错误地拒绝就地更新。如果这是 `sysupgrade -T` 唯一的报错，更新程序会传入 `-F`。其他错误会中止更新。从 v1.x 更新或退回 v1.x 时均使用 RAM 安装器。在 v2 版本间也可用 `--via-ram-installer` 选择此方式，两个 UBI 分区都会重新格式化。详见[手动更新步骤（英文）](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2/blob/main/docs/no-uart-reflash.md)。

| 选项 | 用途 |
|---|---|
| `--release TAG`、`--offline` | 选择版本。离线更新需要缓存中有该标签对应的已校验镜像。 |
| `--no-keep-settings`、`--verify-interface 接口` | 清除配置后需使用有线网卡进行最终校验。通过网线更新时，默认使用连接路由器的网卡。 |
| `--wifi-ssid 名称` | 从 RAM 安装器继续更新且最终通过 Wi-Fi 校验时，指定要重新连接的永久网络。 |
| `--device`、`--images`、`--outdir` | 选择设备配置、镜像缓存或输出目录。 |
| `--yes` | 预先确认操作。 |

OpenWrt 只有一份已安装的内核。切换到 RAM 安装器时，该内核会在重启前被覆盖。如果更新中断但 RAM 安装器仍可访问，请用 `--config-backup` 指定已保存的备份并继续更新。写入中断后可能需要通过 UART 或原厂 TFTP 恢复。使用 `--recover-stale-lock` 前，请检查遗留的写入锁是否确实已失效。

## 恢复原厂固件

从[已批准的 RD03v2 原厂镜像列表（英文）](technical.md#getting-the-images)中选择最新版本。引导程序要求镜像版本至少与这台路由器上次运行的原厂固件版本相同。

在路由器运行 OpenWrt 时执行下列命令。

```sh
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin --interface IFACE
```

在 Windows 中：`py -3 revert.py ...`，这条路径尚未在真实路由器上验证。其他参数：`--transport wired`、`--root-password 'YOUR_OPENWRT_PASSWORD'`、`--dry-run`。确认擦除时必须用拉丁字母输入 `RESTORE`。

`revert.py` 会校验原厂镜像，下载 RAM 系统镜像，把路由器切换到 RAM，恢复原厂固件，并等待原厂系统响应，报告其硬件、ROM 以及设置向导是否在等待。恢复请使用它，而不要重新执行固件写入：执行 `sysupgrade` 之后，原厂的 A/B 备份已经被擦除（见 [NOTICE](../NOTICE)）。
