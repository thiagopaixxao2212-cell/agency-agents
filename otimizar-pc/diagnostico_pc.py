#!/usr/bin/env python3
"""Diagnóstico de desempenho do computador (Windows). Só lê; não altera nada.

Uso:  python diagnostico_pc.py [--nome antes]
Gera: resultados/AAAA-MM-DD_HHMM[_nome]/relatorio-pc.md e dados.json
Só biblioteca padrão do Python 3.8+.

Únicas escritas: um arquivo de teste temporário (apagado em seguida) para medir o disco
e o relatório de bateria do próprio Windows (powercfg /batteryreport) dentro da pasta de resultados.
"""
import argparse
import base64
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
WINDOWS = platform.system() == "Windows"

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# Programas que costumam vir de fábrica ou deixar o PC lento. Só servem de alerta para conferir.
SUSPEITOS = {
    r"mcafee": "antivírus de teste que vem de fábrica; pesado",
    r"norton": "antivírus de teste que vem de fábrica; pesado",
    r"avast|avg ": "antivírus gratuito pesado; o Windows Defender já protege",
    r"wildtangent": "jogos de propaganda de fábrica",
    r"booking\.com|amazon assistant": "atalho/propaganda de fábrica",
    r"acer jumpstart|acer portal|acer collection|acer product registration|abfiles|acer explorer": "utilitário Acer pouco útil",
    r"driver booster|driver easy|driverpack|iobit|advanced systemcare|smart defrag": "\"otimizador\" que costuma pesar mais do que ajuda",
    r"pc optimizer|pc cleaner|reimage|restoro|segurazo|wise care|baidu|hao123|ask toolbar|conduit": "programa indesejado/adware comum",
    r"ccleaner": "limpador com histórico de propaganda; o Windows já limpa sozinho",
    r"utorrent|bittorrent": "fica em segundo plano usando internet e disco",
    r"candy crush|farmville|bubble witch|march of empires": "jogo de propaganda do Windows",
}

PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$r = [ordered]@{}
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$bios = Get-CimInstance Win32_BIOS
$r.sistema = [pscustomobject]@{ fabricante=$cs.Manufacturer; modelo=$cs.Model; windows=$os.Caption; versao=$os.Version;
  build=$os.BuildNumber; arquitetura=$os.OSArchitecture; instalado=$os.InstallDate.ToString('s');
  ultimo_boot=$os.LastBootUpTime.ToString('s'); bios=$bios.SMBIOSBIOSVersion }
$r.cpu = [pscustomobject]@{ nome=$cpu.Name.Trim(); nucleos=$cpu.NumberOfCores; threads=$cpu.NumberOfLogicalProcessors; mhz=$cpu.MaxClockSpeed }
$arr = Get-CimInstance Win32_PhysicalMemoryArray
$r.memoria = [pscustomobject]@{ total_gb=[math]::Round($cs.TotalPhysicalMemory/1GB,1);
  livre_gb=[math]::Round($os.FreePhysicalMemory*1KB/1GB,1);
  pentes=@(Get-CimInstance Win32_PhysicalMemory | ForEach-Object { [pscustomobject]@{ gb=[math]::Round($_.Capacity/1GB,1); mhz=$_.Speed; slot=$_.DeviceLocator; fabricante=$_.Manufacturer; codigo=$_.PartNumber } });
  slots=($arr | Measure-Object MemoryDevices -Sum).Sum; max_gb=[math]::Round((($arr | Measure-Object MaxCapacity -Sum).Sum)/1MB,0) }
