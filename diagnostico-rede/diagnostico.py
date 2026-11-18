#!/usr/bin/env python3
"""Diagnóstico da rede doméstica (Windows). Só mede e lê; não altera nada.

Uso:  python diagnostico.py            (diagnóstico completo, ~4 min)
      python diagnostico.py --rapido   (sem teste de velocidade)
Gera: resultados/AAAA-MM-DD_HHMM/relatorio-diagnostico.md, dados.json e brutos/*.txt
Só biblioteca padrão do Python 3.8+.
"""
import argparse
import ipaddress
import json
import os
import platform
import random
import re
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
WINDOWS = platform.system() == "Windows"
ALVOS_PING = ["1.1.1.1", "8.8.8.8"]
DNS_PUBLICOS = {"Cloudflare": "1.1.1.1", "Google": "8.8.8.8", "Quad9": "9.9.9.9"}
DOMINIOS = ["google.com", "youtube.com", "netflix.com", "globo.com", "uol.com.br",
            "instagram.com", "whatsapp.net", "roku.com", "mercadolivre.com.br", "microsoft.com"]
UA = {"User-Agent": "diagnostico-rede/1.0"}
CF_DOWN = "https://speed.cloudflare.com/__down?bytes={}"
CF_UP = "https://speed.cloudflare.com/__up"
MANUF_URL = "https://www.wireshark.org/download/automated/data/manuf"

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


# ------------------------------------------------------------------ util

def decodificar(b):
    for enc in (("oem",) if WINDOWS else ()) + ("utf-8", "cp850", "latin-1"):
        try:
            return b.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return b.decode("utf-8", "replace")


def rodar(cmd, timeout=120):
    flags = 0x08000000 if WINDOWS else 0  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
        return decodificar(p.stdout) + decodificar(p.stderr)
    except FileNotFoundError:
        return f"(comando não encontrado: {cmd[0]})"
    except subprocess.TimeoutExpired:
        return "(tempo esgotado)"


class Coleta:
    def __init__(self, saida):
        self.saida = saida
        (saida / "brutos").mkdir(parents=True, exist_ok=True)
        self.dados = {"quando": datetime.now().isoformat(timespec="seconds"),
                      "computador": platform.node(), "sistema": platform.platform(), "erros": []}

    def bruto(self, nome, texto):
        (self.saida / "brutos" / f"{nome}.txt").write_text(texto, encoding="utf-8")
        return texto

    def etapa(self, nome, func, *a):
        print(f"\n== {nome} ...", flush=True)
        t0 = time.time()
        try:
            r = func(self, *a)
            print(f"   ok ({time.time() - t0:.0f}s)")
            return r
        except Exception as e:
            msg = f"{nome}: {type(e).__name__}: {e}"
            print(f"   FALHOU: {msg}")
            self.dados["erros"].append(msg)
            return None


# ------------------------------------------------------------------ 1. conexão / gateway

PS_REDE = (r"Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | ForEach-Object { "
           r"[pscustomobject]@{ interface=$_.InterfaceAlias; descricao=$_.InterfaceDescription; "
           r"ip=($_.IPv4Address | Select-Object -First 1).IPAddress; "
           r"prefixo=($_.IPv4Address | Select-Object -First 1).PrefixLength; "
           r"gateway=($_.IPv4DefaultGateway | Select-Object -First 1).NextHop; "
           r"dns=@($_.DNSServer | Where-Object { $_.AddressFamily -eq 2 } | ForEach-Object { $_.ServerAddresses }) } "
           r"} | ConvertTo-Json -Depth 3")


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    finally:
        s.close()


