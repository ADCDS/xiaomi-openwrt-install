# xiaomi-openwrt-install

[English](README.md) · [Русский](README.ru.md) · [简体中文](README.zh-CN.md) · **Português (Brasil)** · [Documentação técnica (em inglês)](docs/technical.md)

Instalação do OpenWrt em roteadores Xiaomi compatíveis sem UART e sem solda, e
também o retorno de um roteador já instalado a uma imagem de fábrica aprovada.
Funciona em Linux e em Windows 10/11, sem WSL.

O instalador obtém root pela
[cadeia de exploits `cab_meshd` documentada](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure#combined-v1--v2-capuci-exploit),
verifica a placa, a NAND e o layout de partições, baixa as imagens do OpenWrt para
o seu dispositivo, inicia o sistema temporário em RAM e grava o firmware
permanente.

> Leia o [NOTICE](NOTICE). Use o instalador apenas no seu próprio roteador.

As perguntas e instruções do instalador seguem o idioma do sistema. É possível
escolher explicitamente entre os quatro idiomas disponíveis — inglês, russo,
chinês simplificado e português do Brasil — com `--lang en`, `--lang ru`,
`--lang zh-CN` ou `--lang pt-BR`. O diagnóstico de baixo nível continua em inglês:
assim fica mais fácil anexá-lo a um relato de erro.

## O que foi verificado

| Cenário | Status |
|---|---|
| Linux, firmwares de fábrica 2.0.28 e 2.0.12 | Cenário principal, verificado em roteador real |
| Windows 10, Wi-Fi, firmware de fábrica 2.0.28, release v1.14 `standard` | Verificado em roteador real em 11 de outubro de 2026 ([alexsys87](https://github.com/alexsys87/xiaomi-openwrt-install)), em uma versão anterior do código |
| Windows 11 25H2 em máquina virtual, cabo, firmware de fábrica 2.0.28, release v2.1.2, com senha de root | Verificado em roteador real em 7 de novembro de 2026 nesta versão do código, incluindo a retomada de uma execução interrompida |
| Windows, Wi-Fi nesta versão do código | Testes automatizados; **ainda não verificado em roteador real** |
| Windows, retorno ao firmware de fábrica (`revert.py`) | Testes automatizados; **ainda não verificado em roteador real** |

Se você for instalar pelo Windows, mantenha por perto um computador com Linux ou
um adaptador USB-UART, para o caso de uma falha. Relate o resultado em
[Issues](https://github.com/ADCDS/xiaomi-openwrt-install/issues), principalmente
se tudo tiver dado certo: isso ajuda a manter a tabela atualizada.

Somente o Xiaomi AX3000T **RD03v2** (Qualcomm IPQ5018) é suportado. O instalador
recusa outros modelos. Não aplique estas instruções a eles.

## 1. Preparação

- Garanta alimentação estável para o roteador. Não o desligue durante a gravação.
- Salve as configurações de fábrica de que você precisa: o reset apaga a
  configuração e as senhas.
- Isole a conexão: nenhum dispositivo de terceiros deve se conectar à rede de
  fábrica nem à rede temporária. A senha da rede temporária é pública, e o root do
  sistema temporário em RAM não tem senha.
- Desative a VPN e outras redes cujos endereços possam coincidir com os endereços
  do roteador.

Baixe o repositório e abra um terminal na pasta dele:

```sh
git clone https://github.com/ADCDS/xiaomi-openwrt-install.git
cd xiaomi-openwrt-install
```

### Linux

É preciso ter Python 3.9 ou mais recente:

```sh
sudo apt install sshpass openssl
```

### Windows 10/11

Instale o Python 3.9 ou mais recente a partir do
[python.org](https://www.python.org/downloads/windows/), incluindo o launcher
`py`. Você pode baixar o ZIP do repositório em vez de usar `git clone`. Abra o
PowerShell na pasta que contém o `install.py` e instale as dependências **enquanto
ainda houver acesso à Internet**:

```powershell
py -3 -m pip install -r requirements-windows.txt
py -3 install.py --list-interfaces
```

O `--list-interfaces` mostra o índice, o nome e o tipo de cada adaptador. Use o
seu índice ou nome no lugar de `12` nos comandos abaixo. Se o nome tiver
caracteres cirílicos ou espaços, é mais simples usar o índice; ele pode mudar
depois que você alterar os adaptadores.

WSL, `sshpass`, `nmcli` e a instalação separada do OpenSSL não são necessários.
Não desative o IPv6 no adaptador escolhido: a redescoberta do roteador após a
reinicialização usa o IPv6 link-local.

Em máquina virtual, conecte a placa de rede como bridge ou macvtap. Durante os
testes, o passthrough USB do adaptador RTL8153 travava em transmissões longas, e
sem isso a instalação não avança.

Execute o instalador como usuário comum; privilégios de administrador só são
necessários para a regra de firewall (veja a
[seção 6](#a-conexão-de-retorno-não-chega-firewall-do-windows)). No Windows, use
`py -3` no lugar de `python3`. No Prompt de Comando (cmd) você pode usar
`install.cmd` com os mesmos parâmetros; no PowerShell: `.\install.cmd`.

## 2. Imagens e Internet

A rede Wi-Fi de fábrica da Xiaomi pode não dar acesso à Internet, então baixe e
verifique as imagens **com antecedência**. O comando não se comunica com o
roteador:

```powershell
py -3 install.py --transport wifi --dry-run
```

No Linux é a mesma coisa: `python3 install.py --transport wifi --dry-run`. Para
instalar por cabo, remova `--transport wifi`.

Por padrão é escolhido o último release publicado do
[port para o RD03v2](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2), com
aceleração de hardware NSS. A saída traz uma linha
`selected ... from release vX.Y.Z`: anote essa tag. Para operar sem Internet, é
preciso de uma tag explícita e das imagens já baixadas:

```powershell
py -3 install.py --release vX.Y.Z --transport wifi --offline --dry-run
```

A build sem NSS existe apenas no último release da linha v1.x:
`py -3 install.py standard --release v1.14`.

## 3. Reset e conexão

1. Faça o reset de hardware com o botão Reset.
2. Conecte-se à porta **LAN** (não à WAN) por cabo, ou a uma rede Wi-Fi de fábrica
   aberta no formato `minet_rd03_XXXX`.
3. **Não conclua o assistente de configuração da Xiaomi**, nem no navegador nem no
   Mi Home. A página que abrir sozinha pode ser fechada.
4. Desative a VPN e conexões desnecessárias. Na instalação por Wi-Fi, desconecte o
   cabo e não deixe o computador mudar para a sua rede doméstica.
5. Verifique o endereço: deve ser `192.168.31.x`, e `http://192.168.31.1` deve
   abrir. A mensagem “Sem acesso à Internet” é normal aqui.

## 4. Instalação

```sh
python3 install.py
```

No Windows, informando o seu adaptador:

```powershell
py -3 install.py --interface 12 --configure
```

Sem Internet, acrescente a tag da seção 2: `--release vX.Y.Z --offline`.

O instalador detecta sozinho a forma de conexão; você pode forçá-la com
`--transport wired` ou `--transport wifi`, e escolher a interface com
`--interface`. Antes de cada gravação irreversível ele pede confirmação. A
resposta deve ser a palavra `YES`, **em letras latinas**.

Perguntas feitas com `--configure` (na instalação por Wi-Fi elas são obrigatórias
mesmo sem esse parâmetro, exceto a senha de root):

| Pergunta | O que informar |
|---|---|
| Senha de root | Nova senha de administrador do OpenWrt. Melhor não deixar em branco. |
| Nome da rede Wi-Fi (SSID) | Nome da rede permanente, por exemplo `Xiaomi`. |
| Senha do Wi-Fi | De 8 a 63 caracteres ASCII imprimíveis: letras latinas, dígitos, sinais comuns. Sem acentos, sem cirílico e sem emojis. |
| Código do país | País de uso real, por exemplo `BR`. |

As senhas não aparecem enquanto você digita. Guarde-as: a senha do Wi-Fi e a
senha de root não estão ligadas uma à outra, e o instalador não exibe a senha do
Wi-Fi.

Para deixar o Wi-Fi permanente desativado, faça a verificação final por cabo:

```sh
python3 install.py --transport wifi --interface WIFI_INTERFACE \
    --verify-interface ETHERNET_INTERFACE
```

## 5. Reinicializações: três redes Wi-Fi diferentes

Na instalação por Wi-Fi, não feche a janela do terminal nem inicie uma segunda
cópia do instalador. O roteador troca de rede duas vezes, e é preciso conectar-se
manualmente às novas redes.

1. **Rede de fábrica da Xiaomi.** Depois da configuração inicial o roteador
   reinicia, e o instalador escreve `waiting up to 300s for tcp/19553`. Essa
   espera é normal. Se o computador se desconectar, conecte-se de novo à rede de
   fábrica.
2. **Sistema temporário em RAM.** Quando a rede de fábrica desaparecer,
   conecte-se a:

   ```text
   SSID:   OpenWrt-RD03v2-Installer
   Пароль: rd03v2install
   ```

3. **OpenWrt permanente.** Depois da gravação a rede temporária desaparece.
   Conecte-se à rede definida na instalação. O instalador espera o roteador por
   até 600 segundos e verifica a placa, o overlay permanente e os tamanhos das
   partições.

No final deve aparecer `[+] O OpenWrt está instalado na NAND.`

Se a execução for interrompida, o instalador imprime o comando para continuar.
Não comece de novo: o gatilho da vulnerabilidade só funciona uma vez por reset de
fábrica. Depois de perder o link, o roteador pode voltar a se conectar apenas após
30 minutos.

Depois das mensagens `flash-started` ou “não desligue a alimentação”, **não desligue a alimentação, não resete o roteador e não execute a etapa de gravação novamente**.

## 6. Erros comuns

Boa parte desta seção vem de uma instalação real feita a partir do Windows 10
([alexsys87](https://github.com/alexsys87/xiaomi-openwrt-install)).

### Cirílico no nome da interface e `Interface ... not found`

No PowerShell, o nome pode ficar corrompido ao ser copiado. Use o índice numérico
de `--list-interfaces`, por exemplo `--interface 12`.

### `--interface ... não corresponde à rota para ...`

O sistema encaminha o tráfego até o roteador por outro adaptador, e o
`--interface` não altera o roteamento. O instalador para antes de consumir o
gatilho de uso único. Conecte-se ao roteador certo, desative a VPN e a rede
concorrente, confira o endereço (`192.168.31.x`) e escolha a interface correta.

### `V2 CAP path is unavailable (netmode=4)` e `inited=1`

O roteador já está configurado. Faça o reset de fábrica, conecte-se à rede de
fábrica e **não conclua o assistente da Xiaomi**. Se o erro se repetir depois de
um reset correto, salve o log e abra uma Issue; não tente contornar as
verificações.

### GitHub: `The handshake operation timed out`

Não foi possível obter as informações do release por HTTPS. Isso não tem relação
com o roteador: na rede de fábrica muitas vezes não há Internet. Se as imagens
foram baixadas com antecedência, use `--release vX.Y.Z --offline` da seção 2. O
`--offline` sozinho não substitui o download. Não é preciso fazer outro reset por
causa deste erro.

### Senha WPA2 não é aceita

Mude o layout do teclado para o inglês. São necessários de 8 a 63 caracteres ASCII
imprimíveis. Essa verificação acontece antes do início da gravação, então o
roteador não é afetado.

### A conexão de retorno não chega: firewall do Windows

Sintoma: `sem shell de root` ou `no root shell`. Primeiro confira a rede e a rota.
Se a conexão de entrada vinda do roteador estiver sendo bloqueada, libere o
`python.exe` e as portas necessárias apenas a partir do endereço de fábrica. O
Windows pode considerar a rede de fábrica como pública, então uma regra só para
redes privadas pode não bastar. No PowerShell **como administrador**:

```powershell
$python = py -3 -c "import sys; print(sys.executable)"
New-NetFirewallRule -DisplayName "Xiaomi OpenWrt installer" -Direction Inbound -Action Allow -Program $python -Protocol TCP -LocalPort 8000,4444,4445 -RemoteAddress 192.168.31.1 -Profile Any
```

Se você mudou o endereço (`--host`) ou as portas, ajuste a regra. Não desative o
firewall por completo. Depois da instalação, remova a exceção:

```powershell
Get-NetFirewallRule -DisplayName "Xiaomi OpenWrt installer" | Remove-NetFirewallRule
```

### Gravação iniciada, mas `permanent OpenWrt was not verified`

A mensagem `sysupgrade já foi iniciado` significa que a gravação não pode ser
repetida. O fato de o roteador não ter sido encontrado, por si só, não prova que
houve falha. Conecte-se à rede permanente, ou à porta LAN por cabo, e execute
**apenas a verificação**, informando o `resume.json` exatamente desta execução (o
caminho é impresso pelo instalador):

```powershell
py -3 install.py --verify-only ".\install-ДАТА-ВРЕМЯ\resume.json" --verify-interface 12
```

Não use o manifesto de outro roteador ou de outra instalação.

### Esqueceu a senha da rede Wi-Fi permanente

Conecte-se à **porta LAN** por cabo, obtenha um endereço automaticamente e abra
`http://192.168.1.1`. No LuCI: **Network → Wireless → Edit → Wireless Security**,
defina uma nova senha e depois **Save & Apply**. Não é preciso reset de fábrica.

## 7. Depois da instalação

- Se o log mostrou `root password=unset`, defina a senha de administrador:
  **System → Administration**.
- Confira o país e as configurações de segurança do Wi-Fi.
- Salve as configurações: **System → Backup / Flash Firmware → Generate archive**.
- Guarde a pasta `install-*` em um local privado: ela contém backups de partições,
  o `firstboot.tar.gz` e o `resume.json` com senhas, segredos de sessão e dados do
  dispositivo. Não publique esses arquivos nem os logs completos sem remover os
  segredos. No Windows, as permissões são herdadas da pasta e não definidas com
  `chmod`, então escolha uma pasta sem acesso compartilhado.

## Atualização de um sistema instalado

```sh
python3 updater.py --root-password 'ВАШ_ПАРОЛЬ'
```

Atualiza um OpenWrt instalado para o último release do port, mantendo as
configurações, como o `sysupgrade`. `--release ТЕГ` escolhe um release específico,
`--no-keep-settings` faz uma instalação limpa, e `--dry-run` apenas baixa e
verifica as imagens. Um `sysupgrade` comum transforma esta placa em tijolo, por
isso a atualização passa pelo instalador em RAM. Antes da gravação, uma cópia de
segurança das configurações é salva em uma pasta privada `update-*`.

Aqui não há reserva A/B: o seguro para o caso de falha é o UART. Se a execução for
interrompida, conecte-se ao instalador em RAM e repita o comando com
`--config-backup` e a cópia salva. O login é feito por senha, então um roteador com
acesso somente por chaves (`PasswordAuth off`) será recusado antes do início da
gravação. No Windows também funciona. O que ainda não foi verificado em roteador real
está na tabela acima.

Em roteador real, a atualização até agora foi executada **uma única vez** (a partir
do Linux, por cabo, v2.1.2, em 7 de novembro de 2026). Mantenha um adaptador UART à
mão e relate o resultado.

## Assistente web

```sh
python3 gui/app.py
```

Um assistente local passo a passo em inglês, russo, chinês simplificado e português do
Brasil: simulação, acompanhamento de etapas e log em tempo real. Veja
[gui/README.md](gui/README.md).

## Retorno ao firmware de fábrica

Baixe uma imagem oficial de fábrica cujo SHA-256 esteja aprovado no perfil do
dispositivo ([lista](docs/technical.md#getting-the-images)). Depois, a partir do
OpenWrt instalado:

```sh
python3 revert.py --device rd03v2 /путь/к/заводскому-образу.bin --interface ИНТЕРФЕЙС
```

No Windows: `py -3 revert.py ...`; esse caminho ainda não foi testado em roteador
real. Parâmetros adicionais: `--transport wired`, `--root-password ПАРОЛЬ`,
`--dry-run`. A confirmação do apagamento deve ser digitada com a palavra `RESTORE`,
em letras latinas.

O `revert.py` verifica a imagem de fábrica, baixa as imagens do sistema em RAM,
coloca o roteador em RAM, restaura o firmware de fábrica e confirma que o
assistente de configuração voltou. Use-o para o retorno, e não uma nova execução da
gravação: depois do `sysupgrade` o backup A/B de fábrica já foi apagado (veja o
[NOTICE](NOTICE)).

## Ferramentas adicionais

| Comando | Finalidade |
|---|---|
| `python3 release.py --device rd03v2` | Mostrar o último release e os arquivos necessários. |
| `python3 release.py --device rd03v2 --download --wifi --dest images` | Baixar e verificar as imagens. |
| `python3 attach.py --bind АДРЕС --peer АДРЕС_РОУТЕРА --session-token ТОКЕН 'id'` | Reconectar ao canal de retorno de uma execução interrompida. |
| `python3 selftest.py` | Verificações sem roteador (Linux). No Windows: `py -3 -m unittest test_hostnet test_windows test_i18n`. |

O `probe.py` não foi testado no Windows; a reconexão automática ao Wi-Fi dele só
funciona no Linux. Execuções interrompidas, etapas individuais e recuperação estão
descritas na [documentação técnica](docs/technical.md) (em inglês).

## Licença

GPL-2.0-only. Veja [LICENSE](LICENSE).