$r.discos = @(Get-PhysicalDisk | ForEach-Object { [pscustomobject]@{ nome=$_.FriendlyName; tipo="$($_.MediaType)"; barramento="$($_.BusType)"; gb=[math]::Round($_.Size/1GB); saude="$($_.HealthStatus)" } })
$r.discos_modelo = @(Get-CimInstance Win32_DiskDrive | ForEach-Object { [pscustomobject]@{ modelo=$_.Model; gb=[math]::Round($_.Size/1GB); status=$_.Status } })
$r.volumes = @(Get-Volume | Where-Object DriveLetter | ForEach-Object { [pscustomobject]@{ letra="$($_.DriveLetter)"; rotulo=$_.FileSystemLabel; total_gb=[math]::Round($_.Size/1GB,1); livre_gb=[math]::Round($_.SizeRemaining/1GB,1) } })
$r.video = @(Get-CimInstance Win32_VideoController | ForEach-Object { [pscustomobject]@{ nome=$_.Name; driver=$_.DriverVersion; data=$(if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') }) } })
$r.inicializacao = @(Get-CimInstance Win32_StartupCommand | ForEach-Object { [pscustomobject]@{ nome=$_.Name; comando=$_.Command; local=$_.Location; usuario=$_.User } })
$r.processos = @(Get-Process | Group-Object ProcessName | ForEach-Object { [pscustomobject]@{ nome=$_.Name; qtd=$_.Count;
  mem_mb=[math]::Round(($_.Group | Measure-Object WorkingSet64 -Sum).Sum/1MB); cpu_s=[math]::Round(($_.Group | Measure-Object CPU -Sum).Sum) } } |
  Sort-Object mem_mb -Descending | Select-Object -First 15)
$r.carga = @(1..8 | ForEach-Object {
  $p = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
  $d = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'"
  $m = Get-CimInstance Win32_OperatingSystem
  [pscustomobject]@{ cpu=[int]$p.PercentProcessorTime; disco=[math]::Min(100,[int]$d.PercentDiskTime); fila=[int]$d.CurrentDiskQueueLength; mem_livre_mb=[math]::Round($m.FreePhysicalMemory/1KB) }
  Start-Sleep -Milliseconds 800 })
$r.servicos_automaticos_rodando = (Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State='Running'" | Measure-Object).Count
$r.antivirus = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object { $_.displayName })
$r.pagefile = @(Get-CimInstance Win32_PageFileUsage | ForEach-Object { [pscustomobject]@{ arquivo=$_.Name; tamanho_mb=$_.AllocatedBaseSize; uso_atual_mb=$_.CurrentUsage; pico_mb=$_.PeakUsage } })
$hf = Get-HotFix | Where-Object InstalledOn | Sort-Object InstalledOn | Select-Object -Last 1
$r.ultima_atualizacao = $(if ($hf) { [pscustomobject]@{ kb=$hf.HotFixID; data=$hf.InstalledOn.ToString('yyyy-MM-dd') } })
$r.plano_energia = (powercfg /getactivescheme | Out-String).Trim()
$r | ConvertTo-Json -Depth 5
"""


def rodar(cmd, timeout=180):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=0x08000000 if WINDOWS else 0)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"({e})"
    saida = p.stdout + p.stderr
    for enc in ("utf-8", "oem", "cp850", "latin-1"):
        try:
            return saida.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass
    return saida.decode("utf-8", "replace")


def powershell_json(script):
    cod = base64.b64encode(("[Console]::OutputEncoding=[Text.Encoding]::UTF8\n" + script).encode("utf-16-le")).decode()
    txt = rodar(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", cod], 300)
    inicio = txt.find("{")
    if inicio < 0:
        raise RuntimeError("PowerShell não retornou dados: " + txt[:300])
    return json.loads(txt[inicio:txt.rfind("}") + 1])


def lista(x):
    return [] if x is None else x if isinstance(x, list) else [x]


# ------------------------------------------------------------------ coletas extras (Python)

def programas_instalados():
    import winreg
    vistos, saida = set(), []
    chaves = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
              (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for raiz, caminho in chaves:
        try:
            k = winreg.OpenKey(raiz, caminho)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(k)[0]):
            try:
                sub = winreg.OpenKey(k, winreg.EnumKey(k, i))
            except OSError:
                continue

            def val(n):
                try:
                    return winreg.QueryValueEx(sub, n)[0]
                except OSError:
                    return None
            nome = val("DisplayName")
            if not nome or val("SystemComponent") == 1 or val("ParentKeyName") or nome in vistos:
                continue
            vistos.add(nome)
            saida.append({"nome": nome, "editor": val("Publisher") or "", "mb": round((val("EstimatedSize") or 0) / 1024),
                          "instalado": val("InstallDate") or ""})
    return sorted(saida, key=lambda p: p["nome"].lower())


def estado_inicializacao():
    """Lê StartupApproved (o que o Gerenciador de Tarefas mostra como Habilitado/Desabilitado)."""
    import winreg
    estado = {}
    base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
    for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in ("Run", "Run32", "StartupFolder"):
            try:
                k = winreg.OpenKey(raiz, base + "\\" + sub)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(k)[1]):
                nome, dado, _ = winreg.EnumValue(k, i)
                if isinstance(dado, bytes) and dado:
                    estado[nome.lower()] = dado[0] % 2 == 0  # 02/06 = habilitado, 03/07 = desabilitado
    return estado


def tamanho_pasta(caminho, limite_s=40):
    total, fim = 0, time.time() + limite_s
    for raiz, _, arquivos in os.walk(caminho, onerror=lambda e: None):
        for a in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, a))
            except OSError:
                pass
        if time.time() > fim:
            break
    return round(total / 1024 ** 3, 2)


def teste_disco():
    """Escrita sequencial de 256 MB e 300 arquivos pequenos com fsync, no disco do Windows. Apaga tudo no fim."""
    pasta = Path(tempfile.mkdtemp(prefix="teste-disco-"))
    try:
        bloco = os.urandom(4 * 1024 * 1024)
        t0 = time.perf_counter()
        with open(pasta / "grande.bin", "wb") as f:
            for _ in range(64):
                f.write(bloco)
            f.flush()
            os.fsync(f.fileno())
        seq = 256 / (time.perf_counter() - t0)
        peq = os.urandom(4096)
        t0 = time.perf_counter()
        for i in range(300):
            with open(pasta / f"p{i}.bin", "wb") as f:
                f.write(peq)
                f.flush()
                os.fsync(f.fileno())
        ms_por_arquivo = (time.perf_counter() - t0) * 1000 / 300
        return {"escrita_seq_mb_s": round(seq), "ms_por_arquivo_pequeno": round(ms_por_arquivo, 2)}
    finally:
        for p in pasta.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            pasta.rmdir()
        except OSError:
            pass


def bateria(saida):
    arq = saida / "bateria.xml"
    rodar(["powercfg", "/batteryreport", "/xml", "/output", str(arq)], 60)
    if not arq.exists():
        return None
    raiz = ET.parse(arq).getroot()
    proj = next((e.text for e in raiz.iter() if e.tag.endswith("DesignCapacity")), None)
    atual = next((e.text for e in raiz.iter() if e.tag.endswith("FullChargeCapacity")), None)
    if not proj or not atual or int(proj) == 0:
        return None
    return {"projetada_mwh": int(proj), "atual_mwh": int(atual), "saude_pct": round(100 * int(atual) / int(proj))}


# ------------------------------------------------------------------ análise

def analisar(d):
    p = []  # (gravidade, texto, recomendação)
    discos = lista(d.get("discos"))
    disco_teste = d.get("teste_disco") or {}
    hdd = any(x.get("tipo") == "HDD" for x in discos) or \
        (disco_teste.get("ms_por_arquivo_pequeno", 0) > 4 and not any(x.get("tipo") == "SSD" for x in discos))
    if hdd:
        p.append((3, f"O Windows está num **HD mecânico** (teste: {disco_teste.get('escrita_seq_mb_s', '?')} MB/s, "
                     f"{disco_teste.get('ms_por_arquivo_pequeno', '?')} ms por arquivo pequeno). É a maior causa de lentidão "
                     "em notebooks dessa idade: ligar, abrir programas e atualizar o Windows ficam lentos.",
                  "Trocar o HD por um **SSD SATA 2,5\"** (240–500 GB) e clonar o sistema. É a melhoria que mais faz diferença."))
    mem = d.get("memoria") or {}
    carga = lista(d.get("carga"))
    total = mem.get("total_gb") or 0
    livre_media = sum(c["mem_livre_mb"] for c in carga) / len(carga) / 1024 if carga else mem.get("livre_gb", 0)
    uso = 100 * (1 - livre_media / total) if total else 0
    if total and total <= 4.5:
        p.append((3 if uso > 75 else 2, f"Só **{total} GB de RAM** (uso agora: {uso:.0f}%). Com navegador aberto, o Windows 10 passa a usar o disco como memória.",
                  f"Adicionar um pente de memória (slots: {mem.get('slots', '?')}, ocupados: {len(lista(mem.get('pentes')))}, "
                  f"máximo suportado: {mem.get('max_gb', '?')} GB). 8 GB no total já ajuda muito."))
    elif uso > 85:
        p.append((2, f"Memória quase cheia ({uso:.0f}% em uso).", "Fechar programas pesados (veja a lista de processos)."))
    if carga:
        disco_medio = sum(c["disco"] for c in carga) / len(carga)
        cpu_media = sum(c["cpu"] for c in carga) / len(carga)
        if disco_medio > 70:
            p.append((3, f"Disco ocupado {disco_medio:.0f}% do tempo **com o PC parado**.",
                      "Ver o que está usando o disco (atualização do Windows, antivírus, indexação) e reduzir a inicialização."))
        if cpu_media > 60:
            p.append((2, f"Processador ocupado {cpu_media:.0f}% com o PC parado.", "Ver os processos que mais usam CPU."))
    for v in lista(d.get("volumes")):
        if v.get("letra") == "C" and v.get("total_gb"):
            pct = 100 * v["livre_gb"] / v["total_gb"]
            if v["livre_gb"] < 20 or (pct < 10 and v["livre_gb"] < 50):
                p.append((2, f"Pouco espaço livre no C: ({v['livre_gb']} GB, {pct:.0f}%).", "Limpeza de disco e remover programas sem uso."))
    ini = [x for x in lista(d.get("inicializacao")) if x.get("habilitado", True)]
    if len(ini) > 6:
        p.append((2, f"**{len(ini)} programas** abrem junto com o Windows.", "Desativar na inicialização os que você não usa todo dia (Gerenciador de Tarefas > Inicializar)."))
    av = lista(d.get("antivirus"))
    if len([a for a in av if "defender" not in a.lower()]) >= 1 and len(av) > 1:
        p.append((2, f"Mais de um antivírus registrado: {', '.join(av)}.", "Manter só um. O Windows Defender é suficiente para uso doméstico."))
    susp = d.get("programas_suspeitos") or []
    if susp:
        p.append((1, f"{len(susp)} programa(s) de fábrica/indesejados: " + ", ".join(s["nome"] for s in susp[:8]) + ".",
                  "Desinstalar os que você não usa (com a sua aprovação, um por vez)."))
    temp = d.get("temporarios") or {}
    lixo = sum(v for v in temp.values() if isinstance(v, (int, float)))
    if lixo > 2:
        p.append((1, f"{lixo:.1f} GB em arquivos temporários.", "Limpeza de Disco do Windows / Sensor de Armazenamento."))
    plano = d.get("plano_energia", "")
    if re.search(r"econom|saver", plano, re.I):
        p.append((1, "Plano de energia em **Economia de energia**, que deixa o processador mais lento.", "Mudar para Equilibrado."))
    try:
        boot = datetime.fromisoformat(d["sistema"]["ultimo_boot"])
        dias = (datetime.now() - boot).days
        if dias >= 7:
            p.append((1, f"O PC não é reiniciado de verdade há {dias} dias (desligar no Windows 10 não zera tudo).",
                      "Usar **Reiniciar** pelo menos uma vez por semana."))
    except Exception:
        pass
    b = d.get("bateria")
    if b and b["saude_pct"] < 60:
        p.append((1, f"Bateria com {b['saude_pct']}% da capacidade original.", "Bateria gasta; não afeta a velocidade na tomada."))
    win = (d.get("sistema") or {}).get("windows", "")
    if "Windows 10" in win:
        p.append((1, "Windows 10 não recebe mais atualizações de segurança gratuitas (suporte encerrado em out/2025).",
                  "Avaliar Windows 11 (se o processador for compatível) ou manter com cuidado redobrado."))
    return sorted(p, key=lambda x: -x[0])


def tabela(cab, linhas):
    return "\n".join(["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)] +
                     ["| " + " | ".join("" if x is None else str(x) for x in l) + " |" for l in linhas])


def relatorio(d):
    s, cpu, mem = d.get("sistema") or {}, d.get("cpu") or {}, d.get("memoria") or {}
    g = {3: "🔴 grave", 2: "🟠 médio", 1: "🟡 leve"}
    L = [f"# Diagnóstico do computador — {d['quando']}", "",
         f"**{s.get('fabricante', '')} {s.get('modelo', '')}** · {s.get('windows', '')} (build {s.get('build', '')}) · "
         f"instalado em {s.get('instalado', '?')[:10]} · último boot {s.get('ultimo_boot', '?')[:16]}",
         f"Processador: {cpu.get('nome')} ({cpu.get('nucleos')} núcleos / {cpu.get('threads')} threads)",
         f"Memória: {mem.get('total_gb')} GB — pentes: " +
         ", ".join(f"{p['gb']} GB {p.get('mhz') or ''}MHz ({p.get('slot')})" for p in lista(mem.get("pentes"))) +
         f" — slots: {mem.get('slots')} — máximo: {mem.get('max_gb')} GB", "",
         "## Problemas e recomendações (do mais grave ao menos grave)", ""]
    for i, (gv, txt, rec) in enumerate(analisar(d), 1):
        L += [f"{i}. {g[gv]} — {txt}", f"   - **Sugestão:** {rec}"]
    L += ["", "## Discos", "",
          tabela(["Disco", "Tipo", "Barramento", "GB", "Saúde"],
                 [[x.get("nome"), x.get("tipo"), x.get("barramento"), x.get("gb"), x.get("saude")] for x in lista(d.get("discos"))]),
          "", tabela(["Unidade", "Total (GB)", "Livre (GB)"],
                     [[x.get("letra") + ":", x.get("total_gb"), x.get("livre_gb")] for x in lista(d.get("volumes"))]),
          "", f"Teste de disco: {d.get('teste_disco')}"]
    carga = lista(d.get("carga"))
    if carga:
        L += ["", "## Carga com o PC parado (8 amostras)", "",
              tabela(["CPU %", "Disco %", "Fila do disco", "RAM livre (MB)"], [[c["cpu"], c["disco"], c["fila"], c["mem_livre_mb"]] for c in carga])]
    L += ["", "## Processos que mais usam memória", "",
          tabela(["Processo", "Instâncias", "RAM (MB)", "CPU (s)"],
                 [[x["nome"], x["qtd"], x["mem_mb"], x["cpu_s"]] for x in lista(d.get("processos"))]),
          "", "## Programas que abrem com o Windows", "",
          tabela(["Nome", "Habilitado", "Comando"],
                 [[x.get("nome"), "sim" if x.get("habilitado", True) else "não", (x.get("comando") or "")[:80]] for x in lista(d.get("inicializacao"))])]
    if d.get("programas_suspeitos"):
        L += ["", "## Programas para conferir", "",
              tabela(["Programa", "Por quê", "MB"], [[x["nome"], x["motivo"], x["mb"]] for x in d["programas_suspeitos"]])]
    L += ["", "## Outros", "",
          f"- Antivírus: {', '.join(lista(d.get('antivirus'))) or '?'}",
          f"- Serviços automáticos rodando: {d.get('servicos_automaticos_rodando')}",
          f"- Arquivos temporários (GB): {d.get('temporarios')}",
          f"- Última atualização instalada: {d.get('ultima_atualizacao')}",
          f"- Plano de energia: {d.get('plano_energia')}",
          f"- Placa de vídeo: {', '.join(v.get('nome', '') + ' (driver ' + str(v.get('data')) + ')' for v in lista(d.get('video')))}",
          f"- Bateria: {d.get('bateria') or 'sem dados'}",
          f"- Programas instalados: {len(d.get('programas', []))} (lista completa em dados.json)"]
    if d["erros"]:
        L += ["", "## Etapas com erro", ""] + [f"- {e}" for e in d["erros"]]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ main

def etapa(d, nome, func):
    print(f"== {nome} ...", flush=True)
    try:
        return func()
    except Exception as e:
        d["erros"].append(f"{nome}: {type(e).__name__}: {e}")
        print(f"   falhou: {e}")
        return None


def main():
    ap = argparse.ArgumentParser(description="Diagnóstico do computador (só leitura).")
    ap.add_argument("--nome", default="", help="rótulo, ex.: antes, depois-inicializacao")
    args = ap.parse_args()
    if not WINDOWS:
        print("Este diagnóstico é para Windows.")
        return 1
    saida = PASTA / "resultados" / (datetime.now().strftime("%Y-%m-%d_%H%M") + (f"_{args.nome}" if args.nome else ""))
    saida.mkdir(parents=True, exist_ok=True)
    d = {"quando": datetime.now().isoformat(timespec="seconds"), "rotulo": args.nome, "erros": []}
    print("Diagnóstico do computador: só leitura, nada é alterado. Leva ~2 minutos.\n")
    d.update(etapa(d, "Hardware, inicialização, processos e carga (PowerShell)", lambda: powershell_json(PS)) or {})
    estado = etapa(d, "Estado dos programas de inicialização", estado_inicializacao) or {}
    for x in lista(d.get("inicializacao")):
        nome = (x.get("nome") or "").lower()
        x["habilitado"] = estado.get(nome, estado.get(nome + ".lnk", True))
    progs = etapa(d, "Programas instalados", programas_instalados) or []
    d["programas"] = progs
    d["programas_suspeitos"] = [dict(p, motivo=m) for p in progs for padrao, m in SUSPEITOS.items()
                                if re.search(padrao, p["nome"], re.I)]
    windir = os.environ.get("WINDIR", r"C:\Windows")
    d["temporarios"] = etapa(d, "Arquivos temporários", lambda: {
        "temp_usuario": tamanho_pasta(os.environ.get("TEMP", "")),
        "temp_windows": tamanho_pasta(os.path.join(windir, "Temp")),
        "downloads_windows_update": tamanho_pasta(os.path.join(windir, "SoftwareDistribution", "Download"))})
    d["teste_disco"] = etapa(d, "Teste de velocidade do disco (arquivo temporário, apagado no fim)", teste_disco)
    d["bateria"] = etapa(d, "Saúde da bateria", lambda: bateria(saida))
    (saida / "dados.json").write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    texto = relatorio(d)
    (saida / "relatorio-pc.md").write_text(texto, encoding="utf-8")
    print("\n" + texto.split("## Discos")[0])
    print(f"Relatório completo: {saida / 'relatorio-pc.md'}")
    print(f"Para me enviar:  notepad \"{saida / 'relatorio-pc.md'}\"  (Ctrl+A, Ctrl+C e cole no chat)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
