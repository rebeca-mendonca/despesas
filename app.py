import calendar
import html
import os
import re
import sqlite3
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "financas.db")
PORT = int(os.environ.get("PORT", "8000"))
META_PADRAO = 1500.0
VALORES_DIARIA = (90, 100)

MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
         "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
RE_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

def esc(s):
    return html.escape(str(s), quote=True)

def brl(v):
    v = round(v or 0, 2)
    if v == 0:
        v = 0.0
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-R$ " if v < 0 else "R$ ") + s

def numero(txt, padrao=0.0):
    txt = (txt or "").replace("R$", "").strip()
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return padrao

def somar_meses(ym, n):
    a, m = int(ym[:4]), int(ym[5:7])
    t = a * 12 + (m - 1) + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"

def nome_mes(ym):
    return f"{MESES[int(ym[5:7]) - 1]} de {ym[:4]}"

def mes_atual():
    h = date.today()
    return f"{h.year:04d}-{h.month:02d}"

def conectar():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def criar_banco():
    con = conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS meses (
            ym TEXT PRIMARY KEY,
            meta REAL NOT NULL,
            saldo_inicial REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS fixos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            tipo TEXT NOT NULL,
            inicio TEXT NOT NULL,
            fim TEXT
        );
        CREATE TABLE IF NOT EXISTS lancamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ym TEXT NOT NULL,
            tipo TEXT NOT NULL,
            categoria TEXT NOT NULL DEFAULT 'avulso',
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            status INTEGER NOT NULL DEFAULT 0,
            data TEXT,
            fixo_id INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_fixo_mes
            ON lancamentos(fixo_id, ym) WHERE fixo_id IS NOT NULL;
    """)
    if con.execute("PRAGMA user_version").fetchone()[0] == 0:
        semear(con)
        con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()


def semear(con):
    con.execute("INSERT INTO fixos(descricao, valor, tipo, inicio, fim) VALUES (?,?,?,?,?)",
                ("Iasmin", 90, "entrada", "2026-12", "2027-01"))
    con.execute("INSERT INTO fixos(descricao, valor, tipo, inicio, fim) VALUES (?,?,?,?,?)",
                ("Jonatas", 100, "entrada", "2026-12", None))
    con.execute("INSERT INTO meses(ym, meta, saldo_inicial) VALUES ('2026-12', 1500, 190)")
    itens = [
        ("entrada", "avulso", "A receber", 150),
        ("entrada", "diaria", "Diária prevista 1", 180),
        ("entrada", "diaria", "Diária prevista 2", 110),
        ("entrada", "diaria", "Diária prevista 3", 80),
        ("gasto", "avulso", "Conta 1", 200),
        ("gasto", "avulso", "Conta 2", 98),
        ("gasto", "avulso", "Conta 3", 75),
        ("gasto", "avulso", "Roupa de Ano Novo (teto)", 50),
        ("gasto", "avulso", "Roupa de Natal (macacão)", 30),
    ]
    for tipo, cat, desc, valor in itens:
        con.execute("INSERT INTO lancamentos(ym, tipo, categoria, descricao, valor) "
                    "VALUES ('2026-12', ?,?,?,?)", (tipo, cat, desc, valor))


def garantir_mes(con, ym):
    "Cria o mes se nao existir e lanca os fixos que ainda faltam nele."
    if not con.execute("SELECT 1 FROM meses WHERE ym=?", (ym,)).fetchone():
        ant = con.execute("SELECT meta FROM meses WHERE ym=?", (somar_meses(ym, -1),)).fetchone()
        con.execute("INSERT INTO meses(ym, meta, saldo_inicial) VALUES (?,?,0)",
                    (ym, ant["meta"] if ant else META_PADRAO))
    fixos = con.execute("SELECT * FROM fixos WHERE inicio<=? AND (fim IS NULL OR fim>=?)",
                        (ym, ym)).fetchall()
    for f in fixos:
        con.execute("INSERT OR IGNORE INTO lancamentos"
                    "(ym, tipo, categoria, descricao, valor, status, fixo_id) "
                    "VALUES (?,?,?,?,?,0,?)",
                    (ym, f["tipo"], "fixo", f["descricao"], f["valor"], f["id"]))
    con.commit()

def totais(con, ym):
    m = con.execute("SELECT * FROM meses WHERE ym=?", (ym,)).fetchone()

    def soma(tipo, so_feitos=False):
        q = "SELECT COALESCE(SUM(valor),0) FROM lancamentos WHERE ym=? AND tipo=?"
        if so_feitos:
            q += " AND status=1"
        return con.execute(q, (ym, tipo)).fetchone()[0]

    ent, ent_ok = soma("entrada"), soma("entrada", True)
    gas, gas_ok = soma("gasto"), soma("gasto", True)
    ret = soma("retirada")
    ini = m["saldo_inicial"]
    meta = m["meta"]
    sobra = ini + ent - gas - ret
    return {
        "meta": meta, "inicial": ini,
        "entradas": ent, "recebido": ent_ok,
        "gastos": gas, "pago": gas_ok,
        "retiradas": ret,
        "sobra": sobra,
        "em_maos": ini + ent_ok - gas_ok - ret,
        "falta": max(0.0, meta - sobra),
        "pct": max(0.0, min(100.0, sobra / meta * 100)) if meta > 0 else 0.0,
    }


def pagina(titulo, corpo, ativo=""):
    def aba(chave, href, texto):
        cls = ' class="ativa"' if chave == ativo else ""
        return f'<a href="{href}"{cls}>{texto}</a>'

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo)}</title>
<link rel="stylesheet" href="/estilo.css">
<script src="/pagina.js" defer></script>
</head>
<body>
<header class="topo">
  <div class="largura">
    <span class="nome">Contas</span>
    <nav>
      {aba("mes", "/", "Mês")}
      {aba("ano", f"/ano/{mes_atual()[:4]}", "Ano")}
      {aba("fixos", "/fixos", "Fixos")}
    </nav>
  </div>
</header>
<main class="largura">
{corpo}
</main>
</body>
</html>"""


