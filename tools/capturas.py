"""Gera as capturas de tela do README (docs/screenshots) com dados de exemplo.

Rodar na raiz do repositório, numa sessão gráfica:  python3 tools/capturas.py
Os dados de exemplo ficam numa pasta temporária; a pasta real (~/MyNotes) não é tocada.
"""
import os, time, tempfile
from pathlib import Path
from datetime import date, timedelta
RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = str(RAIZ / "mynotes"); SAIDA = RAIZ / "docs" / "screenshots"; SAIDA.mkdir(parents=True, exist_ok=True)
pasta = Path(tempfile.mkdtemp(prefix="mynotes-capturas-")) / "MyNotes"
os.environ["MYNOTES_APP_ID"] = "io.github.exemplo.MyNotesShots"
os.environ["MYNOTES_DIR"] = str(pasta)
import importlib.machinery, importlib.util
loader = importlib.machinery.SourceFileLoader("mynotes", SCRIPT)
spec = importlib.util.spec_from_loader("mynotes", loader); mn = importlib.util.module_from_spec(spec); loader.exec_module(mn)
mn.KDF_OPS, mn.KDF_MEM = 1, 8 << 20
mn.caminho_curto = lambda c: "~/MyNotes"   # a pasta temporária fica no lugar da pasta padrão
from gi.repository import Gio, GLib, Gtk, Graphene

hoje = date.today()
repo = mn.Repositorio(pasta); repo.preparar()
repo.salvar_tags(mn.TAGS_PADRAO + [{"nome": "leitura", "cor": "amarelo"}, {"nome": "casa", "cor": "ciano"}])

def nota(titulo, texto, tags=(), fixada=False):
    n = repo.registro(repo.novo_rel_nota(), "nota")
    n.titulo, n.texto, n.tags, n.fixada = titulo, texto, list(tags), fixada
    n.salvar(); time.sleep(0.02)
    return n

nota("Receita de pão caseiro", """## Ingredientes
- 500 g de farinha de trigo
- 10 g de sal
- 350 ml de água morna
- 7 g de fermento biológico seco

## Preparo
1. Misturar tudo e sovar por **10 minutos**.
2. Deixar crescer por 1 hora, coberto.
3. Assar a 220 °C por 35 minutos.""", ["casa"])
nota("Ideias para o blog", """Escrever uma série curta sobre *hábitos de estudo*.

## Possíveis títulos
- Como revisar sem decorar
- O caderno de erros
- Estudar 25 minutos por vez

> Começar pelo que eu mais uso no dia a dia.""", ["projetos"])
nota("Aprender Python", """- [x] Variáveis e tipos
- [x] Listas e dicionários
- [ ] Funções e módulos
- [ ] Ler e gravar arquivos
- [ ] Um pequeno projeto: lista de tarefas no terminal

Usar `python3 -m venv` para cada projeto.""", ["estudos", "programação"])
leitura = nota("Lista de leitura", """## Lendo
- [ ] *Cartas a Lucílio*, Sêneca
- [ ] *Meditações*, Marco Aurélio

## Lidas
- [x] *Memórias Póstumas de Brás Cubas*, Machado de Assis
- [x] *Manual*, Epicteto

Anotar uma frase de cada livro na área **Pensamentos**.""", ["leitura"], fixada=True)
nota("Lembretes da semana", """- [ ] Pagar a conta de luz até sexta
- [x] Marcar dentista
- [ ] Devolver o livro na biblioteca""", ["lembretes"])

diarios = {
    3: ("cansativo", "Semana começou corrida. Muitas reuniões e pouco tempo para o que importa."),
    2: ("bom", "Fui caminhar no parque de manhã cedo. O dia rendeu muito depois disso."),
    1: ("tranquilo", "Domingo calmo: cozinhei, li um pouco e liguei para a família."),
    0: ("tranquilo", """Acordei cedo e consegui estudar por uma hora antes do trabalho. À tarde a \
reunião foi longa, mas saí com uma ideia clara do próximo passo do projeto.

À noite caminhei 30 minutos e li algumas páginas das *Cartas a Lucílio*.

## Aprendizados
- Começar o dia pela tarefa mais difícil funciona melhor para mim.
- Pedir ajuda cedo economiza horas."""),
}
for atras, (humor, texto) in diarios.items():
    d = repo.registro(repo.rel_do_dia("diario", hoje - timedelta(days=atras)), "diario")
    d.humor, d.texto = humor, texto; d.salvar()

