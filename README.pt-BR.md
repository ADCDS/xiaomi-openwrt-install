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

## Dispositivo suportado

Somente o Xiaomi AX3000T **RD03v2** (Qualcomm IPQ5018) é compatível. A instalação e o retorno ao firmware de fábrica foram testados com as versões **2.0.28** e **2.0.12**. Veja o [perfil do dispositivo](devices.py).

## O que foi verificado

Consulte os [resultados por sistema, conexão e versão do firmware](docs/maintenance.pt-BR.md#o-que-foi-verificado).

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

### Verificar o sistema instalado com uma chave SSH

Se a interface web e o login manual por SSH com chave funcionam, repita apenas
a verificação, sem gravar novamente:

```sh
python3 install.py --verify-only /path/to/install/resume.json \
  --verify-interface en0 --verify-host 192.168.1.1 \
  --verify-ssh-key ~/.ssh/id_ed25519
```

Substitua a interface e o caminho da chave privada. Use `agent` no lugar do
caminho para usar ssh-agent ou a configuração padrão do OpenSSH. Carregue chaves
protegidas com `ssh-add` primeiro. Este modo exige OpenSSH e uma chave de host
confiável para o endereço exato: confirme-a com um login SSH manual primeiro.
Ele verifica a placa, as partições permanentes, a versão e a reinicialização
registrada, depois atualiza o `resume.json` local. Essas opções exigem
`--verify-only` e não alteram a autenticação durante a gravação.

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

Use `py -3` no lugar de `python3`. Nos exemplos, substitua `12` pelo índice do seu adaptador. Mantenha o IPv6 ativado para que o instalador encontre o roteador após a reinicialização.

## 2. Imagens e Internet

O instalador usa por padrão a versão publicada mais recente do [OpenWrt para RD03v2](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2), com aceleração NSS. O último build `standard` sem NSS é o v1.14.

Antes de desconectar a Internet, baixe e verifique as imagens com `--dry-run`. Para usar `--offline`, informe a tag com `--release TAG` e prepare o cache para o tipo de conexão escolhido. Veja a [verificação de imagens (em inglês)](docs/technical.md#verifying-openwrt-releases).

O exemplo abaixo prepara as imagens de Wi-Fi no Windows. Para verificar as imagens de Ethernet, troque `--transport wifi` por `--transport wired`.

```powershell
py -3 install.py --transport wifi --dry-run
```

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

Se a verificação final for por Wi-Fi, informe o SSID da rede permanente, uma senha de 8 a 63 caracteres ASCII imprimíveis e o código do país de uso. Para deixar o Wi-Fi desativado, faça a verificação por cabo com `--verify-interface`. A senha de root é independente da senha do Wi-Fi.

Para fornecer senhas por variáveis de ambiente, consulte a [configuração do primeiro boot (em inglês)](docs/technical.md#first-boot-configuration).

O instalador segue o idioma do sistema. Use `--lang pt-BR` para escolher português do Brasil. As mensagens de diagnóstico são exibidas em inglês.

## 5. Reinicializações: três redes Wi-Fi diferentes

Mantenha o terminal aberto. Quando solicitado, passe da rede de fábrica para a rede temporária indicada pelo instalador e, após a gravação, para a rede permanente que você configurou.

Se a execução for interrompida depois da pré-verificação, o instalador imprime o
comando para continuar. Antes disso ele não imprime: nenhum firmware foi gravado
ainda, então recomeçar é seguro se o gatilho de uso único não foi gasto. O log
sozinho não diz qual dos casos é; se o gatilho já foi gasto, o exploit recusa e só
um reset de fábrica o recupera. Depois de perder o link, o roteador pode levar até 30 minutos para se
conectar de novo.

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

Se a gravação começou, use o `resume.json` desta execução para fazer [apenas a verificação](docs/maintenance.pt-BR.md#verificação-após-a-gravação).

### Esqueceu a senha da rede Wi-Fi permanente

Você pode [alterar a senha do Wi-Fi pelo LuCI](docs/maintenance.pt-BR.md#esqueceu-a-senha-da-rede-wi-fi-permanente) usando um cabo de rede.

## 7. Depois da instalação

Defina uma senha de administrador se ela estiver em branco. Guarde os backups e o `resume.json` em uma pasta privada. Veja os [cuidados depois da instalação](docs/maintenance.pt-BR.md#depois-da-instalação).

## Atualização de um sistema instalado

O `updater.py` mantém as configurações por padrão. Veja como [atualizar o OpenWrt e retomar uma atualização interrompida](docs/maintenance.pt-BR.md#atualização-de-um-sistema-instalado).

Depois de uma atualização verificada, o `updater.py` reinstala os pacotes que
você instalou (`--no-reinstall` desliga isso). Arquivos que não pertencem a
nenhum pacote só são procurados com `--auto-keep`. Detalhes (em inglês):
[docs/packages.md](docs/packages.md).

## Assistente web

```sh
python3 gui/app.py
```

Um assistente local passo a passo em inglês, russo, chinês simplificado e português do
Brasil: simulação, acompanhamento de etapas e log em tempo real. Como o
`updater.py`, roda tanto no Windows 10/11 quanto no Linux. Veja
[gui/README.md](gui/README.md).

## Retorno ao firmware de fábrica

Use o `revert.py` com uma imagem oficial aprovada para o seu modelo. Veja como [restaurar o firmware de fábrica](docs/maintenance.pt-BR.md#retorno-ao-firmware-de-fábrica).

## Ferramentas adicionais

Consulte os [comandos de diagnóstico e teste (em inglês)](docs/technical.md#diagnostics-and-tests).

## Licença

GPL-2.0-only. Veja [LICENSE](LICENSE).