def linha(l, rotulos):
    i = l["id"]
    feito = l["status"] == 1
    fixo = ' <span class="tag">fixo</span>' if l["fixo_id"] else ""
    botao = (f'<form method="post" action="/alternar/{i}">'
             f'<button class="estado {"ok" if feito else ""}">{rotulos[1] if feito else rotulos[0]}</button></form>')
    return f"""<tr class="{"feito" if feito else ""}">
<td>{esc(l["descricao"])}{fixo}</td>
<td class="num"><form method="post" action="/valor/{i}">
<input type="number" step="0.01" min="0" name="valor" value="{l["valor"]:.2f}" onchange="this.form.submit()"></form></td>
<td>{botao}</td>
<td class="acao"><form method="post" action="/remover/{i}" onsubmit="return confirm('Remover este item?')">
<button class="link">remover</button></form></td>
</tr>"""


def linha_retirada(l):
    i = l["id"]
    return f"""<tr>
<td>{esc(l["descricao"])} <span class="tag">{esc(l["data"] or "")}</span></td>
<td class="num">{brl(-l["valor"])}</td>
<td class="acao"><form method="post" action="/remover/{i}" onsubmit="return confirm('Remover este item?')">
<button class="link">remover</button></form></td>
</tr>"""


def form_novo(ym, tipo, categoria, dica, status=0):
    return f"""<form method="post" action="/lancar" class="novo">
<input type="hidden" name="ym" value="{ym}">
<input type="hidden" name="tipo" value="{tipo}">
<input type="hidden" name="categoria" value="{categoria}">
<input type="hidden" name="status" value="{status}">
<input name="descricao" placeholder="{dica}" required>
<input name="valor" type="number" step="0.01" min="0.01" placeholder="Valor" required>
<button>Adicionar</button>
</form>"""


def bloco(titulo, subtitulo, linhas, total_texto, form):
    vazio = '<tr><td colspan="4" class="vazio">Nada lançado.</td></tr>'
    return f"""<section class="bloco">
<h2>{titulo}</h2>
<p class="sub">{subtitulo}</p>
<table>
<tbody>
{"".join(linhas) if linhas else vazio}
</tbody>
</table>
<p class="total">{total_texto}</p>
{form}
</section>"""