for atras, secoes, virt, dev in (
        (1, {"gratidao": "Pela tarde com a família e pela comida boa.",
             "acertos": "Não respondi com pressa a uma mensagem que me irritou.",
             "dificuldades": "Fiquei tempo demais no celular antes de dormir.",
             "dependia": "- Desligar o celular às 22h",
             "nao_dependia": "- A chuva que cancelou o passeio",
             "propositos": "- [ ] Deixar o celular fora do quarto"},
         ["temperança", "justiça"], ["temperança"]),
        (0, {"gratidao": "Pela caminhada e por um dia produtivo.",
             "acertos": "Comecei pela tarefa mais difícil.",
             "dificuldades": "Fiquei impaciente na reunião da tarde.",
             "propositos": "- [ ] Ouvir até o fim antes de responder\n- [ ] Dormir antes das 23h"},
         ["sabedoria", "coragem"], ["justiça"])):
    e = repo.registro(repo.rel_do_dia("exame", hoje - timedelta(days=atras)), "exame")
    e.texto = mn.juntar_secoes("", secoes, [])
    e.virtudes, e.desenvolver = virt, dev
    e.salvar()

app = mn.App(); app.set_flags(Gio.ApplicationFlags.NON_UNIQUE); app.register(None); app.hold()
win = mn.Janela(app, repo); win.set_default_size(1440, 900); win.set_resizable(False); win.present()
ctx = GLib.MainContext.default()

def girar(ms):
    fim = time.monotonic() + ms / 1000
    while time.monotonic() < fim:
        ctx.iteration(False); time.sleep(0.005)

def foto(nome, widget=None, focar=False):
    if not focar:
        (widget or win).set_focus(None)
    girar(600)
    w = widget or win
    p = Gtk.WidgetPaintable(widget=w)
    snap = Gtk.Snapshot()
    p.snapshot(snap, w.get_width(), w.get_height())
    tex = win.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w.get_width(), w.get_height()))
    tex.save_to_png(str(SAIDA / f"{nome}.png")); print("foto", nome, w.get_width(), w.get_height())

def dialogo():
    tops = [t for t in Gtk.Window.list_toplevels() if t is not win and t.get_visible()]
    return tops[-1] if tops else win

girar(800)
# biblioteca: uma reflexão pessoal e dois favoritos
b = win.biblioteca
b.adicionar({"classificacao": "pessoal", "texto": "O que eu adio por medo quase nunca é tão difícil quanto parece.",
             "temas": ["coragem", "hábitos"]})
for p in b.todos()[:40]:
    if p.get("autor") in ("Sêneca", "Epicteto") and len(b.favoritos) < 2:
        b.favoritar(p["id"])
girar(200)

win.modos.set_active_name("nota"); girar(300)
win.mostrar(leitura); foto("notas")
win.modos.set_active_name("diario"); girar(300); foto("diario")
win.modos.set_active_name("exame"); girar(300); foto("exame")
win.modos.set_active_name("pensamento"); girar(300)
alvo = next((p for p in b.todos() if p.get("autor") == "Marco Aurélio"), None)
if alvo:
    win.mostrar_pensamento_biblioteca(alvo)
foto("pensamentos")
win.modos.set_active_name("nota"); girar(200)
win.busca.set_text("família"); win.on_busca(); girar(500); foto("busca", focar=True)
win.busca.set_text(""); win.on_busca(); girar(300)
win.modos.set_active_name("pensamento"); dd = win.formulario_pensamento(); dd.tipo.set_active_name("verificada"); girar(400)
foto("novo-pensamento", dialogo()); dd.close(); girar(300)
# cofre
dd = win.assistente_cofre(); girar(400); foto("cofre-assistente", dialogo())
dd.pilha.set_visible_child_name("senha"); dd.campos.modo.set_active_name("completa")
dd.campos.e1.set_text("uma frase longa de exemplo"); dd.campos.e2.set_text("uma frase longa de exemplo"); girar(300)
foto("cofre-senha", dialogo())
dd.entendi.set_active(True); dd.criar()
while win.ocupado: girar(50)
girar(300); dd.abrir(); girar(500); foto("cofre-bloqueado", focar=True)
print("ok")
