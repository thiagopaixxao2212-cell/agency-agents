#!/usr/bin/env python3
"""Controle de TV Roku pela rede local (API ECP, porta 8060).

Só usa a biblioteca padrão do Python 3.8+. Veja README.md para exemplos.
"""
import argparse
import ipaddress
import json
import re
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PASTA = Path(__file__).resolve().parent
CONFIG = PASTA / "config.json"
APPS = PASTA / "apps.json"
PORTA = 8060
PADRAO = {"ip": "192.168.5.199", "mac": "94:8C:D7:E8:DA:4F"}

TECLAS_NAV = {
    "cima": "Up", "baixo": "Down", "esquerda": "Left", "direita": "Right",
    "ok": "Select", "voltar": "Back", "home": "Home",
}
TECLAS_VOL = {"subir": "VolumeUp", "descer": "VolumeDown", "mudo": "VolumeMute"}
# Palavras que indicam suporte a Bluetooth/fone em /query/device-info
PISTAS_AUDIO = ("bluetooth", "bt-", "headphone", "private-listening", "audio")

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


class ErroRoku(Exception):
    pass


# ---------------------------------------------------------------- config

def ler_config():
    cfg = dict(PADRAO)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg


def salvar_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalizar_mac(mac):
    partes = re.split(r"[:\-.]", (mac or "").strip().lower())
    if len(partes) == 6:
        return ":".join(p.zfill(2) for p in partes)
    limpo = re.sub(r"[^0-9a-f]", "", (mac or "").lower())
    return ":".join(limpo[i:i + 2] for i in range(0, 12, 2)) if len(limpo) == 12 else ""


# ---------------------------------------------------------------- HTTP / ECP

def ecp(ip, metodo, caminho, timeout=4.0):
    url = f"http://{ip}:{PORTA}{caminho}"
    req = urllib.request.Request(url, method=metodo, data=b"" if metodo == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise ErroRoku(
                f"A TV recusou {caminho} (HTTP 403). Confira na TV: Configurações > Sistema > "
                "Configurações avançadas do sistema > Controle por aplicativos móveis > "
                "Acesso à rede = Padrão ou Permissivo.") from None
        raise ErroRoku(f"HTTP {e.code} em {url}") from None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise ErroRoku(
            f"A TV não respondeu em {ip}:{PORTA} ({getattr(e, 'reason', e)}). "
            "Ela está ligada e na mesma rede? Rode: python roku.py descobrir") from None


def xml_plano(dados):
    raiz = ET.fromstring(dados)
    return {filho.tag: (filho.text or "").strip() for filho in raiz}


def device_info(ip, timeout=4.0):
    return xml_plano(ecp(ip, "GET", "/query/device-info", timeout))


def tecla(ip, nome, vezes=1, pausa=0.35):
    for i in range(vezes):
        ecp(ip, "POST", f"/keypress/{nome}")
        if i < vezes - 1:
            time.sleep(pausa)


def macs_da_tv(info):
    return {normalizar_mac(info.get(k, "")) for k in ("wifi-mac", "ethernet-mac")} - {""}


# ---------------------------------------------------------------- descoberta

def ssdp(espera=3.0):
    msg = ("M-SEARCH * HTTP/1.1\r\nHost: 239.255.255.250:1900\r\n"
           'Man: "ssdp:discover"\r\nST: roku:ecp\r\nMX: 2\r\n\r\n').encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.5)
    ips = set()
    try:
        s.sendto(msg, ("239.255.255.250", 1900))
        s.sendto(msg, ("239.255.255.250", 1900))
        fim = time.time() + espera
        while time.time() < fim:
            try:
                dados, _ = s.recvfrom(2048)
            except socket.timeout:
                continue
            m = re.search(r"LOCATION:\s*http://([\d.]+):8060", dados.decode(errors="ignore"), re.I)
            if m:
                ips.add(m.group(1))
    finally:
        s.close()
    return ips


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # não envia nada; só escolhe a interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def porta_aberta(ip):
    try:
        with socket.create_connection((str(ip), PORTA), timeout=0.6):
            return str(ip)
    except OSError:
        return None


def varrer(rede):
    hosts = list(ipaddress.ip_network(rede, strict=False).hosts())
    with ThreadPoolExecutor(max_workers=64) as pool:
        return [ip for ip in pool.map(porta_aberta, hosts) if ip]