def pagina_mes(con, ym, consulta):
    garantir_mes(con, ym)
    t = totais(con, ym)
    lanc = con.execute("SELECT * FROM lancamentos WHERE ym=? ORDER BY id", (ym,)).fetchall()
    entradas = [l for l in lanc if l["tipo"] == "entrada" and l["categoria"] != "diaria"]
    diarias = [l for l in lanc if l["tipo"] == "entrada" and l["categoria"] == "diaria"]
    gastos = [l for l in lanc if l["tipo"] == "gasto"]
    retiradas = [l for l in lanc if l["tipo"] == "retirada"]

    soma_ent = sum(l["valor"] for l in entradas)
    soma_dia = sum(l["valor"] for l in diarias)

    ant, prox = somar_meses(ym, -1), somar_meses(ym, 1)
    restam = ""
    if ym == mes_atual():
        h = date.today()
        ult = calendar.monthrange(h.year, h.month)[1]
        restam = f'<p class="sub">Faltam {ult - h.day} dias para acabar o mês.</p>'

    if t["falta"] > 0:
        d90 = -(-t["falta"] // 90)
        d100 = -(-t["falta"] // 100)
        equiv = (f"Faltam {brl(t['falta'])} para a meta. Isso equivale a cerca de "
                 f"{int(d90)} diárias de R$ 90 ou {int(d100)} de R$ 100.")
    else:
        equiv = f"Meta atingida, com {brl(t['sobra'] - t['meta'])} acima."

    s90 = int(numero(consulta.get("s90", ["0"])[0]))
    s100 = int(numero(consulta.get("s100", ["0"])[0]))
    s90, s100 = max(0, s90), max(0, s100)
    simulado = t["sobra"] + s90 * 90 + s100 * 100
    dif = simulado - t["meta"]
    texto_sim = (f"Sobra simulada: <b>{brl(simulado)}</b>. "
                 + (f"Passa da meta em {brl(dif)}." if dif >= 0 else f"Ainda faltam {brl(-dif)}."))

    rapidas = "".join(
        f"""<form method="post" action="/lancar">
<input type="hidden" name="ym" value="{ym}"><input type="hidden" name="tipo" value="entrada">
<input type="hidden" name="categoria" value="diaria"><input type="hidden" name="status" value="1">
<input type="hidden" name="descricao" value="Diária"><input type="hidden" name="valor" value="{v}">
<button class="rapido">Diária feita de R$ {v}</button></form>"""
        for v in VALORES_DIARIA)

    corpo = f"""
<div class="titulo-mes">
  <a class="seta" href="/mes/{ant}" title="{nome_mes(ant)}">&larr; {MESES[int(ant[5:7]) - 1][:3]}</a>
  <div><h1>{nome_mes(ym)}</h1>{restam}</div>
  <a class="seta" href="/mes/{prox}" title="{nome_mes(prox)}">{MESES[int(prox[5:7]) - 1][:3]} &rarr;</a>
</div>

<div class="resumo">
  <div class="cartao"><span class="rot">Sobra projetada</span><span class="val">{brl(t["sobra"])}</span>
    <span class="nota">tudo que está previsto, já descontados gastos e retiradas</span></div>
  <div class="cartao"><span class="rot">Em mãos agora</span><span class="val">{brl(t["em_maos"])}</span>
    <span class="nota">só o que já foi recebido e o que já foi pago</span></div>
  <div class="cartao"><span class="rot">Meta do mês</span><span class="val">{brl(t["meta"])}</span>
    <form method="post" action="/meta" class="mini">
      <input type="hidden" name="ym" value="{ym}">
      <input type="number" step="0.01" min="0" name="meta" value="{t["meta"]:.2f}">
      <button>Alterar</button></form></div>
</div>

<div class="progresso-area">
  <div class="progresso"><div style="width:{t["pct"]:.1f}%"></div></div>
  <p>{t["pct"]:.0f}% da meta. {equiv}</p>
</div>

<section class="bloco">
  <h2>Saldo inicial</h2>
  <p class="sub">Dinheiro que você já tinha em mãos no começo do mês.</p>
  <form method="post" action="/saldo" class="novo">
    <input type="hidden" name="ym" value="{ym}">
    <input type="number" step="0.01" name="saldo" value="{t["inicial"]:.2f}">
    <button>Salvar</button>
  </form>
  <form method="post" action="/trazer" class="avulso">
    <input type="hidden" name="ym" value="{ym}">
    <button class="link">Trazer a sobra projetada de {nome_mes(ant)}</button>
  </form>
</section>

<div class="colunas">
<div>
{bloco("Entradas", "Valores que entram neste mês. Marque quando receber.",
       [linha(l, ("Receber", "Recebido")) for l in entradas],
       f"Total de entradas: <b>{brl(soma_ent)}</b>",
       form_novo(ym, "entrada", "avulso", "Quem vai pagar"))}

<section class="bloco">
<h2>Diárias e serviços</h2>
<p class="sub">Demandas que você faz. Registre as previstas e marque quando concluir.</p>
<div class="rapidas">{rapidas}</div>
<table><tbody>
{"".join(linha(l, ("Receber", "Recebido")) for l in diarias) or '<tr><td colspan="4" class="vazio">Nada lançado.</td></tr>'}
</tbody></table>
<p class="total">Total de diárias: <b>{brl(soma_dia)}</b></p>
{form_novo(ym, "entrada", "diaria", "Descrição do serviço")}
</section>
</div>

<div>
{bloco("Gastos", "Contas e compras do mês. Ajuste o valor real quando pagar.",
       [linha(l, ("Pagar", "Pago")) for l in gastos],
       f"Total de gastos: <b>{brl(t['gastos'])}</b>",
       form_novo(ym, "gasto", "avulso", "Conta ou compra"))}

<section class="bloco">
<h2>Retiradas</h2>
<p class="sub">Saídas fora do previsto: remédio, lanche, transporte.</p>
<table><tbody>
{"".join(linha_retirada(l) for l in retiradas) or '<tr><td colspan="3" class="vazio">Nada lançado.</td></tr>'}
</tbody></table>
<p class="total">Total retirado: <b>{brl(t['retiradas'])}</b></p>
<form method="post" action="/lancar" class="novo">
<input type="hidden" name="ym" value="{ym}"><input type="hidden" name="tipo" value="retirada">
<input type="hidden" name="categoria" value="avulso"><input type="hidden" name="status" value="1">
<input name="descricao" placeholder="Motivo" required>
<input name="valor" type="number" step="0.01" min="0.01" placeholder="Valor" required>
<button>Retirar</button></form>
</section>
</div>
</div>

<section class="bloco">
<h2>E se eu fizer mais diárias?</h2>
<p class="sub">Simulação, não altera nenhum registro.</p>
<form method="get" action="/mes/{ym}" class="novo">
<label>Diárias de R$ 90 <input type="number" min="0" max="60" name="s90" value="{s90}"></label>
<label>Diárias de R$ 100 <input type="number" min="0" max="60" name="s100" value="{s100}"></label>
<button>Calcular</button>
</form>
<p class="total">{texto_sim}</p>
</section>
"""
    return pagina(nome_mes(ym), corpo, "mes")


def pagina_ano(con, ano):
    linhas = []
    tot_meta = tot_ent = tot_gas = tot_sobra = 0.0
    for n in range(1, 13):
        ym = f"{ano:04d}-{n:02d}"
        garantir_mes(con, ym)
        t = totais(con, ym)
        tot_meta += t["meta"]
        tot_ent += t["entradas"]
        tot_gas += t["gastos"] + t["retiradas"]
        tot_sobra += t["sobra"] - t["inicial"]
        bate = t["sobra"] >= t["meta"]
        linhas.append(f"""<tr>
<td><a href="/mes/{ym}">{MESES[n - 1]}</a></td>
<td class="num">{brl(t["meta"])}</td>
<td class="num">{brl(t["entradas"])}</td>
<td class="num">{brl(t["gastos"] + t["retiradas"])}</td>
<td class="num">{brl(t["sobra"])}</td>
<td class="num {"bom" if bate else "falta"}">{t["pct"]:.0f}%</td>
</tr>""")
    corpo = f"""
<div class="titulo-mes">
  <a class="seta" href="/ano/{ano - 1}">&larr; {ano - 1}</a>
  <div><h1>{ano}</h1><p class="sub">Os meses futuros já trazem os valores fixos cadastrados.</p></div>
  <a class="seta" href="/ano/{ano + 1}">{ano + 1} &rarr;</a>
</div>
<section class="bloco">
<table class="grade">
<thead><tr><th>Mês</th><th class="num">Meta</th><th class="num">Entradas</th>
<th class="num">Saídas</th><th class="num">Sobra projetada</th><th class="num">Meta</th></tr></thead>
<tbody>{"".join(linhas)}</tbody>
<tfoot><tr><td>Total</td><td class="num">{brl(tot_meta)}</td><td class="num">{brl(tot_ent)}</td>
<td class="num">{brl(tot_gas)}</td><td class="num">{brl(tot_ent - tot_gas)}</td><td></td></tr></tfoot>
</table>
<p class="sub">A sobra de cada mês inclui o saldo inicial informado naquele mês; os totais somam só o que entrou e saiu.</p>
</section>"""
    return pagina(f"Ano {ano}", corpo, "ano")


def pagina_fixos(con):
    fixos = con.execute("SELECT * FROM fixos ORDER BY tipo, descricao").fetchall()
    linhas = []
    for f in fixos:
        fim = nome_mes(f["fim"]) if f["fim"] else "sem data para acabar"
        linhas.append(f"""<tr>
<td>{esc(f["descricao"])}</td>
<td>{"Entrada" if f["tipo"] == "entrada" else "Gasto"}</td>
<td class="num">{brl(f["valor"])}</td>
<td>de {nome_mes(f["inicio"])}<br>até {fim}</td>
<td><form method="post" action="/fixo/{f["id"]}/fim" class="mini">
<input type="month" name="fim" value="{f["fim"] or ""}">
<button>Definir fim</button></form></td>
<td class="acao"><form method="post" action="/fixo/{f["id"]}/remover"
 onsubmit="return confirm('Remover este fixo? Os meses já marcados como pagos ou recebidos continuam.')">
<button class="link">remover</button></form></td>
</tr>""")
    corpo = f"""
<div class="titulo-mes"><div><h1>Valores fixos</h1>
<p class="sub">Entram sozinhos em todos os meses do período. Defina o fim para parar de lançar.</p></div></div>
<section class="bloco">
<table class="grade">
<thead><tr><th>Descrição</th><th>Tipo</th><th class="num">Valor</th><th>Período</th><th></th><th></th></tr></thead>
<tbody>{"".join(linhas) or '<tr><td colspan="6" class="vazio">Nenhum fixo cadastrado.</td></tr>'}</tbody>
</table>
</section>
<section class="bloco">
<h2>Novo fixo</h2>
<form method="post" action="/fixo/novo" class="novo">
<input name="descricao" placeholder="Descrição" required>
<select name="tipo"><option value="entrada">Entrada</option><option value="gasto">Gasto</option></select>
<input name="valor" type="number" step="0.01" min="0.01" placeholder="Valor" required>
<label>Começa em <input type="month" name="inicio" value="{mes_atual()}" required></label>
<label>Termina em <input type="month" name="fim"></label>
<button>Cadastrar</button>
</form>
</section>"""
    return pagina("Fixos", corpo, "fixos")

class Handler(BaseHTTPRequestHandler):
    server_version = "Contas"

    def log_message(self, fmt, *args):
        pass

    def responder(self, texto, tipo="text/html; charset=utf-8", status=200):
        dados = texto.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def voltar(self, destino=None):
        if destino is None:
            ref = urlparse(self.headers.get("Referer", "/"))
            destino = ref.path or "/"
        self.send_response(303)
        self.send_header("Location", destino)
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        caminho = url.path
        if caminho in ("/estilo.css", "/pagina.js"):
            nome = caminho[1:]
            tipo = "text/css" if nome.endswith(".css") else "application/javascript"
            try:
                with open(os.path.join(BASE, "static", nome), encoding="utf-8") as f:
                    return self.responder(f.read(), tipo + "; charset=utf-8")
            except OSError:
                return self.responder("não encontrado", "text/plain; charset=utf-8", 404)

        con = conectar()
        try:
            if caminho == "/":
                return self.voltar(f"/mes/{mes_atual()}")
            m = re.fullmatch(r"/mes/(\d{4}-\d{2})", caminho)
            if m and RE_MES.match(m.group(1)):
                return self.responder(pagina_mes(con, m.group(1), parse_qs(url.query)))
            m = re.fullmatch(r"/ano/(\d{4})", caminho)
            if m:
                return self.responder(pagina_ano(con, int(m.group(1))))
            if caminho == "/fixos":
                return self.responder(pagina_fixos(con))
            self.responder(pagina("Não encontrado", "<h1>Página não encontrada</h1>"), status=404)
        finally:
            con.close()

    def do_POST(self):
        tam = int(self.headers.get("Content-Length", "0") or 0)
        bruto = self.rfile.read(tam).decode("utf-8")
        f = {k: v[0] for k, v in parse_qs(bruto, keep_blank_values=True).items()}
        caminho = urlparse(self.path).path
        con = conectar()
        try:
            self.tratar(con, caminho, f)
            con.commit()
        finally:
            con.close()
        self.voltar()

    def tratar(self, con, caminho, f):
        ym = f.get("ym", "")
        if caminho == "/lancar" and RE_MES.match(ym):
            tipo = f.get("tipo")
            valor = numero(f.get("valor"))
            if tipo in ("entrada", "gasto", "retirada") and valor > 0:
                data = date.today().strftime("%d/%m") if tipo == "retirada" else None
                con.execute("INSERT INTO lancamentos(ym, tipo, categoria, descricao, valor, status, data) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (ym, tipo, f.get("categoria", "avulso"),
                             f.get("descricao", "").strip() or "Sem descrição", valor,
                             1 if f.get("status") == "1" else 0, data))
            return
        m = re.fullmatch(r"/(alternar|valor|remover)/(\d+)", caminho)
        if m:
            acao, i = m.group(1), int(m.group(2))
            if acao == "alternar":
                con.execute("UPDATE lancamentos SET status = 1 - status WHERE id=?", (i,))
            elif acao == "valor":
                con.execute("UPDATE lancamentos SET valor=? WHERE id=?",
                            (max(0.0, numero(f.get("valor"))), i))
            else:
                con.execute("DELETE FROM lancamentos WHERE id=?", (i,))
            return
        if caminho == "/meta" and RE_MES.match(ym):
            con.execute("UPDATE meses SET meta=? WHERE ym=?", (max(0.0, numero(f.get("meta"))), ym))
        elif caminho == "/saldo" and RE_MES.match(ym):
            con.execute("UPDATE meses SET saldo_inicial=? WHERE ym=?", (numero(f.get("saldo")), ym))
        elif caminho == "/trazer" and RE_MES.match(ym):
            ant = somar_meses(ym, -1)
            garantir_mes(con, ant)
            con.execute("UPDATE meses SET saldo_inicial=? WHERE ym=?",
                        (totais(con, ant)["sobra"], ym))
        elif caminho == "/fixo/novo":
            inicio, fim = f.get("inicio", ""), f.get("fim", "")
            valor = numero(f.get("valor"))
            if RE_MES.match(inicio) and valor > 0 and f.get("tipo") in ("entrada", "gasto"):
                con.execute("INSERT INTO fixos(descricao, valor, tipo, inicio, fim) VALUES (?,?,?,?,?)",
                            (f.get("descricao", "").strip() or "Fixo", valor, f["tipo"], inicio,
                             fim if RE_MES.match(fim) else None))
        else:
            m = re.fullmatch(r"/fixo/(\d+)/(fim|remover)", caminho)
            if m:
                i, acao = int(m.group(1)), m.group(2)
                if acao == "fim":
                    fim = f.get("fim", "")
                    fim = fim if RE_MES.match(fim) else None
                    con.execute("UPDATE fixos SET fim=? WHERE id=?", (fim, i))
                    if fim:
                        con.execute("DELETE FROM lancamentos WHERE fixo_id=? AND ym>? AND status=0", (i, fim))
                else:
                    con.execute("DELETE FROM lancamentos WHERE fixo_id=? AND status=0", (i,))
                    con.execute("UPDATE lancamentos SET fixo_id=NULL WHERE fixo_id=?", (i,))
                    con.execute("DELETE FROM fixos WHERE id=?", (i,))


def main():
    criar_banco()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Rodando em http://127.0.0.1:{PORT}  (Ctrl+C para parar)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()