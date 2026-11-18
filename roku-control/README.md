# roku-control

Controle da TV Roku (AOC 43S5135/78G) pela rede local, usando a API ECP da Roku (porta 8060).
Precisa só de Python 3.8+, sem `pip install`. Rode no computador que está no mesmo Wi-Fi da TV.

Na TV: **Configurações > Sistema > Configurações avançadas do sistema > Controle por aplicativos móveis > Acesso à rede = Padrão** (ou Permissivo).

## Primeiros passos

```bash
python roku.py descobrir     # acha a TV pelo MAC 94:8C:D7:E8:DA:4F e salva o IP em config.json
python roku.py info          # modelo, versão e campos de fone/Bluetooth
python roku.py apps          # lista os apps e salva apps.json
```

Sem `config.json`, o script usa 192.168.5.199. Para forçar outro IP: `python roku.py --ip 192.168.5.50 status`.

## Comandos

| Comando | Exemplo |
|---|---|
| `descobrir` | `python roku.py descobrir` (ou `--rede 192.168.5.0/24`) |
| `info` | `python roku.py info` (`--tudo` mostra todos os campos) |
| `apps` | `python roku.py apps` |
| `ligar` / `desligar` | `python roku.py ligar` (se não responder, tenta Wake-on-LAN) |
| `volume subir/descer/mudo [N]` | `python roku.py volume subir 5` |
| `navegar cima/baixo/esquerda/direita/ok/voltar/home [N]` | `python roku.py navegar baixo 3` |
| `abrir <app>` | `python roku.py abrir netflix` (aceita parte do nome, sem acento) |
| `digitar "<texto>"` | `python roku.py digitar "stranger things"` |
| `status` | `python roku.py status` (energia, app aberto, tocando/pausado, tempo) |
| `tecla <NomeECP> [N]` | `python roku.py tecla Play`, `tecla InputHDMI1`, `tecla Rev` |
| `bluetooth` | veja abaixo |

## Bluetooth

A API ECP **não tem comandos de Bluetooth**. O comando `bluetooth` faz três coisas:

1. Mostra o que a TV informa em `device-info` sobre fone, audição privada e Bluetooth.
2. `python roku.py bluetooth --gravar`: olhando para a TV, você navega pelo terminal (`w a s d`, `ok`, `voltar`) até **Configurações > Controles remotos e dispositivos** e digita `fim`. A sequência fica salva em `config.json`.
3. `python roku.py bluetooth`: repete a sequência salva depois de pedir confirmação (`--passo` pede confirmação a cada tecla). Ele só navega pelos menus e não pareia nada. O pareamento você confirma na própria TV.

Se a TV não tiver Bluetooth para áudio, as alternativas são:

- **Audição privada** no app Roku do celular (fone no celular, som da TV no fone).
- **Controle remoto com saída de fone** (P2), se for compatível com a sua Roku TV.
- **Transmissor Bluetooth externo** ligado à saída **óptica** ou **P2** da TV.

## Erros comuns

- `HTTP 403`: o "Controle por aplicativos móveis" está desativado ou limitado na TV.
- `A TV não respondeu`: o IP mudou ou a TV não está na rede. Rode `python roku.py descobrir`.
- `ligar` falhando com a TV desligada: ative **Configurações > Sistema > Energia > Início rápido da TV** (ou equivalente), senão a placa de rede desliga junto.