def cmd_descobrir(cfg, args):
    alvo = normalizar_mac(args.mac or cfg["mac"])
    print(f"Procurando Roku com MAC {alvo}...")
    candidatos = []
    print("  1) SSDP (ST: roku:ecp)...")
    try:
        candidatos += sorted(ssdp())
    except OSError as e:
        print(f"     SSDP falhou: {e}")
    print(f"     respostas: {candidatos or 'nenhuma'}")
    if cfg.get("ip") and cfg["ip"] not in candidatos:
        candidatos.append(cfg["ip"])

    def checar(ip):
        try:
            info = device_info(ip, timeout=2.0)
        except ErroRoku:
            return None
        return info if alvo in macs_da_tv(info) else None

    for ip in candidatos:
        info = checar(ip)
        if info:
            return achou(cfg, ip, info)

    rede = args.rede or f"{ip_local()}/24"
    print(f"  2) Varrendo a porta {PORTA} em {rede}...")
    abertos = varrer(rede)
    print(f"     porta {PORTA} aberta em: {abertos or 'nenhum'}")
    for ip in abertos:
        info = checar(ip)
        if info:
            return achou(cfg, ip, info)
    raise ErroRoku("Nenhuma Roku com esse MAC encontrada. A TV está ligada e no mesmo Wi-Fi?")


def achou(cfg, ip, info):
    cfg["ip"] = ip
    cfg["nome"] = info.get("user-device-name") or info.get("friendly-device-name", "")
    salvar_config(cfg)
    print(f"Encontrada: {cfg['nome']} ({info.get('model-name', '?')}) em {ip}. Salvo em config.json.")


# ---------------------------------------------------------------- comandos

def cmd_info(cfg, args):
    info = device_info(cfg["ip"])
    if args.tudo:
        for k, v in info.items():
            print(f"{k}: {v}")
        return
    campos = ["user-device-name", "friendly-device-name", "vendor-name", "model-name",
              "model-number", "software-version", "software-build", "wifi-mac",
              "network-name", "power-mode", "is-tv", "supports-wake-on-wlan"]
    for k in campos:
        if k in info:
            print(f"{k}: {info[k]}")
    print("\nCampos ligados a áudio / fone / Bluetooth:")
    mostrar_pistas_audio(info)


def mostrar_pistas_audio(info):
    achados = {k: v for k, v in info.items() if any(p in k.lower() for p in PISTAS_AUDIO)}
    if not achados:
        print("  (nenhum campo de Bluetooth, fone ou audição privada informado pela TV)")
    for k, v in achados.items():
        print(f"  {k}: {v}")
    if not any("bluetooth" in k.lower() for k in info):
        print("  -> device-info não tem nenhum campo 'bluetooth'.")
    return achados


