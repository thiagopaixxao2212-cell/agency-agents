# otimizar-pc

Diagnóstico e otimização do notebook (Acer Aspire ES 15, Windows 10).

## Passo 1: diagnóstico (só leitura)

1. Salve `diagnostico_pc.py` numa pasta, por exemplo `Desktop\otimizar-pc`.
2. Abra a pasta no Explorador, digite `cmd` na barra de endereço e aperte Enter.
3. Rode:

```
python diagnostico_pc.py --nome antes
```

Leva cerca de 2 minutos. Deixe o PC parado enquanto roda (sem abrir programas).
No fim, ele mostra o comando `notepad ...` para abrir o relatório e copiar para o chat.

O que ele olha: processador, memória (pentes e slots livres), tipo e saúde do disco (HD ou SSD),
espaço livre, uso de CPU/disco/RAM com o PC parado, programas que abrem com o Windows,
processos mais pesados, antivírus instalados, programas de fábrica/indesejados, arquivos temporários,
plano de energia, última atualização do Windows, driver de vídeo e saúde da bateria.

Não altera nada. As únicas escritas são um arquivo de teste de disco (apagado em seguida)
e o relatório de bateria do Windows, dentro de `resultados\`.

## Passo 2: otimização

Cada mudança é proposta com motivo e risco, aplicada **uma por vez** depois do seu "pode",
e medida de novo (`python diagnostico_pc.py --nome depois-<mudança>`) para comparar.
O histórico fica em `relatorio.md`.
