# Relatório da rede de casa

Rede **Twibi Thiago** (192.168.5.x). Medições feitas com `diagnostico.py` no computador DESKTOP-2B0IVL8.

## 1. Diagnóstico inicial ("antes") — 17/11/2026 23:58, computador no cabo

### O que está bom

| Item | Resultado | Avaliação |
|---|---|---|
| Rede interna (PC → roteador, cabo) | 1 ms, jitter 0, perda 0% | perfeita |
| Internet (até 1.1.1.1 e 8.8.8.8) | ~30 ms, jitter < 1 ms, perda 0% | estável, ótima para chamadas |
| DNS atual (o próprio roteador) | 6,7 ms | **o mais rápido**; Cloudflare 34,6 · Quad9 25,5 · Google 38,3 ms. Não vale trocar. |
| Velocidade no cabo | 79,2 Mbps ↓ / 81,7 Mbps ↑ | depende do plano contratado (a confirmar) |

### Problemas, do mais grave ao menos grave

1. **🟠 Três roteadores em cascata (NAT triplo).** O traceroute passa por
   `192.168.5.1` (Twibi) → `192.168.0.1` (outro roteador) → `192.168.1.254` (modem/ONU da operadora) → operadora,
   que ainda usa CGNAT (endereços `100.122.x.x`).
   A velocidade quase não sofre (cada salto soma ~1 ms), mas isso costuma causar:
   NAT "estrito" em jogos e videogames, falhas em algumas chamadas/VPN, redirecionamento de portas impossível
   e **mais de um Wi-Fi competindo no mesmo espaço** (veja o item 2).
2. **🟠 Wi-Fi 2,4 GHz no canal mais congestionado.** A "Twibi Thiago" está no **canal 6**, o mais disputado
   (índice 5,9). O canal **11** (3,58) e o **1** (3,92) estão mais livres. Além disso, há redes muito fortes
   que provavelmente são da própria casa e se sobrepõem: **"Thiago"** no canal 2 (96%) e uma rede **oculta** no canal 7 (100%).
   São 20 redes vizinhas em 2,4 GHz.
3. **🟡 Bufferbloat nota B (+52 ms).** Com a internet ocupada (download/upload), a latência sobe de 30 para ~82 ms.
   Chamadas e jogos podem engasgar quando alguém está baixando ou assistindo algo pesado. QoS no roteador, se existir, ajuda.
4. **🟡 Velocidade a confirmar.** 79/82 Mbps no cabo. Se o plano for de 100 Mbps, está normal. Se for maior,
   o gargalo pode ser a porta de rede do computador (muitos notebooks antigos têm porta de 100 Mbps).
5. **ℹ️ A placa Wi-Fi deste computador só enxerga 2,4 GHz.** Nenhuma rede de 5 GHz apareceu na varredura,
   nem as dos vizinhos. No Wi-Fi, este PC nunca vai usar a faixa rápida; no cabo isso não importa.
6. **ℹ️ Dispositivos.** Apareceram 5: roteador, TV Roku (192.168.5.199), este PC (192.168.5.200),
   um aparelho `38:a5:c9:29:74:96` (192.168.5.248) e um celular com MAC aleatório (192.168.5.249).
   A lista completa e o consumo por aparelho ficam no app do Twibi.

## 2. Mudanças propostas (aguardando aprovação, uma por vez)

| # | Mudança | Motivo | Risco |
|---|---|---|---|
| A | Canal 2,4 GHz do Twibi: 6 → 11 (largura 20 MHz) | canal menos congestionado | baixo; os aparelhos reconectam em segundos |
| B | Desligar o Wi-Fi do roteador do meio ("Thiago"), se for de vocês | tira interferência da própria casa | baixo; aparelhos ligados nele precisam passar para a "Twibi Thiago" |
| C | Simplificar a cascata de roteadores (roteador do meio em modo bridge/AP, ou removê-lo) | resolve NAT triplo | **médio/alto**: mexe na configuração de internet; pode deixar a casa sem internet até corrigir |
| D | QoS / priorização no Twibi, se existir | reduz bufferbloat | baixo |
| — | Trocar DNS | **não recomendado**: o atual é o mais rápido | — |

## 3. Antes e depois

_(preenchido a cada mudança aprovada)_

## 4. Como desfazer

_(preenchido a cada mudança aprovada)_