def etapa_conexao(c):
    info = {}
    if WINDOWS:
        txt = c.bruto("powershell-rede", rodar(["powershell", "-NoProfile", "-Command", PS_REDE], 60))
        try:
            inicio = min(i for i in (txt.find("["), txt.find("{")) if i >= 0)
            j = json.loads(txt[inicio:])
            j = j if isinstance(j, list) else [j]
            info = j[0]
            if len(j) > 1:
                info["outras_interfaces"] = j[1:]
        except ValueError:
            c.dados["erros"].append("PowerShell não retornou a configuração de rede")
        c.bruto("ipconfig", rodar(["ipconfig", "/all"]))
        wl = c.bruto("wlan-interfaces", rodar(["netsh", "wlan", "show", "interfaces"]))
        info["wifi"] = parse_wlan_interface(wl)
    if not info.get("ip"):
        info["ip"] = ip_local()
    if not info.get("gateway"):
        info["gateway"] = str(ipaddress.ip_network(f"{info['ip']}/24", strict=False)[1])
        info["gateway_suposto"] = True
    info["prefixo"] = int(info.get("prefixo") or 24)
    dns = info.get("dns") or []
    info["dns"] = [dns] if isinstance(dns, str) else dns
    w = info.get("wifi") or {}
    info["tipo"] = "Wi-Fi" if w.get("conectado") else "cabo (ou outra)"
    c.dados["conexao"] = info
    print(f"   IP {info['ip']}  gateway {info['gateway']}  DNS {', '.join(info['dns']) or '?'}  via {info['tipo']}")
    if w.get("conectado"):
        print(f"   Wi-Fi: {w.get('ssid')} canal {w.get('canal')} ({w.get('banda')}) sinal {w.get('sinal')}% "
              f"taxa {w.get('taxa_rx')}/{w.get('taxa_tx')} Mbps")


CHAVES_WLAN = {
    "ssid": r"^SSID$", "bssid": r"^BSSID$", "estado": r"^(Estado|State)$",
    "canal": r"^(Canal|Channel)$", "sinal": r"^(Sinal|Signal)$", "radio": r"^(Tipo de r.dio|Radio type)$",
    "banda": r"^(Banda|Band)$", "taxa_rx": r"^(Taxa de recep|Receive rate)", "taxa_tx": r"^(Taxa de transmiss|Transmit rate)",
    "autenticacao": r"^(Autentica|Authentication)",
}


def pares(texto):
    for linha in texto.splitlines():
        m = re.match(r"^\s*(.+?)\s+:(?:\s+(.*))?$", linha)
        if m:
            yield m.group(1).strip(), (m.group(2) or "").strip()


def parse_wlan_interface(texto):
    r = {}
    for k, v in pares(texto):
        for nome, padrao in CHAVES_WLAN.items():
            if nome not in r and re.search(padrao, k, re.I):
                r[nome] = v
    r["conectado"] = bool(r.get("bssid")) and bool(re.search(r"conectad|connected", r.get("estado", ""), re.I))
    normalizar_radio(r)
    return r


def normalizar_radio(r):
    if "sinal" in r:
        r["sinal"] = int(re.sub(r"\D", "", r["sinal"]) or 0)
        r["dbm_aprox"] = r["sinal"] // 2 - 100
    if "canal" in r:
        r["canal"] = int(re.sub(r"\D", "", r["canal"]) or 0)
        if not r.get("banda") and r["canal"]:
            r["banda"] = "2,4 GHz" if r["canal"] <= 14 else "5 GHz"
    for k in ("taxa_rx", "taxa_tx"):
        if k in r:
            r[k] = r[k].replace(",", ".")


# ------------------------------------------------------------------ 2. ping, jitter, traceroute

def ping(host, n=20, intervalo_ms=1000):
    cmd = ["ping", "-n", str(n), "-w", str(intervalo_ms), host] if WINDOWS else ["ping", "-c", str(n), "-W", "1", host]
    txt = rodar(cmd, timeout=n * 3 + 10)
    tempos = [float(x.replace(",", ".")) for x in re.findall(r"(?:tempo|time)\s*[=<]\s*(\d+(?:[.,]\d+)?)\s*ms", txt, re.I)]
    respostas = len(re.findall(r"TTL=", txt, re.I)) if WINDOWS else len(tempos)
    return txt, resumo_ping(tempos, n, respostas)


def resumo_ping(tempos, enviados, respostas):
    r = {"enviados": enviados, "recebidos": respostas,
         "perda_pct": round(100 * (enviados - respostas) / enviados, 1) if enviados else None}
    if tempos:
        r.update(min=min(tempos), media=round(statistics.mean(tempos), 1), max=max(tempos),
                 mediana=statistics.median(tempos),
                 jitter=round(statistics.mean(abs(a - b) for a, b in zip(tempos, tempos[1:])), 1) if len(tempos) > 1 else 0)
    return r


