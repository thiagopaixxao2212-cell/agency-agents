# diagnostico-rede

Diagnóstico da rede de casa (Twibi, 192.168.5.x). **Só mede e lê, não muda nada** no roteador nem no computador.
Precisa de Windows e Python 3.8+ (o mesmo do `roku-control`), sem `pip install`.

## Como rodar

1. Salve `diagnostico.py` numa pasta, por exemplo `Desktop\diagnostico-rede`.
2. Abra a pasta no Explorador, digite `cmd` na barra de endereço e aperte Enter.
3. Rode (demora ~4 minutos; não use a internet pesado nesse tempo):

```
python diagnostico.py --nome antes
```

O resultado fica em `resultados\<data>_antes\`:
- `relatorio-diagnostico.md`: o resumo, com os problemas do mais grave ao menos grave
- `dados.json`: os números
- `brutos\`: a saída original de cada comando (ping, tracert, netsh, arp)

## Variações

| Comando | Para quê |
|---|---|
| `python diagnostico.py --nome cabo` | rodar de novo com o computador no cabo, para comparar com o Wi-Fi |
| `python diagnostico.py --rapido` | pula o teste de velocidade (mais rápido) |
| `python diagnostico.py --nome depois-canal` | medir depois de cada mudança, para comparar com o "antes" |

## O que é medido

- IP do roteador (gateway), DNS em uso e se a conexão é Wi-Fi ou cabo (sinal, canal, banda, taxa)
- Ping para o roteador, 1.1.1.1 e 8.8.8.8: latência, jitter e perda (30 pacotes)
- Traceroute até 1.1.1.1
- Download/upload (servidor da Cloudflare) e **bufferbloat**: quanto a latência sobe com a rede ocupada
- Redes Wi-Fi vizinhas (canais 2,4 e 5 GHz, sinal) e os nós da sua mesh visíveis daqui
- Dispositivos na rede (IP, MAC, fabricante, nome); MAC aleatório = celular com privacidade de Wi-Fi ativada
- Velocidade dos DNS: o atual vs. Cloudflare, Google e Quad9

O que **não** dá para medir do computador: consumo de banda por aparelho. Isso aparece no app do Twibi.

## Privacidade

Os testes acessam só `speed.cloudflare.com` (velocidade), `wireshark.org` (lista pública de fabricantes) e os servidores de DNS testados.
Nenhum IP, MAC ou nome dos seus aparelhos é enviado para fora. A pasta `resultados\` fica só no seu computador.
