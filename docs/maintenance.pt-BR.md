# Manutenção do OpenWrt no Xiaomi AX3000T RD03v2

[English](maintenance.md) · [Русский](maintenance.ru.md) · [简体中文](maintenance.zh-CN.md) · **Português (Brasil)**

Execute os comandos no computador, a partir da pasta do repositório. No Windows, use `py -3` no lugar de `python3`. No exemplo de verificação, substitua `12` pelo índice do adaptador ligado ao roteador.

[Preparação e requisitos](../README.pt-BR.md#1-preparação)

## O que foi verificado

| Cenário | Status |
|---|---|
| Linux, firmwares de fábrica 2.0.28 e 2.0.12 | Cenário principal, verificado em roteador real |
| Windows 10, Wi-Fi, firmware de fábrica 2.0.28, release v1.14 `standard` | Verificado em roteador real em 11 de outubro de 2026 ([alexsys87](https://github.com/alexsys87/xiaomi-openwrt-install)), em uma versão anterior do código |
| Windows 11 25H2 em máquina virtual, cabo, firmware de fábrica 2.0.28, release v2.1.2, com senha de root | Verificado em roteador real em 7 de novembro de 2026 nesta versão do código, incluindo a retomada de uma execução interrompida |
| `updater.py` no Linux, no lugar, por Wi-Fi, mantendo as configurações: v2.1.3 para v2.1.2 e depois v2.1.2 para v2.1.3 (em que passa `-F` sozinho) | Executado em 12 de novembro de 2026, com este código, com `--host`. As duas atualizações inicializaram e foram verificadas na revisão do novo release. Ainda não executado em hardware: no lugar por cabo, com `--no-keep-settings` ou `--config-backup`, ou encontrando o roteador sem `--host` |
| Linux por cabo, atualizando pelo instalador em RAM com `--config-backup` | Em 7 de novembro de 2026, todas as etapas de gravação da v2.1.2 foram concluídas. A configuração restaurada desativava o login por senha. A verificação final foi feita com uma chave SSH. |
| `updater.py` a partir do Windows, ou pelo instalador em RAM contra um roteador que mantém login por senha | Testes automatizados; **ainda não verificado em roteador real** |
| Windows, Wi-Fi nesta versão do código | Testes automatizados; **ainda não verificado em roteador real** |
| Windows, retorno ao firmware de fábrica (`revert.py`) | Testes automatizados; **ainda não verificado em roteador real** |

Se você for instalar pelo Windows, mantenha por perto um computador com Linux ou
um adaptador USB-UART, para o caso de uma falha. Relate o resultado em
[Issues](https://github.com/ADCDS/xiaomi-openwrt-install/issues), principalmente
se tudo tiver dado certo: isso ajuda a manter a tabela atualizada.

## Verificação após a gravação

A mensagem `sysupgrade já foi iniciado` significa que a gravação não pode ser
repetida. O fato de o roteador não ter sido encontrado, por si só, não prova que
houve falha. Conecte-se à rede permanente, ou à porta LAN por cabo, e execute
**apenas a verificação**, informando o `resume.json` exatamente desta execução (o
caminho é impresso pelo instalador):

```powershell
py -3 install.py --verify-only ".\install-DATE-TIME\resume.json" --verify-interface 12
```

Não use o manifesto de outro roteador ou de outra instalação.

### Esqueceu a senha da rede Wi-Fi permanente

Conecte-se à **porta LAN** por cabo, obtenha um endereço automaticamente e abra
`http://192.168.1.1`. No LuCI: **Network → Wireless → Edit → Wireless Security**,
defina uma nova senha e depois **Save & Apply**. Não é preciso reset de fábrica.

## Depois da instalação

- Se o log mostrou `root password=unset`, defina a senha de administrador:
  **System → Administration**.
- Confira o país e as configurações de segurança do Wi-Fi.
- Salve as configurações: **System → Backup / Flash Firmware → Generate archive**.
- Guarde a pasta `install-*` em um local privado. O `firstboot.tar.gz` pode conter a senha do Wi-Fi. O `resume.json` contém dados do dispositivo e segredos da sessão. No Windows, escolha uma pasta sem acesso compartilhado. Remova informações sensíveis antes de publicar logs.

## Atualização de um sistema instalado

```sh
python3 updater.py --root-password 'YOUR_OPENWRT_PASSWORD'
```

Por padrão, o `updater.py` salva um backup da configuração em uma pasta privada `update-*` e atualiza o sistema mantendo as configurações. Ele precisa de acesso SSH por senha. Com `--dry-run`, baixa e verifica as imagens sem se conectar ao roteador.

Entre versões v2, a atualização usa `sysupgrade` no próprio sistema. Se a única objeção de `sysupgrade -T` for a recusa incorreta das versões v2.0.0 a v2.1.2, a ferramenta aplica `-F`. Outras falhas interrompem a atualização. Atualizações que partem da v1.x ou voltam para ela usam o instalador em RAM. Esse também é o efeito de `--via-ram-installer` na v2 e envolve reformatar as duas partições UBI. Veja o [procedimento manual (em inglês)](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2/blob/main/docs/no-uart-reflash.md).

| Opção | Quando usar |
|---|---|
| `--release TAG`, `--offline` | Escolha uma versão. Para trabalhar offline, o cache precisa conter as imagens verificadas dessa tag. |
| `--no-keep-settings`, `--verify-interface IFACE` | Para uma configuração limpa, a verificação final precisa de uma interface com fio. Em uma atualização por cabo, o padrão é a interface ligada ao roteador. |
| `--wifi-ssid NOME` | Informe a rede permanente ao retomar pelo instalador em RAM e verificar por Wi-Fi. |
| `--device`, `--images`, `--outdir` | Escolha o perfil, o cache de imagens ou a pasta de saída. |
| `--yes` | Confirme as operações antecipadamente. |

O OpenWrt tem um único kernel instalado. No caminho via RAM, esse kernel é sobrescrito antes da reinicialização. Se a atualização for interrompida e o instalador em RAM estiver acessível, retome com `--config-backup` e o arquivo salvo. Uma gravação incompleta pode exigir recuperação por UART ou TFTP de fábrica. Use `--recover-stale-lock` somente depois de inspecionar o bloqueio inativo.

## Retorno ao firmware de fábrica

Escolha a imagem oficial mais recente da [lista aprovada para o RD03v2 (em inglês)](technical.md#getting-the-images). O bootloader exige uma versão igual ou posterior à última versão de fábrica que o roteador executou.

Com o roteador ainda executando o OpenWrt, inicie a restauração pelo computador.

```sh
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin --interface IFACE
```

No Windows: `py -3 revert.py ...`; esse caminho ainda não foi testado em roteador
real. Parâmetros adicionais: `--transport wired`, `--root-password 'YOUR_OPENWRT_PASSWORD'`,
`--dry-run`. A confirmação do apagamento deve ser digitada com a palavra `RESTORE`,
em letras latinas.

O `revert.py` verifica a imagem de fábrica, baixa as imagens do sistema em RAM,
coloca o roteador em RAM, restaura o firmware de fábrica e aguarda a resposta do sistema de
fábrica, informando o hardware, a ROM e se o assistente de configuração está
aguardando. Use-o para o retorno, e não uma nova execução da
gravação: depois do `sysupgrade` o backup A/B de fábrica já foi apagado (veja o
[NOTICE](../NOTICE)).