def etapa_ping(c):
    alvos = [c.dados["conexao"]["gateway"]] + ALVOS_PING
    res = {}
    with ThreadPoolExecutor(3) as pool:
        futs = {h: pool.submit(ping, h, 30) for h in alvos}
    for h, f in futs.items():
        txt, r = f.result()
        c.bruto(f"ping-{h}", txt)
        res[h] = r
        print(f"   {h:>15}: média {r.get('media', '?')} ms, jitter {r.get('jitter', '?')} ms, perda {r['perda_pct']}%")
    c.dados["ping"] = res


def etapa_traceroute(c):
    cmd = ["tracert", "-d", "-h", "15", "-w", "800", "1.1.1.1"] if WINDOWS else ["traceroute", "-n", "-m", "15", "1.1.1.1"]
    txt = c.bruto("traceroute-1.1.1.1", rodar(cmd, 180))
    saltos = []
    for linha in txt.splitlines():
        m = re.match(r"^\s*(\d+)\s+(.*)$", linha)
        if m and int(m.group(1)) <= 30:
            ms = [int(x) for x in re.findall(r"<?(\d+)\s*ms", m.group(2))]
            ip = re.findall(r"(\d+\.\d+\.\d+\.\d+)", m.group(2))
            saltos.append({"salto": int(m.group(1)), "ip": ip[-1] if ip else "*", "ms": ms})
    c.dados["traceroute"] = saltos
    for s in saltos:
        print(f"   {s['salto']:>2}  {s['ip']:<16} {s['ms']}")


# ------------------------------------------------------------------ 3. velocidade + bufferbloat