def cmd_apps(cfg, args):
    raiz = ET.fromstring(ecp(cfg["ip"], "GET", "/query/apps"))
    apps = [{"id": a.get("id"), "nome": (a.text or "").strip(), "tipo": a.get("type"),
             "versao": a.get("version")} for a in raiz.findall("app")]
    APPS.write_text(json.dumps(apps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for a in apps:
        print(f"{a['id']:>8}  {a['nome']}")
    print(f"\n{len(apps)} apps salvos em apps.json")


def wake_on_lan(mac):
    m = normalizar_mac(mac).replace(":", "")
    pacote = bytes.fromhex("FF" * 6 + m * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for destino in ("255.255.255.255", str(ipaddress.ip_network(f"{ip_local()}/24", strict=False).broadcast_address)):
            try:
                s.sendto(pacote, (destino, 9))
            except OSError:
                pass


def cmd_ligar(cfg, args):
    try:
        tecla(cfg["ip"], "PowerOn")
        print("Comando PowerOn enviado.")
    except ErroRoku as e:
        print(f"PowerOn não respondeu ({e}).\nEnviando Wake-on-LAN para {cfg['mac']}...")
        wake_on_lan(cfg["mac"])
        time.sleep(6)
        tecla(cfg["ip"], "PowerOn")
        print("TV acordou e recebeu PowerOn.")


def cmd_desligar(cfg, args):
    tecla(cfg["ip"], "PowerOff")
    print("Comando PowerOff enviado (a TV entra em espera).")


def cmd_volume(cfg, args):
    tecla(cfg["ip"], TECLAS_VOL[args.acao], args.vezes)
    print(f"Volume: {args.acao} x{args.vezes}")


def cmd_navegar(cfg, args):
    tecla(cfg["ip"], TECLAS_NAV[args.direcao], args.vezes)
    print(f"Tecla: {args.direcao} x{args.vezes}")


def cmd_tecla(cfg, args):
    tecla(cfg["ip"], args.nome, args.vezes)
    print(f"Tecla ECP: {args.nome} x{args.vezes}")


def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def cmd_abrir(cfg, args):
    if not APPS.exists():
        print("apps.json não existe; buscando a lista de apps na TV...")
        cmd_apps(cfg, args)
    apps = json.loads(APPS.read_text(encoding="utf-8"))
    busca = sem_acento(args.app)
    exatos = [a for a in apps if sem_acento(a["nome"]) == busca or a["id"] == args.app]
    parciais = [a for a in apps if busca in sem_acento(a["nome"])]
    achados = exatos or parciais
    if not achados:
        raise ErroRoku(f"Nenhum app parecido com '{args.app}'. Rode 'python roku.py apps' para ver a lista.")
    if len(achados) > 1:
        raise ErroRoku("Mais de um app combina: " + ", ".join(a["nome"] for a in achados) + ". Seja mais específico.")
    app = achados[0]
    ecp(cfg["ip"], "POST", f"/launch/{app['id']}")
    print(f"Abrindo {app['nome']} (id {app['id']}).")


def cmd_digitar(cfg, args):
    for ch in args.texto:
        ecp(cfg["ip"], "POST", "/keypress/Lit_" + urllib.parse.quote(ch, safe=""))
        time.sleep(0.08)
    print(f"Digitado: {args.texto!r}")


def segundos(ms):
    try:
        s = int(re.sub(r"\D", "", ms)) // 1000
    except ValueError:
        return "?"
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def cmd_status(cfg, args):
    ip = cfg["ip"]
    info = device_info(ip)
    estados = {"PowerOn": "ligada", "DisplayOff": "em espera (tela apagada)",
               "Ready": "em espera", "Headless": "sem tela"}
    modo = info.get("power-mode", "?")
    print(f"TV: {info.get('user-device-name') or info.get('friendly-device-name', '?')} ({ip})")
    print(f"Energia: {estados.get(modo, modo)}")
    raiz = ET.fromstring(ecp(ip, "GET", "/query/active-app"))
    app = raiz.find("app")
    tela = raiz.find("screensaver")
    if app is not None and not app.get("id"):
        print("App ativo: nenhum (tela inicial da Roku)")
    elif app is not None:
        print(f"App ativo: {(app.text or '').strip()} (id {app.get('id')})")
    if tela is not None:
        print(f"Protetor de tela: {(tela.text or '').strip()}")
    try:
        p = ET.fromstring(ecp(ip, "GET", "/query/media-player"))
        estado = {"play": "tocando", "pause": "pausado", "buffer": "carregando",
                  "stop": "parado", "close": "nada tocando", "startup": "iniciando"}
        linha = f"Player: {estado.get(p.get('state'), p.get('state'))}"
        pos, dur = p.findtext("position"), p.findtext("duration")
        if pos:
            linha += f" — {segundos(pos)}" + (f" / {segundos(dur)}" if dur else "")
        print(linha)
    except ErroRoku:
        pass
    if app is not None and app.get("id") == "tvinput.dtv":
        try:
            c = xml_plano(ecp(ip, "GET", "/query/tv-active-channel").replace(b"<channel>", b"").replace(b"</channel>", b""))
        except Exception:
            c = {}
        if c:
            print(f"Canal: {c.get('number', '?')} {c.get('name', '')} — {c.get('program-title', '')}")


# ---------------------------------------------------------------- bluetooth

COMANDOS_GRAVACAO = {
    "w": "Up", "s": "Down", "a": "Left", "d": "Right",
    "cima": "Up", "baixo": "Down", "esquerda": "Left", "direita": "Right",
    "ok": "Select", "voltar": "Back", "home": "Home",
}


def confirmar(pergunta):
    return input(f"{pergunta} [s/N] ").strip().lower() in ("s", "sim", "y")


def cmd_bluetooth(cfg, args):
    ip = cfg["ip"]
    print("1) O que a TV informa sobre Bluetooth / fone (device-info):")
    mostrar_pistas_audio(device_info(ip))
    print("\nObs.: a API ECP não tem comandos de Bluetooth; aqui só navegamos pelo menu.\n")

    if args.gravar:
        return gravar_sequencia(cfg)

    seq = cfg.get("bluetooth_seq")
    if not seq:
        print("Ainda não há sequência gravada até 'Controles remotos e dispositivos'.\n"
              "Olhando para a TV, rode:  python roku.py bluetooth --gravar")
        return
    print("2) Sequência salva: " + " ".join(seq))
    print("   Ela só navega pelos menus; não pareia nem altera nada.")
    if not confirmar("Enviar essas teclas para a TV agora?"):
        print("Cancelado.")
        return
    for t in seq:
        if args.passo and not confirmar(f"   enviar {t}?"):
            print("Parado.")
            return
        tecla(ip, t)
        time.sleep(0.8 if t == "Home" else 0.45)
    print("Pronto. A TV deve estar em 'Controles remotos e dispositivos'\n"
          "(opções: Controles remotos, Alto-falantes, Adicionar dispositivos).\n"
          "Esta TV não lista Bluetooth para fone. Para ouvir no fone, use a audição privada\n"
          "do app Roku no celular (veja o README).")


def gravar_sequencia(cfg):
    ip = cfg["ip"]
    print("MODO GRAVAÇÃO — olhe para a TV.\n"
          "Comandos: w/s/a/d (ou cima/baixo/esquerda/direita; 'sss' = baixo 3x), ok, voltar, home,\n"
          "          fim (salva), sair (descarta). Errou? Aperte a direção oposta.\n"
          "Vá até a tela 'Controles remotos e dispositivos' e digite 'fim'.\n"
          "NÃO confirme pareamento nem outras opções durante a gravação.\n")
    seq = ["Home", "Home"]
    tecla(ip, "Home")
    time.sleep(1.0)
    tecla(ip, "Home")
    while True:
        cmd = input(f"[{len(seq)} teclas] > ").strip().lower()
        if cmd == "fim":
            cfg["bluetooth_seq"] = seq
            salvar_config(cfg)
            print("Sequência salva em config.json: " + " ".join(seq))
            return
        if cmd == "sair":
            print("Descartado; nada salvo.")
            return
        if not cmd:
            continue
        if cmd in COMANDOS_GRAVACAO:
            nomes = [COMANDOS_GRAVACAO[cmd]]
        elif set(cmd) <= set("wasd"):  # ex.: "sss" = baixo 3 vezes
            nomes = [COMANDOS_GRAVACAO[c] for c in cmd]
        else:
            print("Comando desconhecido. Use w/s/a/d (ex.: sss), ok, voltar, home, fim ou sair.")
            continue
        for nome in nomes:
            tecla(ip, nome)
            seq.append(nome)
            time.sleep(0.35)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(prog="roku.py", description="Controle da TV Roku pela rede local.")
    p.add_argument("--ip", help="usa este IP em vez do salvo em config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("descobrir", help="acha a TV pelo MAC e salva o IP em config.json")
    s.add_argument("--mac")
    s.add_argument("--rede", help="faixa a varrer, ex. 192.168.5.0/24")
    s.set_defaults(f=cmd_descobrir)

    s = sub.add_parser("info", help="mostra /query/device-info")
    s.add_argument("--tudo", action="store_true", help="todos os campos")
    s.set_defaults(f=cmd_info)

    sub.add_parser("apps", help="lista os apps e salva apps.json").set_defaults(f=cmd_apps)
    sub.add_parser("ligar").set_defaults(f=cmd_ligar)
    sub.add_parser("desligar").set_defaults(f=cmd_desligar)

    s = sub.add_parser("volume")
    s.add_argument("acao", choices=list(TECLAS_VOL))
    s.add_argument("vezes", nargs="?", type=int, default=1)
    s.set_defaults(f=cmd_volume)

    s = sub.add_parser("navegar")
    s.add_argument("direcao", choices=list(TECLAS_NAV))
    s.add_argument("vezes", nargs="?", type=int, default=1)
    s.set_defaults(f=cmd_navegar)

    s = sub.add_parser("tecla", help="envia uma tecla ECP crua (ex.: Play, Rev, Fwd, InputHDMI1)")
    s.add_argument("nome")
    s.add_argument("vezes", nargs="?", type=int, default=1)
    s.set_defaults(f=cmd_tecla)

    s = sub.add_parser("abrir", help="abre um app pelo nome")
    s.add_argument("app", nargs="+")
    s.set_defaults(f=cmd_abrir)

    s = sub.add_parser("digitar", help="digita texto num campo de busca/teclado da TV")
    s.add_argument("texto")
    s.set_defaults(f=cmd_digitar)

    sub.add_parser("status", help="estado da TV e o que está tocando").set_defaults(f=cmd_status)

    s = sub.add_parser("bluetooth", help="vai até Configurações > Controles remotos e dispositivos")
    s.add_argument("--gravar", action="store_true", help="grava a sequência de teclas olhando a TV")
    s.add_argument("--passo", action="store_true", help="pede confirmação a cada tecla")
    s.set_defaults(f=cmd_bluetooth)

    args = p.parse_args(argv)
    if getattr(args, "app", None):
        args.app = " ".join(args.app)
    cfg = ler_config()
    if args.ip:
        cfg["ip"] = args.ip
    try:
        args.f(cfg, args)
    except ErroRoku as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