def medir_fluxo(func, segundos=10, fluxos=4):
    total = [0]
    trava = threading.Lock()
    fim = time.time() + segundos
    erros = []

    def trabalhador():
        while time.time() < fim:
            try:
                n = func(fim)
            except Exception as e:
                erros.append(str(e))
                time.sleep(0.5)
                if len(erros) > 20:
                    return
                continue
            with trava:
                total[0] += n

    t0 = time.time()
    ts = [threading.Thread(target=trabalhador, daemon=True) for _ in range(fluxos)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(segundos + 30)
    dur = time.time() - t0
    return round(total[0] * 8 / dur / 1e6, 1), erros[:3]


def baixar(fim):
    req = urllib.request.Request(CF_DOWN.format(25_000_000), headers=UA)
    n = 0
    with urllib.request.urlopen(req, timeout=15) as r:
        while time.time() < fim:
            bloco = r.read(65536)
            if not bloco:
                break
            n += len(bloco)
    return n


DADOS_UP = os.urandom(2_000_000)


def enviar(fim):
    req = urllib.request.Request(CF_UP, data=DADOS_UP, headers={**UA, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()
    return len(DADOS_UP)


def com_ping(func, segundos):
    resultado = {}
    th = threading.Thread(target=lambda: resultado.update(ping=ping("1.1.1.1", segundos)), daemon=True)
    th.start()
    time.sleep(0.5)
    mbps, erros = medir_fluxo(func, segundos)
    th.join(segundos + 15)
    return mbps, erros, resultado.get("ping", ("", {}))


def nota_bufferbloat(acrescimo):
    for limite, nota in ((5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")):
        if acrescimo < limite:
            return nota
    return "F"


def etapa_velocidade(c):
    txt, ocioso = ping("1.1.1.1", 10)
    c.bruto("ping-ocioso", txt)
    print(f"   latência ociosa: {ocioso.get('media', '?')} ms")
    down, e1, (t1, p_down) = com_ping(baixar, 12)
    c.bruto("ping-durante-download", t1)
    print(f"   download: {down} Mbps   latência durante: {p_down.get('media', '?')} ms")
    up, e2, (t2, p_up) = com_ping(enviar, 12)
    c.bruto("ping-durante-upload", t2)
    print(f"   upload:   {up} Mbps   latência durante: {p_up.get('media', '?')} ms")
    base = ocioso.get("media") or 0
    pior = max(p_down.get("media") or 0, p_up.get("media") or 0)
    r = {"servidor": "speed.cloudflare.com", "download_mbps": down, "upload_mbps": up,
         "latencia_ociosa": ocioso, "latencia_download": p_down, "latencia_upload": p_up,
         "acrescimo_sob_carga_ms": round(pior - base, 1) if base and pior else None, "erros": e1 + e2}
    if r["acrescimo_sob_carga_ms"] is not None:
        r["bufferbloat_nota"] = nota_bufferbloat(r["acrescimo_sob_carga_ms"])
        print(f"   bufferbloat: +{r['acrescimo_sob_carga_ms']} ms sob carga -> nota {r['bufferbloat_nota']}")
    c.dados["velocidade"] = r


# ------------------------------------------------------------------ 4. Wi-Fi ao redor

def parse_redes(texto):
    redes, atual, bss = [], None, None
    for k, v in pares(texto):
        if re.match(r"^SSID \d+$", k):
            atual = {"ssid": v or "(oculta)", "bssids": []}
            redes.append(atual)
        elif re.match(r"^BSSID \d+$", k) and atual is not None:
            bss = {"bssid": v.lower()}
            atual["bssids"].append(bss)
        elif bss is not None:
            for nome in ("canal", "sinal", "radio", "banda"):
                if nome not in bss and re.search(CHAVES_WLAN[nome], k, re.I):
                    bss[nome] = v
        elif atual is not None and re.search(CHAVES_WLAN["autenticacao"], k, re.I):
            atual["autenticacao"] = v
    for r in redes:
        for b in r["bssids"]:
            normalizar_radio(b)
    return redes


def congestionamento_24(aps, canal):
    """Soma o 'peso' das redes que se sobrepõem a um canal de 2,4 GHz (±4 canais)."""
    return round(sum((b["sinal"] / 100) * (1 - abs(b["canal"] - canal) / 5)
                     for b in aps if abs(b["canal"] - canal) <= 4), 2)


def etapa_wifi(c):
    if not WINDOWS:
        raise RuntimeError("varredura Wi-Fi só no Windows")
    txt = c.bruto("wlan-redes", rodar(["netsh", "wlan", "show", "networks", "mode=bssid"]))
    if re.search(r"location|localiza", txt, re.I) and "BSSID" not in txt:
        raise RuntimeError("o Windows bloqueou a varredura: ative Configurações > Privacidade > Localização")
    redes = parse_redes(txt)
    minha = (c.dados.get("conexao", {}).get("wifi") or {}).get("ssid")
    aps = [dict(b, ssid=r["ssid"]) for r in redes for b in r["bssids"] if b.get("canal")]
    a24 = [b for b in aps if b["canal"] <= 14]
    a5 = [b for b in aps if b["canal"] > 14]
    vizinhos24 = [b for b in a24 if b["ssid"] != minha]
    r = {"total_redes": len(redes), "total_aps": len(aps), "aps_24": len(a24), "aps_5": len(a5),
         "minha_rede": minha, "meus_aps": sorted([b for b in aps if b["ssid"] == minha], key=lambda b: -b["sinal"]),
         "congestionamento_24": {ch: congestionamento_24(vizinhos24, ch) for ch in (1, 6, 11)},
         "ocupacao_5": {}, "redes": redes}
    for b in a5:
        if b["ssid"] != minha:
            r["ocupacao_5"][b["canal"]] = r["ocupacao_5"].get(b["canal"], 0) + 1
    r["melhor_canal_24"] = min(r["congestionamento_24"], key=r["congestionamento_24"].get)
    c.dados["wifi_scan"] = r
    print(f"   {len(redes)} redes / {len(aps)} pontos de acesso (2,4 GHz: {len(a24)}, 5 GHz: {len(a5)})")
    for b in r["meus_aps"]:
        print(f"   sua rede: {b['bssid']} canal {b['canal']} ({b.get('banda')}) sinal {b['sinal']}%")
    print(f"   congestionamento 2,4 GHz (menor é melhor): {r['congestionamento_24']}")


# ------------------------------------------------------------------ 5. dispositivos

FONTES_FABRICANTES = [MANUF_URL, "https://standards-oui.ieee.org/oui/oui.txt"]


def ler_tabela_fabricantes(texto):
    """Aceita o formato do Wireshark (00:00:0C<TAB>Cisco<TAB>Cisco Systems) e o do IEEE (00-00-0C   (hex)  Cisco)."""
    tabela = {}
    for linha in texto.splitlines():
        m = re.match(r"^\s*([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2})(?:\s+\(hex\))?\s+(.+)$", linha)
        if m and "/" not in linha.split()[0]:
            nome = m.group(4).split("\t")
            nome = (nome[1] if len(nome) > 1 and nome[1].strip() else nome[0]).strip()
            tabela[":".join(m.group(i) for i in (1, 2, 3)).lower()] = nome
    return tabela


def carregar_fabricantes(c):
    arq = PASTA / "manuf.txt"
    tabela = ler_tabela_fabricantes(arq.read_text(encoding="utf-8", errors="replace")) if arq.exists() else {}
    if len(tabela) > 1000 and time.time() - arq.stat().st_mtime < 90 * 86400:
        return tabela
    for url in FONTES_FABRICANTES:
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                texto = r.read().decode("utf-8", "replace")
            nova = ler_tabela_fabricantes(texto)
            if len(nova) > 1000:
                arq.write_text(texto, encoding="utf-8")
                print(f"   lista de fabricantes: {len(nova)} prefixos ({url.split('/')[2]})")
                return nova
            raise ValueError(f"formato inesperado ({len(nova)} prefixos lidos)")
        except Exception as e:
            msg = f"lista de fabricantes ({url.split('/')[2]}): {type(e).__name__}: {e}"
            print(f"   aviso: {msg}")
            c.dados["erros"].append(msg)
    return tabela


def mac_aleatorio(mac):
    return int(mac[1], 16) & 2 == 2


def ping_rapido(ip):
    cmd = ["ping", "-n", "1", "-w", "400", ip] if WINDOWS else ["ping", "-c", "1", "-W", "1", ip]
    return "TTL=" in rodar(cmd, 5).upper()


def nome_host(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


def etapa_dispositivos(c):
    con = c.dados["conexao"]
    rede = ipaddress.ip_network(f"{con['ip']}/{min(max(con['prefixo'], 24), 30)}", strict=False)
    hosts = [str(h) for h in rede.hosts()]
    print(f"   varrendo {rede} ({len(hosts)} endereços)...")
    with ThreadPoolExecutor(64) as pool:
        vivos = dict(zip(hosts, pool.map(ping_rapido, hosts)))
    arp = c.bruto("arp", rodar(["arp", "-a"]))
    macs = {}
    for ip, mac in re.findall(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})", arp):
        mac = mac.lower().replace("-", ":")
        if ipaddress.ip_address(ip) in rede and not mac.startswith(("ff:ff", "01:00:5e")):
            macs[ip] = mac
    fabricantes = carregar_fabricantes(c)
    ips = sorted(set(macs) | {h for h, v in vivos.items() if v} | {con["ip"]}, key=ipaddress.ip_address)
    pool = ThreadPoolExecutor(32)
    futs = {ip: pool.submit(nome_host, ip) for ip in ips}
    wait(futs.values(), timeout=8)
    pool.shutdown(wait=False, cancel_futures=True)
    lista = []
    for ip in ips:
        mac = macs.get(ip, "")
        f = futs[ip]
        d = {"ip": ip, "mac": mac, "nome": f.result() if f.done() and not f.exception() else "",
             "fabricante": fabricantes.get(mac[:8], "") if mac else "", "responde_ping": vivos.get(ip, False),
             "obs": []}
        if ip == con["ip"]:
            d["obs"].append("este computador")
        if ip == con["gateway"]:
            d["obs"].append("roteador (gateway)")
        if ip == "192.168.5.199":
            d["obs"].append("TV Roku")
        if mac and mac_aleatorio(mac):
            d["obs"].append("MAC aleatório (celular/tablet com privacidade de Wi-Fi ativada)")
            d["fabricante"] = d["fabricante"] or "(privado)"
        if mac and not d["fabricante"]:
            d["obs"].append("fabricante desconhecido: confira no app do Twibi")
        lista.append(d)
    c.dados["dispositivos"] = lista
    for d in lista:
        print(f"   {d['ip']:<15} {d['mac'] or '-':<17} {d['fabricante'][:28]:<28} {d['nome'][:24]:<24} {'; '.join(d['obs'])}")


# ------------------------------------------------------------------ 6. DNS

def consulta_dns(servidor, dominio, timeout=2.0):
    ident = random.randint(0, 65535)
    pacote = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    pacote += b"".join(bytes([len(p)]) + p.encode() for p in dominio.split(".")) + b"\x00"
    pacote += struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t0 = time.perf_counter()
        s.sendto(pacote, (servidor, 53))
        while True:
            dados, _ = s.recvfrom(2048)
            if dados[:2] == pacote[:2]:
                return (time.perf_counter() - t0) * 1000
    except OSError:
        return None
    finally:
        s.close()


def etapa_dns(c):
    servidores = {}
    for i, ip in enumerate(c.dados["conexao"].get("dns") or []):
        servidores[f"Atual{'' if i == 0 else ' ' + str(i + 1)} ({ip})"] = ip
    for nome, ip in DNS_PUBLICOS.items():
        if ip not in servidores.values():
            servidores[f"{nome} ({ip})"] = ip
    res = {}
    for nome, ip in servidores.items():
        tempos, falhas = [], 0
        for _ in range(3):
            for dom in DOMINIOS:
                t = consulta_dns(ip, dom)
                if t is None:
                    falhas += 1
                else:
                    tempos.append(t)
        res[nome] = {"ip": ip, "mediana_ms": round(statistics.median(tempos), 1) if tempos else None,
                     "media_ms": round(statistics.mean(tempos), 1) if tempos else None,
                     "falhas": falhas, "consultas": 3 * len(DOMINIOS)}
        print(f"   {nome:<28} mediana {res[nome]['mediana_ms']} ms   falhas {falhas}/{3 * len(DOMINIOS)}")
    validos = {k: v for k, v in res.items() if v["mediana_ms"] is not None}
    c.dados["dns"] = {"servidores": res, "mais_rapido": min(validos, key=lambda k: validos[k]["mediana_ms"]) if validos else None}


# ------------------------------------------------------------------ relatório

def problemas(d):
    p = []  # (gravidade 1-3, texto)
    con = d.get("conexao", {})
    pg = d.get("ping", {})
    gw = pg.get(con.get("gateway"), {})
    if gw.get("perda_pct"):
        p.append((3, f"Perda de {gw['perda_pct']}% até o roteador: problema no Wi-Fi/cabo dentro de casa."))
    if (gw.get("media") or 0) > 15 or (gw.get("jitter") or 0) > 10:
        p.append((2, f"Latência até o roteador alta/instável ({gw.get('media')} ms, jitter {gw.get('jitter')} ms); o normal é < 5 ms."))
    for h in ALVOS_PING:
        r = pg.get(h, {})
        if r.get("perda_pct") and r["perda_pct"] > (gw.get("perda_pct") or 0):
            p.append((3, f"Perda de {r['perda_pct']}% até {h} (maior que até o roteador): possível problema na operadora."))
        if (r.get("jitter") or 0) > 20:
            p.append((2, f"Jitter alto até {h} ({r['jitter']} ms): atrapalha chamadas e jogos."))
    v = d.get("velocidade") or {}
    if v.get("bufferbloat_nota") in ("C", "D", "F"):
        p.append((2, f"Bufferbloat nota {v['bufferbloat_nota']}: latência sobe {v['acrescimo_sob_carga_ms']} ms quando a rede está ocupada "
                     "(chamadas travam quando alguém baixa/assiste algo). QoS no roteador ajuda."))
    w = con.get("wifi") or {}
    if w.get("conectado"):
        if w.get("sinal", 100) < 60:
            p.append((2, f"Sinal do Wi-Fi fraco neste computador ({w['sinal']}% ≈ {w.get('dbm_aprox')} dBm)."))
        if w.get("banda", "").startswith("2"):
            p.append((1, "Este computador está em 2,4 GHz; em 5 GHz costuma ser bem mais rápido perto do roteador."))
    sc = d.get("wifi_scan") or {}
    if sc.get("minha_rede") and sc.get("meus_aps"):
        meus24 = [b for b in sc["meus_aps"] if b["canal"] <= 14]
        for b in meus24:
            if b["canal"] not in (1, 6, 11):
                p.append((1, f"Sua rede usa o canal {b['canal']} em 2,4 GHz; 1, 6 ou 11 evitam sobreposição."))
        if meus24:
            atual = meus24[0]["canal"]
            cong = sc.get("congestionamento_24", {})
            melhor = sc.get("melhor_canal_24")
            if atual in cong and melhor != atual and cong[atual] - cong[melhor] > 1:
                p.append((1, f"Canal {melhor} de 2,4 GHz está menos ocupado que o atual ({atual})."))
    dns = d.get("dns") or {}
    serv = dns.get("servidores", {})
    atual = next((v for k, v in serv.items() if k.startswith("Atual")), None)
    melhor = serv.get(dns.get("mais_rapido") or "", {})
    if atual and melhor and atual.get("mediana_ms") and melhor.get("mediana_ms") and \
            atual["mediana_ms"] - melhor["mediana_ms"] > 10:
        p.append((1, f"DNS atual ({atual['mediana_ms']} ms) é mais lento que {dns['mais_rapido']} ({melhor['mediana_ms']} ms)."))
    desconhecidos = [x for x in d.get("dispositivos", []) if any("desconhecido" in o for o in x["obs"])]
    if desconhecidos:
        p.append((1, f"{len(desconhecidos)} dispositivo(s) de fabricante não identificado: confira no app do Twibi."))
    return sorted(p, key=lambda x: -x[0])


def tabela(cab, linhas):
    out = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    out += ["| " + " | ".join(str(x) for x in l) + " |" for l in linhas]
    return "\n".join(out)


def relatorio(d):
    g = {3: "🔴 grave", 2: "🟠 médio", 1: "🟡 leve"}
    con = d.get("conexao", {})
    w = con.get("wifi") or {}
    L = [f"# Diagnóstico da rede — {d['quando']}", "",
         f"Computador: {d['computador']} — conexão via **{con.get('tipo', '?')}**"
         + (f" ({w.get('ssid')}, canal {w.get('canal')} / {w.get('banda')}, sinal {w.get('sinal')}%, "
            f"taxa {w.get('taxa_rx')}/{w.get('taxa_tx')} Mbps)" if w.get("conectado") else ""),
         f"IP {con.get('ip')} · gateway **{con.get('gateway')}** · DNS {', '.join(con.get('dns') or []) or '?'}", "",
         "## Problemas encontrados (do mais grave ao menos grave)", ""]
    ps = problemas(d)
    L += [f"{i}. {g[s]} — {t}" for i, (s, t) in enumerate(ps, 1)] or ["Nenhum problema evidente nas medições."]
    if d.get("ping"):
        L += ["", "## Latência, jitter e perda (30 pings)", "",
              tabela(["Destino", "Média (ms)", "Mín/Máx", "Jitter (ms)", "Perda"],
                     [[h, r.get("media"), f"{r.get('min')}/{r.get('max')}", r.get("jitter"), f"{r['perda_pct']}%"]
                      for h, r in d["ping"].items()])]
    v = d.get("velocidade")
    if v:
        L += ["", "## Velocidade e bufferbloat (Cloudflare)", "",
              tabela(["Download", "Upload", "Latência ociosa", "Durante download", "Durante upload", "Bufferbloat"],
                     [[f"{v['download_mbps']} Mbps", f"{v['upload_mbps']} Mbps", f"{v['latencia_ociosa'].get('media')} ms",
                       f"{v['latencia_download'].get('media')} ms", f"{v['latencia_upload'].get('media')} ms",
                       f"+{v.get('acrescimo_sob_carga_ms')} ms (nota {v.get('bufferbloat_nota', '?')})"]])]
    if d.get("traceroute"):
        L += ["", "## Traceroute até 1.1.1.1", "", tabela(["Salto", "IP", "ms"],
                                                          [[s["salto"], s["ip"], s["ms"]] for s in d["traceroute"]])]
    sc = d.get("wifi_scan")
    if sc:
        L += ["", "## Wi-Fi ao redor", "",
              f"{sc['total_redes']} redes, {sc['total_aps']} pontos de acesso (2,4 GHz: {sc['aps_24']}, 5 GHz: {sc['aps_5']}).", "",
              "**Sua rede (cada linha é um nó/rádio da mesh visto daqui):**", "",
              tabela(["BSSID", "Canal", "Banda", "Sinal"],
                     [[b["bssid"], b["canal"], b.get("banda"), f"{b['sinal']}%"] for b in sc["meus_aps"]]), "",
              f"Congestionamento em 2,4 GHz (menor é melhor): {sc['congestionamento_24']} → melhor: **canal {sc['melhor_canal_24']}**", "",
              f"Vizinhos por canal em 5 GHz: {dict(sorted(sc['ocupacao_5'].items())) or 'nenhum'}", "",
              tabela(["Rede", "Canal", "Sinal"],
                     [[r["ssid"], b.get("canal"), f"{b.get('sinal')}%"] for r in sc["redes"] for b in r["bssids"]
                      if r["ssid"] != sc["minha_rede"]][:40])]
    if d.get("dispositivos"):
        L += ["", "## Dispositivos na rede", "",
              tabela(["IP", "MAC", "Fabricante", "Nome", "Observação"],
                     [[x["ip"], x["mac"], x["fabricante"], x["nome"], "; ".join(x["obs"])] for x in d["dispositivos"]]),
              "", "_Consumo de banda por dispositivo não dá para medir deste computador; veja no app do Twibi._"]
    dns = d.get("dns")
    if dns:
        L += ["", "## Velocidade dos DNS", "",
              tabela(["Servidor", "Mediana (ms)", "Falhas"],
                     [[k, v["mediana_ms"], f"{v['falhas']}/{v['consultas']}"] for k, v in dns["servidores"].items()]),
              "", f"Mais rápido: **{dns['mais_rapido']}**"]
    if d["erros"]:
        L += ["", "## Etapas com erro", ""] + [f"- {e}" for e in d["erros"]]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="Diagnóstico da rede (só leitura).")
    ap.add_argument("--rapido", action="store_true", help="pula o teste de velocidade")
    ap.add_argument("--nome", default="", help="rótulo da medição, ex.: antes, depois-canal, cabo")
    args = ap.parse_args()
    rotulo = datetime.now().strftime("%Y-%m-%d_%H%M") + (f"_{args.nome}" if args.nome else "")
    c = Coleta(PASTA / "resultados" / rotulo)
    c.dados["rotulo"] = args.nome
    print("Diagnóstico da rede: só medições, nada é alterado.")
    if c.etapa("Conexão e roteador", etapa_conexao) is None and "conexao" not in c.dados:
        print("Sem informação da conexão; parando.")
        return 1
    c.etapa("Latência, jitter e perda", etapa_ping)
    c.etapa("Traceroute", etapa_traceroute)
    if not args.rapido:
        c.etapa("Velocidade e bufferbloat", etapa_velocidade)
    c.etapa("Redes Wi-Fi ao redor", etapa_wifi)
    c.etapa("Dispositivos conectados", etapa_dispositivos)
    c.etapa("Velocidade dos DNS", etapa_dns)
    (c.saida / "dados.json").write_text(json.dumps(c.dados, indent=2, ensure_ascii=False), encoding="utf-8")
    (c.saida / "relatorio-diagnostico.md").write_text(relatorio(c.dados), encoding="utf-8")
    print(f"\nPronto. Relatório: {c.saida / 'relatorio-diagnostico.md'}")
    print("Me envie o arquivo relatorio-diagnostico.md (ou a pasta inteira) no chat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
