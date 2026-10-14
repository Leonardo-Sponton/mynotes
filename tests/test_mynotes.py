#!/usr/bin/env python3
"""Testes do MyNotes.

Rodar (na raiz do repositório):  python3 tests/test_mynotes.py -v
Os testes usam pastas temporárias; nunca tocam na pasta de dados real (~/MyNotes).
Os testes de interface abrem uma janela de verdade por alguns segundos (precisam de
uma sessão gráfica) com outro application_id, para não mexer no app aberto.
Para pular a interface: MYNOTES_SEM_GUI=1 python3 tests/test_mynotes.py
Para testar outra cópia do app: MYNOTES_SCRIPT=/caminho/do/mynotes python3 tests/test_mynotes.py
"""
import importlib.machinery
import importlib.util
import errno
import json
import os
import stat
import tempfile
import time
import unittest
import warnings
import zipfile
from datetime import date, timedelta
from pathlib import Path

os.environ["MYNOTES_APP_ID"] = "io.github.leonardo_sponton.MyNotesTeste"
warnings.filterwarnings("ignore", message=".*EventLoopPolicy.*")  # aviso do PyGObject no Python 3.14
os.environ["MYNOTES_DIR"] = tempfile.mkdtemp(prefix="mynotes-teste-padrao-")

_caminho = Path(os.environ.get("MYNOTES_SCRIPT") or Path(__file__).resolve().parent.parent / "mynotes")
_loader = importlib.machinery.SourceFileLoader("mynotes", str(_caminho))
_spec = importlib.util.spec_from_loader("mynotes", _loader)
mn = importlib.util.module_from_spec(_spec)
_loader.exec_module(mn)

Registro, Repositorio = mn.Registro, mn.Repositorio
# parâmetros fracos do Argon2id só para os testes rodarem rápido (o app usa 4 passagens / 1 GiB)
OPS, MEM = 1, 8 << 20
mn.KDF_OPS, mn.KDF_MEM = OPS, MEM

EXAME_ANTIGO = """# Domingo, 11 de outubro (exemplo)
criada: 2026-10-11 21:50
modificada: 2026-10-11 22:20
virtudes: coragem, justiça

## Que mal curei hoje?
*Um defeito que enfrentei, um erro que corrigi.*
Organizei os documentos.

## Em que estou melhor?
- Li 20 páginas

## O que não dependia de mim
- [x] Aceitar que o pedido atrasou
"""


def tocar_mtime(p: Path):
    """Garante um mtime diferente (sistemas de arquivos com resolução grossa)."""
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mynotes-teste-")
        self.pasta = Path(self.tmp.name) / "MyNotes"
        self.repo = Repositorio(self.pasta)
        self.repo.preparar()

    def nova(self, tipo="nota", rel=None):
        return self.repo.registro(rel or self.repo.novo_rel_nota(), tipo)

    def do_dia(self, tipo, d):
        return self.repo.registro(self.repo.rel_do_dia(tipo, d), tipo)

    def caminho_do_dia(self, tipo, d):
        return self.pasta / self.repo.rel_do_dia(tipo, d)

    def tearDown(self):
        for raiz, dirs, _ in os.walk(self.tmp.name):
            for d in dirs:
                os.chmod(Path(raiz) / d, 0o700)
        self.tmp.cleanup()


class TestRegistro(Base):
    def test_nota_ida_e_volta(self):
        r = self.nova()
        r.titulo, r.texto, r.tags, r.fixada = "Ideia", "linha 1\n\n- [ ] tarefa", ["vida", "estudos"], True
        r.salvar()
        l = self.repo.registro(r.rel, "nota")
        self.assertEqual((l.titulo, l.texto, l.tags, l.fixada, l.feita),
                         ("Ideia", "linha 1\n\n- [ ] tarefa", ["vida", "estudos"], True, False))

    def test_formato_antigo_de_nota_continua_valido(self):
        p = self.pasta / "2026-10-13-01.md"
        p.write_text("# Velha\nstatus: feita\ncriada: 2026-10-13 21:00\nmodificada: 2026-10-13 21:05\n"
                     "feita_em: 2026-10-13 21:05\ntags: vida\n\nCorpo **antigo**\n", encoding="utf-8")
        r = self.repo.registro("2026-10-13-01.md", "nota")
        self.assertTrue(r.feita)
        self.assertEqual(r.feita_em, "2026-10-13 21:05")
        self.assertEqual(r.texto, "Corpo **antigo**")
        self.assertEqual(r.serializar(), p.read_text(encoding="utf-8"))

    def test_corpo_que_parece_cabecalho_nao_e_engolido(self):
        r = self.nova()
        r.texto = "tags: isto é texto, não metadado"
        r.salvar()
        l = self.repo.registro(r.rel, "nota")
        self.assertEqual(l.texto, "tags: isto é texto, não metadado")
        self.assertEqual(l.tags, [])

    def test_diario_titulo_padrao_e_humor(self):
        d = date(2026, 10, 13)
        r = self.do_dia("diario", d)
        self.assertTrue(r.vazio())
        self.assertFalse(r.existe())
        r.humor, r.texto = "tranquilo", "Dia bom."
        r.salvar()
        l = self.repo.registro(r.rel, "diario")
        self.assertEqual(l.titulo, "")                       # usa a data como título
        self.assertTrue(r.caminho.read_text().startswith("# Terça-feira, 13 de outubro"))
        self.assertEqual((l.humor, l.texto, l.data()), ("tranquilo", "Dia bom.", d))

    def test_gravacao_atomica_sem_restos_e_permissao_600(self):
        r = self.nova()
        r.texto = "x"
        r.salvar()
        self.assertEqual(stat.S_IMODE(r.caminho.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.pasta.iterdir() if p.name.endswith(".tmp")], [])

    def test_falha_de_gravacao_levanta_erro_e_preserva_original(self):
        r = self.nova()
        r.texto = "original"
        r.salvar()
        os.chmod(self.pasta, 0o500)          # pasta só leitura: não dá para criar o temporário
        try:
            r.texto = "novo"
            with self.assertRaises(OSError):
                r.salvar()
        finally:
            os.chmod(self.pasta, 0o700)
        self.assertIn("original", r.caminho.read_text())
        self.assertEqual(r.texto, "novo")    # o texto continua na memória

    def test_conflito_com_edicao_externa(self):
        r = self.nova()
        r.texto = "do app"
        r.salvar()
        r.caminho.write_text(r.caminho.read_text().replace("do app", "do editor"))
        tocar_mtime(r.caminho)
        r.texto = "do app, editado"
        with self.assertRaises(mn.ConflitoError):
            r.salvar()
        self.assertIn("do editor", r.caminho.read_text())
        r.salvar(forcar=True)
        self.assertIn("do app, editado", r.caminho.read_text())

    def test_conflito_quando_dia_virtual_foi_criado_fora(self):
        r = self.do_dia("diario", date(2026, 1, 2))
        r.caminho.write_text("# outro\n\nescrito fora\n")
        r.texto = "escrito no app"
        with self.assertRaises(mn.ConflitoError):
            r.salvar()


class TestSecoesExame(unittest.TestCase):
    def test_formato_antigo_e_preservado(self):
        corpo = EXAME_ANTIGO.split("\n\n", 1)[1]
        livre, conhecidas, extras = mn.dividir_secoes(corpo)
        self.assertEqual(livre, "")
        self.assertEqual(conhecidas, {"nao_dependia": "- [x] Aceitar que o pedido atrasou"})
        self.assertEqual(extras, [("Que mal curei hoje?", "Organizei os documentos."),
                                  ("Em que estou melhor?", "- Li 20 páginas")])
        junto = mn.juntar_secoes(livre, conhecidas, extras)
        self.assertNotIn("Um defeito que enfrentei", junto)      # instrução do modelo antigo
        self.assertIn("## Que mal curei hoje?\n\nOrganizei os documentos.", junto)
        self.assertIn("## Responsabilidade: o que não dependia de mim", junto)

    def test_secoes_vazias_nao_vao_para_o_arquivo(self):
        texto = mn.juntar_secoes("", {"gratidao": "Família", "acertos": "  "}, [("Antiga", "")])
        self.assertEqual(texto, "## Gratidão\n\nFamília")

    def test_ida_e_volta(self):
        conhecidas = {"gratidao": "a", "propositos": "- [ ] dormir cedo", "virtudes": "paciência"}
        texto = mn.juntar_secoes("solto", conhecidas, [("Velha", "v")])
        self.assertEqual(mn.dividir_secoes(texto), ("solto", conhecidas, [("Velha", "v")]))

    def test_acrescentar_em_secao(self):
        f = mn.acrescentar_em_secao
        self.assertEqual(f("", None, "oi"), "oi")
        self.assertEqual(f("a", None, "b"), "a\n\nb")
        self.assertEqual(f("a", "Aprendizados", "x"), "a\n\n## Aprendizados\n\nx")
        texto = "a\n\n## Aprendizados\n\nx\n\n## Observações pessoais\n\nz"
        self.assertEqual(f(texto, "Aprendizados", "y"),
                         "a\n\n## Aprendizados\n\nx\n\ny\n\n## Observações pessoais\n\nz")


class TestBusca(Base):
    def criar(self, tipo, rel, texto, titulo=""):
        r = self.repo.registro(rel, tipo)
        r.texto, r.titulo = texto, titulo
        r.salvar()
        return r

    def test_busca_global_tipos_periodo_e_acentos(self):
        hoje = date.today()
        n = self.criar("nota", self.repo.novo_rel_nota(), "Estudar consciência", "Docker")
        d = self.criar("diario", self.repo.rel_do_dia("diario", hoje), "Pensei na Consciencia")
        e = self.criar("exame", self.repo.rel_do_dia("exame", hoje - timedelta(days=60)),
                       "## Gratidão\n\nconsciência tranquila")
        todos = [n, d, e]
        achados = lambda *a, **k: {r.tipo for r in mn.buscar(todos, *a, **k)}  # noqa: E731
        self.assertEqual(achados("conSCIENCIA"), {"nota", "diario", "exame"})
        self.assertEqual(achados("consciência", tipos={"diario"}), {"diario"})
        self.assertEqual(achados("consciência", desde=hoje - timedelta(days=7)), {"nota", "diario"})
        self.assertEqual(achados("docker"), {"nota"})               # título

    def test_trecho_destaca_o_termo(self):
        antes, achado, depois = mn.trecho("**Hoje** fui à praia com a família", "PRAIA")
        self.assertEqual(achado, "praia")
        self.assertTrue(antes.endswith("à "))
        self.assertNotIn("*", antes)


class TestLixeiraExportacaoBackup(Base):
    def popular(self):
        n = self.nova()
        n.titulo, n.texto, n.tags = "Nota", "conteúdo da nota", ["vida"]
        n.salvar()
        d = self.do_dia("diario", date(2026, 10, 13))
        d.texto, d.humor = "Texto do diário", "bom"
        d.salvar()
        e = self.caminho_do_dia("exame", date(2026, 10, 11))
        e.write_text(EXAME_ANTIGO, encoding="utf-8")
        return n, d, self.do_dia("exame", date(2026, 10, 11))

    def test_lixeira_e_desfazer(self):
        n, d, _ = self.popular()
        destino = self.repo.para_lixeira(d)
        self.assertFalse(d.caminho.exists())
        self.assertTrue((self.pasta / destino).exists())
        self.assertEqual(stat.S_IMODE((self.pasta / ".lixeira" / "diario").stat().st_mode), 0o700)
        self.assertTrue(self.repo.restaurar_da_lixeira(destino, d.rel))
        self.assertEqual(self.repo.registro(d.rel, "diario").texto, "Texto do diário")
        # não sobrescreve um registro que apareceu no lugar
        destino = self.repo.para_lixeira(n)
        n.caminho.write_text("# outra\n\nnova")
        self.assertFalse(self.repo.restaurar_da_lixeira(destino, n.rel))
        self.assertTrue((self.pasta / destino).exists())

    def test_exportacao_markdown_e_json(self):
        n, d, e = self.popular()
        destino = Path(self.tmp.name) / "export"
        destino.mkdir()
        alvo = self.repo.exportar(destino)
        self.assertEqual((alvo / "notas" / n.caminho.name).read_bytes(), n.caminho.read_bytes())
        self.assertEqual((alvo / "diario" / d.caminho.name).read_bytes(), d.caminho.read_bytes())
        self.assertEqual((alvo / "exames" / e.caminho.name).read_bytes(), e.caminho.read_bytes())
        dados = json.loads((alvo / "mynotes.json").read_text())
        self.assertEqual(dados["notas"][0]["texto"], "conteúdo da nota")
        self.assertEqual(dados["diario"][0]["humor"], "bom")
        self.assertEqual(dados["exames"][0]["virtudes"], ["coragem", "justiça"])
        self.assertIn("Que mal curei hoje?", dados["exames"][0]["secoes"])
        self.assertEqual(stat.S_IMODE(alvo.stat().st_mode), 0o700)

    def test_backup_e_restauracao(self):
        n, d, e = self.popular()
        zipado = Path(self.tmp.name) / "b.zip"
        self.assertEqual(self.repo.fazer_backup(zipado), 3)
        with zipfile.ZipFile(zipado) as z:
            self.assertEqual(sorted(z.namelist()),
                             sorted([".tags.json", n.caminho.name, "diario/2026-10-13.md",
                                     "exames/2026-10-11.md"]))
        # estraga e apaga, depois restaura
        d.texto = "estragado"
        d.salvar()
        n.caminho.unlink()
        restaurados, ignorados, copia = self.repo.restaurar_backup(zipado)
        self.assertEqual((restaurados, ignorados), (3, 0))
        self.assertEqual(self.repo.registro(d.rel, "diario").texto, "Texto do diário")
        self.assertTrue(n.caminho.exists())
        with zipfile.ZipFile(copia) as z:          # cópia do estado anterior
            self.assertIn("estragado", z.read("diario/2026-10-13.md").decode())

    def test_restauracao_rejeita_caminhos_perigosos(self):
        zipado = Path(self.tmp.name) / "mau.zip"
        with zipfile.ZipFile(zipado, "w") as z:
            z.writestr("../fora.md", "x")
            z.writestr("/etc/abs.md", "x")
            z.writestr("diario/../../fora2.md", "x")
            z.writestr("script.sh", "x")
            z.writestr("ok.md", "# ok\n\nok")
        restaurados, ignorados, _ = self.repo.restaurar_backup(zipado)
        self.assertEqual((restaurados, ignorados), (1, 4))
        self.assertFalse((Path(self.tmp.name) / "fora.md").exists())
        self.assertFalse((Path(self.tmp.name) / "fora2.md").exists())

    def test_zip_que_nao_e_backup(self):
        zipado = Path(self.tmp.name) / "outro.zip"
        with zipfile.ZipFile(zipado, "w") as z:
            z.writestr("foto.jpg", "x")
        with self.assertRaises(ValueError):
            self.repo.restaurar_backup(zipado)

    def test_preparar_fecha_permissoes(self):
        p = self.pasta / "aberta.md"
        p.write_text("# a\n\nb")
        os.chmod(p, 0o644)
        os.chmod(self.pasta, 0o755)
        self.repo.preparar()
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.pasta.stat().st_mode), 0o700)

    def test_arquivo_ilegivel_nao_derruba_o_carregamento(self):
        self.popular()
        (self.pasta / "binario.md").write_bytes(b"\xff\xfe\x00lixo")
        notas = self.repo.carregar("nota")
        self.assertEqual(len(notas), 1)
        self.assertEqual(len(self.repo.erros), 1)


SENHA = "uma frase longa de teste"
SEM_NACL = not mn.tem_criptografia()
MOTIVO_NACL = "PyNaCl não instalado (sudo pacman -S python-pynacl)"


def todos_os_bytes(pasta: Path):
    """Conteúdo de todos os arquivos (e nomes) dentro da pasta, para procurar texto vazado."""
    saida = b""
    for p in sorted(Path(pasta).rglob("*")):
        saida += p.name.encode() + b"\n"
        if p.is_file():
            saida += p.read_bytes() + b"\n"
    return saida


@unittest.skipIf(SEM_NACL, MOTIVO_NACL)
class TestCripto(unittest.TestCase):
    def test_cifrar_decifrar_e_autenticidade(self):
        chave = os.urandom(32)
        blob = mn.cifrar(chave, b"segredo", b"blob/x")
        self.assertNotIn(b"segredo", blob)
        self.assertEqual(mn.decifrar(chave, blob, b"blob/x"), b"segredo")
        self.assertNotEqual(blob, mn.cifrar(chave, b"segredo", b"blob/x"))   # nonce novo
        estragado = bytearray(blob)
        estragado[-1] ^= 1
        for chave_, blob_, ctx in ((chave, bytes(estragado), b"blob/x"), (os.urandom(32), blob, b"blob/x"),
                                   (chave, blob, b"blob/y"), (chave, b"lixo", b"blob/x")):
            with self.assertRaises(mn.CofreDanificado):
                mn.decifrar(chave_, blob_, ctx)

    def test_envelope_senha_certa_errada_e_acentos(self):
        chave = os.urandom(32)
        env = mn.criar_envelope(chave, "senha", "café com açúcar!", OPS, MEM)
        self.assertNotIn("café", json.dumps(env))
        self.assertEqual(mn.abrir_envelope(env, "senha", "café com açúcar!"), chave)
        # "é" composto de outro jeito (NFD) vale igual
        self.assertEqual(mn.abrir_envelope(env, "senha", "café com açúcar!"), chave)
        with self.assertRaises(mn.SenhaIncorreta):
            mn.abrir_envelope(env, "senha", "café com açúcar")
        with self.assertRaises(mn.SenhaIncorreta):            # envelope de outro tipo
            mn.abrir_envelope(env, "recuperacao", "café com açúcar!")
        env["mem"] = 1 << 40                                       # parâmetro adulterado
        with self.assertRaises(mn.CofreDanificado):
            mn.abrir_envelope(env, "senha", "café com açúcar!")

    def test_validacao_das_senhas(self):
        num = lambda s: mn.avaliar_senha(s, True)    # noqa: E731
        comp = lambda s: mn.avaliar_senha(s, False)  # noqa: E731
        for ruim in ("1234", "12345678901", "123456789012", "111111111111", "121212121212",
                     "987654321098", "12345678901a", "123123123123"):
            self.assertIsNotNone(num(ruim), ruim)
        self.assertIsNone(num("839201746153"))
        for ruim in ("curta", "abcdefghijkl", "aaaaaaaaaaaa", "            ", "abcabcabcabc"):
            self.assertIsNotNone(comp(ruim), ruim)
        for boa in ("uma frase longa de teste", "Tr3m-azul#2026!", "839201746153"):
            self.assertIsNone(comp(boa), boa)


class BaseCofre(Base):
    """Uma pasta com cofre (criado vazio) e o repositório já desbloqueado."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mynotes-teste-cofre-")
        self.pasta = Path(self.tmp.name) / "MyNotes"
        mn.migrar_para_cofre(self.pasta, SENHA, OPS, MEM)
        self.repo = self.abrir()
        self.repo.preparar()

    def abrir(self, senha=SENHA, tipo="senha"):
        chave = mn.abrir_cofre(self.pasta, senha, tipo)
        return Repositorio(self.pasta, mn.ArmazemCofre(self.pasta, chave))


@unittest.skipIf(SEM_NACL, MOTIVO_NACL)
class TestCofre(BaseCofre):
    def popular(self):
        n = self.nova()
        n.titulo, n.texto, n.tags = "Plano secreto", "conteúdo confidencial da nota", ["vida"]
        n.salvar()
        d = self.do_dia("diario", date(2026, 10, 13))
        d.texto, d.humor = "Diário íntimo de hoje", "tranquilo"
        d.salvar()
        e = self.do_dia("exame", date(2026, 10, 11))
        e.texto, e.virtudes = "## Gratidão\n\nPela família", ["coragem"]
        e.salvar()
        return n, d, e

    def test_cria_desbloqueia_e_persiste(self):
        n, d, e = self.popular()
        self.repo.armazem.fechar()
        with self.assertRaises(mn.SenhaIncorreta):
            self.abrir("senha errada qualquer")
        repo = self.abrir()                       # "fechar e abrir o app"
        self.assertEqual(sorted(repo.arquivos()), sorted([n.rel, d.rel, e.rel]))
        l = repo.registro(d.rel, "diario")
        self.assertEqual((l.texto, l.humor), ("Diário íntimo de hoje", "tranquilo"))
        self.assertEqual(repo.registro(e.rel, "exame").virtudes, ["coragem"])
        self.assertEqual(repo.registro(n.rel, "nota").tags, ["vida"])
        self.assertEqual(repo.carregar_tags()[0]["nome"], "programação")

    def test_nada_legivel_no_disco(self):
        self.popular()
        r = self.repo.registro(self.repo.novo_rel_nota(), "nota")
        r.texto = "para a lixeira"
        r.salvar()
        self.repo.para_lixeira(r)
        b = mn.Biblioteca([], self.repo.armazem)
        b.adicionar({"classificacao": "pessoal", "texto": "Reflexão muito particular"})
        tudo = todos_os_bytes(self.pasta)
        for palavra in ("Plano secreto", "confidencial", "Diário íntimo", "tranquilo", "Gratidão",
                        "família", "coragem", "programação", "2026-10-13", "diario", "exames",
                        "lixeira", "Reflexão muito particular", "vida", ".md", SENHA):
            self.assertNotIn(palavra.encode(), tudo, palavra)
        for p in (self.pasta / "cofre").iterdir():
            self.assertRegex(p.name, r"^[0-9a-f]{32}\.bin$")
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.pasta / "cofre").stat().st_mode), 0o700)
        self.assertEqual([p for p in self.pasta.rglob("*.tmp")], [])
        self.assertEqual(sorted(p.name for p in self.pasta.iterdir()), ["cofre", "cofre.json"])

    def test_tamanho_cifrado_em_blocos(self):
        n = self.nova()
        n.texto = "x"
        n.salvar()
        tam = self.repo.armazem.arquivo_de(n.rel).stat().st_size
        self.assertEqual((tam - 4 - 24 - 16) % mn.BLOCO_PAD, 0)

    def test_arquivo_alterado_ou_trocado_e_detectado(self):
        n, d, e = self.popular()
        arq_d = self.repo.armazem.arquivo_de(d.rel)
        arq_e = self.repo.armazem.arquivo_de(e.rel)
        dados = bytearray(arq_d.read_bytes())
        dados[40] ^= 0xFF
        arq_d.write_bytes(bytes(dados))
        arq_e.write_bytes(self.repo.armazem.arquivo_de(n.rel).read_bytes())   # cópia de outro arquivo
        repo = self.abrir()
        self.assertEqual(list(repo.arquivos()), [n.rel])
        self.assertEqual(len(repo.armazem.erros), 2)
        self.assertTrue(arq_d.exists())          # nada é apagado

    def test_trocar_senha_e_recuperacao(self):
        n, *_ = self.popular()
        chave = self.repo.armazem._chave
        mn.trocar_senha(self.pasta, chave, "outra senha bem longa", OPS, MEM)
        with self.assertRaises(mn.SenhaIncorreta):
            self.abrir()
        self.assertEqual(self.abrir("outra senha bem longa").registro(n.rel, "nota").texto,
                         "conteúdo confidencial da nota")
        self.assertFalse(mn.tem_recuperacao(self.pasta))
        codigo = mn.criar_recuperacao(self.pasta, chave, OPS, MEM)
        self.assertRegex(codigo, r"^([A-Z2-9]{5}-){4}[A-Z2-9]{5}$")
        self.assertNotIn(codigo.encode(), todos_os_bytes(self.pasta))
        aberto = self.abrir(mn.normalizar_codigo(codigo.lower().replace("-", " ")), "recuperacao")
        self.assertEqual(aberto.registro(n.rel, "nota").texto, "conteúdo confidencial da nota")
        mn.remover_recuperacao(self.pasta)
        with self.assertRaises(mn.SenhaIncorreta):
            self.abrir(mn.normalizar_codigo(codigo), "recuperacao")
        self.assertIsNotNone(self.abrir("outra senha bem longa"))

    def test_edicao_lixeira_conflito_e_busca(self):
        n, d, e = self.popular()
        d.texto += "\nmais uma linha"
        d.salvar()
        self.assertIn("mais uma linha", self.abrir().registro(d.rel, "diario").texto)
        destino = self.repo.para_lixeira(n)
        self.assertFalse(n.existe())
        self.assertEqual(self.repo.lixeira(), [destino])
        self.assertTrue(self.repo.restaurar_da_lixeira(destino, n.rel))
        self.assertEqual(self.repo.registro(n.rel, "nota").titulo, "Plano secreto")
        # outra "instância" grava o mesmo registro: a próxima gravação acusa conflito
        outro = self.abrir().registro(d.rel, "diario")
        outro.texto = "escrito por outro"
        outro.salvar()
        tocar_mtime(self.repo.armazem.arquivo_de(d.rel))
        d.texto = "escrito aqui"
        with self.assertRaises(mn.ConflitoError):
            d.salvar()
        copia = self.repo.guardar_copia(d)
        self.assertTrue(copia.startswith(".lixeira/conflitos/"))
        d.carregar()
        self.assertEqual(d.texto, "escrito por outro")
        todos = [self.repo.registro(r, t) for t in mn.TIPOS for r in self.repo.arquivos(t)]
        self.assertEqual([r.tipo for r in mn.buscar(todos, "confidencial")], ["nota"])
        self.assertEqual([r.tipo for r in mn.buscar(todos, "FAMILIA")], ["exame"])

    def test_lixeira_no_cofre_restaurar_e_nomes(self):
        n, d, e = self.popular()
        dn = self.repo.para_lixeira(d)
        self.repo.para_lixeira(n)
        outro = self.do_dia("diario", date(2026, 10, 13))
        outro.texto = "novo no mesmo dia"
        outro.salvar()
        dn2 = self.repo.para_lixeira(outro)              # mesmo nome: ganha sufixo
        self.assertNotEqual(dn, dn2)
        self.assertEqual(len(self.repo.lixeira()), 3)
        tipo, original, reg = self.repo.item_lixeira(dn2)
        self.assertEqual((tipo, original, reg.texto), ("diario", d.rel, "novo no mesmo dia"))
        self.assertEqual(self.repo.restaurar_item_lixeira(dn2), d.rel)
        self.assertIsNone(self.repo.restaurar_item_lixeira(dn))   # já existe diário nesse dia
        self.assertEqual(len(self.repo.lixeira()), 2)

    def test_backup_cifrado_e_restauracao(self):
        n, d, e = self.popular()
        b = mn.Biblioteca([], self.repo.armazem)
        meu = b.adicionar({"classificacao": "pessoal", "texto": "Pensamento só meu"})
        zipado = Path(self.tmp.name) / "b.zip"
        self.assertEqual(self.repo.fazer_backup(zipado), 3)
        with zipfile.ZipFile(zipado) as z:
            nomes = z.namelist()
            conteudo = b"".join(z.read(x) for x in nomes)
        self.assertIn("cofre.json", nomes)
        self.assertEqual(len([x for x in nomes if x.startswith("cofre/")]), 5)   # 3 + tags + pensamentos
        for palavra in (b"confidencial", "Diário".encode(), "só meu".encode(), b"2026-10-13"):
            self.assertNotIn(palavra, conteudo + zipado.read_bytes())
        # estraga, apaga, restaura
        d.texto = "estragado"
        d.salvar()
        self.repo.armazem.apagar(n.rel)
        self.repo.armazem.apagar(".pensamentos.json")
        restaurados, ignorados, copia = self.repo.restaurar_backup(zipado)
        self.assertEqual((restaurados, ignorados), (3, 0))
        repo = self.abrir()
        self.assertEqual(repo.registro(d.rel, "diario").texto, "Diário íntimo de hoje")
        self.assertTrue(repo.armazem.existe(n.rel))
        self.assertEqual([p["texto"] for p in mn.Biblioteca([], repo.armazem).proprios],
                         [meu["texto"]])
        with zipfile.ZipFile(copia) as z:          # a cópia de segurança também é cifrada
            self.assertIn("cofre.json", z.namelist())
            self.assertNotIn(b"estragado", b"".join(z.read(x) for x in z.namelist()))

    def test_backup_de_outra_senha_e_backup_adulterado(self):
        self.popular()
        zipado = Path(self.tmp.name) / "b.zip"
        self.repo.fazer_backup(zipado)
        # um segundo cofre (outra chave) tenta restaurar este backup
        pasta2 = Path(self.tmp.name) / "Outro"
        mn.migrar_para_cofre(pasta2, "senha do segundo cofre", OPS, MEM)
        repo2 = Repositorio(pasta2, mn.ArmazemCofre(pasta2, mn.abrir_cofre(pasta2, "senha do segundo cofre")))
        with self.assertRaises(mn.PrecisaSenha):
            repo2.restaurar_backup(zipado)
        with self.assertRaises(mn.SenhaIncorreta):
            repo2.restaurar_backup(zipado, "senha errada demais")
        self.assertEqual(repo2.restaurar_backup(zipado, SENHA)[0], 3)
        # o que foi restaurado foi cifrado de novo com a chave do segundo cofre
        repo2b = Repositorio(pasta2, mn.ArmazemCofre(pasta2, mn.abrir_cofre(pasta2, "senha do segundo cofre")))
        self.assertEqual(len(list(repo2b.arquivos())), 3)
        self.assertEqual(repo2b.armazem.erros, [])
        # backup adulterado: nada é restaurado, nada muda
        ruim = Path(self.tmp.name) / "ruim.zip"
        with zipfile.ZipFile(zipado) as z, zipfile.ZipFile(ruim, "w") as w:
            for i, nome in enumerate(z.namelist()):
                dados = bytearray(z.read(nome))
                if nome.startswith("cofre/") and i == 3:
                    dados[50] ^= 1
                w.writestr(nome, bytes(dados))
        antes = todos_os_bytes(self.pasta)
        with self.assertRaises(ValueError):
            self.repo.restaurar_backup(ruim)
        self.assertEqual(todos_os_bytes(self.pasta), antes)

    def test_backup_cifrado_nao_restaura_sem_cofre(self):
        self.popular()
        zipado = Path(self.tmp.name) / "b.zip"
        self.repo.fazer_backup(zipado)
        comum = Repositorio(Path(self.tmp.name) / "Comum")
        comum.preparar()
        with self.assertRaises(ValueError):
            comum.restaurar_backup(zipado)
        self.assertEqual(list(comum.arquivos()), [])

    def test_restauracao_que_falha_no_meio_volta_atras(self):
        n, d, e = self.popular()
        zipado = Path(self.tmp.name) / "b.zip"
        self.repo.fazer_backup(zipado)
        d.texto = "versão atual"
        d.salvar()
        self.repo.armazem.apagar(e.rel)
        antes = {r: self.repo.armazem.ler(r) for r in self.repo.armazem.todos()}
        original = self.repo.armazem.gravar
        chamadas = []

        def falha(rel, dados, mod=None):
            chamadas.append(rel)
            if len(chamadas) == 3:
                raise OSError(errno.ENOSPC, "disco cheio (simulado)")
            return original(rel, dados, mod)
        self.repo.armazem.gravar = falha
        with self.assertRaises(OSError):
            self.repo.restaurar_backup(zipado)
        self.repo.armazem.gravar = original
        depois = {r: self.repo.armazem.ler(r) for r in self.repo.armazem.todos()}
        self.assertEqual(depois, antes)

    def test_plaintext_so_na_exportacao_pedida(self):
        n, d, e = self.popular()
        destino = Path(self.tmp.name) / "export"
        destino.mkdir()
        antes = todos_os_bytes(self.pasta)
        alvo = self.repo.exportar(destino)
        self.assertIn("Diário íntimo", (alvo / "diario" / "2026-10-13.md").read_text())
        dados = json.loads((alvo / "mynotes.json").read_text())
        self.assertEqual(dados["notas"][0]["titulo"], "Plano secreto")
        self.assertEqual(todos_os_bytes(self.pasta), antes)     # nada novo na pasta do cofre


@unittest.skipIf(SEM_NACL, MOTIVO_NACL)
class TestMigracao(Base):
    def popular(self):
        n = self.nova()
        n.titulo, n.texto, n.tags = "Nota antiga", "texto da nota", ["vida"]
        n.salvar()
        sem_mod = self.pasta / "2026-10-01-01.md"            # formato bem antigo, sem "modificada:"
        sem_mod.write_text("# Velhíssima\n\ncorpo\n", encoding="utf-8")
        os.utime(sem_mod, (1_700_000_000, 1_700_000_000))
        d = self.do_dia("diario", date(2026, 10, 12))
        d.texto, d.humor = "Diário de ontem", "bom"
        d.salvar()
        self.caminho_do_dia("exame", date(2026, 10, 11)).write_text(EXAME_ANTIGO, encoding="utf-8")
        lixo = self.nova()
        lixo.texto = "apagada"
        lixo.salvar()
        self.repo.para_lixeira(lixo)
        mn.Biblioteca([], self.repo.armazem).adicionar({"classificacao": "pessoal", "texto": "meu"})
        (self.pasta / ".seguranca").mkdir()
        self.repo.fazer_backup(self.pasta / ".seguranca" / "antigo.zip")
        return {p.relative_to(self.pasta).as_posix(): p.read_bytes()
                for p in self.pasta.rglob("*") if p.is_file()}

    def test_migracao_completa_preserva_tudo(self):
        antes = self.popular()
        modificada_antes = self.repo.registro("2026-10-01-01.md", "nota").modificada
        exame_antes = self.repo.registro("exames/2026-10-11.md", "exame")
        rel = mn.migrar_para_cofre(self.pasta, SENHA, OPS, MEM)
        self.assertEqual(rel["total"], 7)       # 2 notas, diário, exame, lixeira, tags, pensamentos
        self.assertEqual(rel["por_tipo"], {"nota": 2, "diario": 1, "exame": 1, "lixeira": 1,
                                           "auxiliares": 2})
        # originais intactos
        for r, dados in antes.items():
            self.assertEqual((self.pasta / r).read_bytes(), dados, r)
        with zipfile.ZipFile(rel["copia"]) as z:
            self.assertEqual(len(z.namelist()), 7)
        # o cofre abre e tem o mesmo conteúdo
        repo = Repositorio(self.pasta, mn.ArmazemCofre(self.pasta, mn.abrir_cofre(self.pasta, SENHA)))
        self.assertEqual(repo.registro("2026-10-01-01.md", "nota").modificada, modificada_antes)
        ex = repo.registro("exames/2026-10-11.md", "exame")
        self.assertEqual((ex.texto, ex.virtudes, ex.criada), (exame_antes.texto, exame_antes.virtudes,
                                                                exame_antes.criada))
        self.assertEqual(mn.dividir_secoes(ex.texto), mn.dividir_secoes(exame_antes.texto))
        self.assertEqual(len(repo.lixeira()), 1)
        self.assertEqual(mn.Biblioteca([], repo.armazem).proprios[0]["texto"], "meu")
        # remover os originais: só depois, e só o que confere
        (self.pasta / "diario" / "2026-10-12.md").write_text("mudado depois da migração")
        removidos, mantidos = mn.remover_texto_puro(self.pasta, repo.armazem)
        self.assertEqual(len(removidos), 6)
        self.assertEqual(sorted(p.name for p, _ in mantidos),
                         sorted(["2026-10-12.md", "antigo.zip", rel["copia"].name]))
        removidos, mantidos = mn.remover_texto_puro(self.pasta, repo.armazem, incluir_copias=True)
        self.assertEqual(len(mantidos), 1)            # o diário alterado continua lá
        self.assertFalse((self.pasta / ".lixeira").exists())

    def test_migracao_interrompida_preserva_originais_e_pode_ser_retomada(self):
        antes = self.popular()
        with self.assertRaises(OSError):
            mn.migrar_para_cofre(self.pasta, SENHA, OPS, MEM, _falhar_apos=3)
        self.assertFalse(mn.cofre_existe(self.pasta))
        self.assertFalse((self.pasta / mn.DIR_COFRE_NOVO).exists())
        self.assertFalse((self.pasta / "cofre").exists())
        for r, dados in antes.items():
            self.assertEqual((self.pasta / r).read_bytes(), dados, r)
        # o app continua no modo comum, lendo os originais
        self.assertEqual(len(Repositorio(self.pasta).carregar("nota")), 2)
        # nova tentativa funciona
        self.assertEqual(mn.migrar_para_cofre(self.pasta, SENHA, OPS, MEM)["total"], 7)

    def test_restos_de_efetivacao_interrompida(self):
        self.popular()
        # simula uma queda entre mover a pasta cifrada e gravar o cofre.json
        (self.pasta / "cofre").mkdir()
        (self.pasta / "cofre" / ("ab" * 16 + ".bin")).write_bytes(b"IDC1...")
        mn.migrar_para_cofre(self.pasta, SENHA, OPS, MEM)
        repo = Repositorio(self.pasta, mn.ArmazemCofre(self.pasta, mn.abrir_cofre(self.pasta, SENHA)))
        self.assertEqual(repo.armazem.erros, [])
        # uma pasta temporária com algo que não é do app não é apagada
        pasta2 = Path(self.tmp.name) / "P2"
        (pasta2 / mn.DIR_COFRE_NOVO).mkdir(parents=True)
        (pasta2 / mn.DIR_COFRE_NOVO / "importante.txt").write_text("não apague")
        with self.assertRaises(mn.CofreError):
            mn.migrar_para_cofre(pasta2, SENHA, OPS, MEM)
        self.assertTrue((pasta2 / mn.DIR_COFRE_NOVO / "importante.txt").exists())

    def test_senha_fraca_e_cofre_existente_sao_recusados(self):
        self.popular()
        with self.assertRaises(mn.CofreError):
            mn.migrar_para_cofre(self.pasta, "123", OPS, MEM)
        mn.migrar_para_cofre(self.pasta, SENHA, OPS, MEM)
        with self.assertRaises(mn.CofreError):
            mn.migrar_para_cofre(self.pasta, SENHA, OPS, MEM)


class TestBiblioteca(Base):
    PUBLICOS = [
        {"id": "sen-1", "classificacao": "verificada", "texto": "Só o tempo é nosso.",
         "autor": "Sêneca", "obra": "Cartas a Lucílio", "referencia": "1.3", "tradicao": "Estoicismo",
         "temas": ["tempo"], "fonte": "https://exemplo/sen"},
        {"id": "con-1", "classificacao": "verificada", "texto": "Saber o que se sabe e o que não se sabe.",
         "autor": "Confúcio", "obra": "Analectos", "referencia": "2.17", "tradicao": "Confucionismo",
         "temas": ["conhecimento", "humildade"], "fonte": "https://exemplo/con"},
        {"id": "inc-1", "classificacao": "incerta", "texto": "Frase de origem duvidosa.",
         "autor": "Alguém Famoso", "tradicao": "", "temas": []},
        {"id": "ruim", "classificacao": "verificada", "texto": "Sem fonte", "autor": "X"},   # inválido
    ]

    def setUp(self):
        super().setUp()
        self.arq = Path(self.tmp.name) / "pub.json"
        self.arq.write_text(json.dumps({"pensamentos": self.PUBLICOS}), encoding="utf-8")
        self.publicos, erro = mn.carregar_biblioteca_publica(self.arq)
        self.assertIsNone(erro)
        self.b = mn.Biblioteca(self.publicos, self.repo.armazem)

    def test_publicos_invalidos_ficam_de_fora(self):
        self.assertEqual([p["id"] for p in self.publicos], ["sen-1", "con-1", "inc-1"])

    def test_cadastro_e_validacao(self):
        for ruim in ({"classificacao": "verificada", "texto": "a", "autor": "B"},          # sem fonte
                     {"classificacao": "verificada", "texto": "a", "fonte": "livro"},     # sem autor
                     {"classificacao": "incerta", "texto": "a"},                         # sem autor
                     {"classificacao": "pessoal", "texto": "   "},
                     {"classificacao": "pessoal", "texto": "x" * 2000},
                     {"classificacao": "outra", "texto": "a"}):
            with self.assertRaises(ValueError):
                self.b.adicionar(ruim)
        meu = self.b.adicionar({"classificacao": "pessoal", "texto": "  Calma antes de responder. ",
                                "temas": ["Paciência", "paciência", " calma "]})
        self.assertEqual((meu["texto"], meu["autor"], meu["temas"]),
                         ("Calma antes de responder.", "", ["paciência", "calma"]))
        cit = self.b.adicionar({"classificacao": "verificada", "texto": "Conhece-te a ti mesmo.",
                                "autor": "Inscrição de Delfos", "fonte": "Platão, Cármides 164d",
                                "tradicao": "Filosofia grega"})
        self.assertTrue(cit["id"].startswith("u-"))
        self.assertIn("Filosofia grega", self.b.tradicoes())

    def test_duplicatas(self):
        with self.assertRaises(ValueError):
            self.b.adicionar({"classificacao": "verificada", "texto": "só o TEMPO é nosso",
                              "autor": "Seneca", "fonte": "x"})
        self.b.adicionar({"classificacao": "pessoal", "texto": "Uma ideia."})
        with self.assertRaises(ValueError):
            self.b.adicionar({"classificacao": "pessoal", "texto": "uma ideia"})
        # mesmo texto, outro autor: permitido
        self.b.adicionar({"classificacao": "incerta", "texto": "Uma ideia.", "autor": "Outro"})

    def test_editar_e_excluir_so_os_proprios(self):
        with self.assertRaises(ValueError):
            self.b.editar("sen-1", {"classificacao": "pessoal", "texto": "mudado"})
        with self.assertRaises(ValueError):
            self.b.excluir("sen-1")
        p = self.b.adicionar({"classificacao": "pessoal", "texto": "Primeira versão"})
        self.b.favoritar(p["id"])
        self.b.escolher(date(2026, 10, 13), p["id"])
        self.b.editar(p["id"], {"classificacao": "pessoal", "texto": "Segunda versão", "temas": ["x"]})
        self.assertEqual(self.b.por_id(p["id"])["texto"], "Segunda versão")
        desfazer = self.b.excluir(p["id"])
        self.assertIsNone(self.b.por_id(p["id"]))
        self.assertNotIn(p["id"], self.b.favoritos)
        self.assertFalse(self.b.do_dia(date(2026, 10, 13))[1])
        self.b.restaurar(desfazer)
        b2 = mn.Biblioteca(self.publicos, self.repo.armazem)
        self.assertEqual(b2.por_id(p["id"])["texto"], "Segunda versão")
        self.assertIn(p["id"], b2.favoritos)
        self.assertEqual(b2.do_dia(date(2026, 10, 13)), (b2.por_id(p["id"]), True))

    def test_busca_e_filtros(self):
        meu = self.b.adicionar({"classificacao": "pessoal", "texto": "Tempo com a família",
                                "temas": ["tempo"]})
        ids = lambda **k: [p["id"] for p in self.b.filtrar(**k)]   # noqa: E731
        self.assertEqual(ids(termo="seneca"), ["sen-1"])                     # autor, sem acento
        self.assertEqual(ids(termo="analectos"), ["con-1"])                 # obra
        self.assertEqual(ids(termo="TEMPO"), ["sen-1", meu["id"]])           # texto e tema
        self.assertEqual(ids(autor="Confúcio"), ["con-1"])
        self.assertEqual(ids(tradicao="Estoicismo"), ["sen-1"])
        self.assertEqual(ids(tema="humildade"), ["con-1"])
        self.assertEqual(ids(origem="usuario"), [meu["id"]])
        self.assertEqual(len(ids(origem="app")), 3)
        self.assertEqual(ids(classificacao="incerta"), ["inc-1"])
        self.b.favoritar("con-1")
        self.assertEqual(ids(so_favoritos=True), ["con-1"])
        self.assertEqual(self.b.temas(), ["conhecimento", "humildade", "tempo"])

    def test_pensamento_do_dia_automatico_e_escolha_manual(self):
        d = date(2026, 10, 13)
        p, manual = self.b.do_dia(d)
        self.assertFalse(manual)
        self.assertEqual(p, self.b.do_dia(d)[0])            # estável no mesmo dia
        self.assertIn(p["id"], ["sen-1", "con-1", "inc-1"])
        self.b.escolher(d, "con-1")
        self.assertEqual(self.b.do_dia(d), (self.b.por_id("con-1"), True))
        self.assertFalse(self.b.do_dia(d + timedelta(days=1))[1])   # o dia seguinte não muda
        b2 = mn.Biblioteca(self.publicos, self.repo.armazem)    # persiste ao "reabrir"
        self.assertEqual(b2.do_dia(d)[0]["id"], "con-1")

    def test_aleatorio(self):
        import random as rnd
        vistos = {self.b.aleatorio(evitar="sen-1", rng=rnd.Random(i))["id"] for i in range(40)}
        self.assertEqual(vistos, {"con-1", "inc-1"})

    def test_backup_e_restauracao_preservam_pensamentos(self):
        p = self.b.adicionar({"classificacao": "pessoal", "texto": "Guardar isto"})
        self.b.favoritar("sen-1")
        zipado = Path(self.tmp.name) / "b.zip"
        self.repo.fazer_backup(zipado)
        (self.pasta / ".pensamentos.json").unlink()
        self.repo.restaurar_backup(zipado)
        b2 = mn.Biblioteca(self.publicos, self.repo.armazem)
        self.assertEqual([q["id"] for q in b2.proprios], [p["id"]])
        self.assertEqual(b2.favoritos, {"sen-1"})

    def test_arquivo_ilegivel_nao_e_sobrescrito(self):
        (self.pasta / ".pensamentos.json").write_text("{quebrado", encoding="utf-8")
        b = mn.Biblioteca(self.publicos, self.repo.armazem)
        self.assertTrue(b.somente_leitura)
        with self.assertRaises(OSError):
            b.adicionar({"classificacao": "pessoal", "texto": "novo"})
        self.assertEqual(b.proprios, [])
        self.assertEqual((self.pasta / ".pensamentos.json").read_text(), "{quebrado")


class TestAcervo(unittest.TestCase):
    """O pensamentos.json que acompanha o app."""

    def test_acervo_valido_diverso_e_referenciado(self):
        arq = mn.ARQ_BIBLIOTECA
        bruto = json.loads(Path(arq).read_text(encoding="utf-8"))["pensamentos"]
        publicos, erro = mn.carregar_biblioteca_publica(arq)
        self.assertIsNone(erro)
        self.assertEqual(len(publicos), len(bruto), "algum item do acervo é inválido")
        self.assertEqual(len({p["id"] for p in publicos}), len(publicos))
        chaves = [mn.chave_pensamento(p["texto"], p["autor"]) for p in publicos]
        self.assertEqual(len(set(chaves)), len(chaves), "há citações repetidas")
        for p in publicos:
            if p["classificacao"] == "verificada":
                for campo in ("obra", "referencia", "fonte", "tradicao", "traducao"):
                    self.assertTrue(p[campo], f"{p['id']}: falta {campo}")
                self.assertTrue(p["temas"], p["id"])
        self.assertGreaterEqual(len({p["tradicao"] for p in publicos}), 8)
        self.assertGreaterEqual(len({p["autor"] for p in publicos}), 20)


class InterfaceBase:
    """Utilidades dos testes de interface (um só Gtk.Application por processo)."""

    app = None

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gio
        if InterfaceBase.app is None:
            InterfaceBase.app = mn.App()
            InterfaceBase.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
            InterfaceBase.app.register(None)
            InterfaceBase.app.hold()

    def girar(self, ms):
        from gi.repository import GLib
        ctx = GLib.MainContext.default()
        fim = time.monotonic() + ms / 1000
        while time.monotonic() < fim:
            ctx.iteration(False)
            time.sleep(0.005)

    def esperar(self, condicao, segundos=30):
        fim = time.monotonic() + segundos
        while not condicao():
            self.assertLess(time.monotonic(), fim, "tempo esgotado esperando a interface")
            self.girar(20)

    def digitar(self, buf, texto):
        buf.insert(buf.get_end_iter(), texto)

    def linhas(self, w):
        saida, i = [], 0
        while (row := w.lista.get_row_at_index(i)) is not None:
            saida.append(row)
            i += 1
        return saida

    def fechar_janela(self, w):
        w.fechar_mesmo_assim = True
        w.destroy()
        self.girar(100)


SEM_GUI = bool(os.environ.get("MYNOTES_SEM_GUI") or not (os.environ.get("WAYLAND_DISPLAY")
                                                        or os.environ.get("DISPLAY")))


@unittest.skipIf(SEM_GUI or SEM_NACL, "sem sessão gráfica ou sem PyNaCl")
class TestInterfaceCofre(InterfaceBase, BaseCofre):
    def setUp(self):
        super().setUp()
        self.win = mn.Janela(self.app, self.repo)
        self.win.present()
        self.girar(300)

    def tearDown(self):
        self.fechar_janela(self.win)
        super().tearDown()

    def desbloquear(self, w, senha):
        w.cad_entrada.set_text(senha)
        w.desbloquear()
        self.esperar(lambda: not w.ocupado)
        self.girar(50)

    def test_bloquear_limpa_a_tela_e_desbloquear_volta(self):
        w = self.win
        w.modos.set_active_name("diario")
        self.girar(50)
        self.digitar(w.editor.get_buffer(), "Segredo do diário")
        self.girar(1000)
        self.assertEqual(w.estado_atual, "salvo")
        w.busca.set_text("segredo")
        self.assertTrue(w.bloquear())
        self.assertEqual(w.raiz.get_visible_child_name(), "cadeado")
        self.assertIsNone(w.repo)
        self.assertIsNone(self.repo.armazem._chave)
        self.assertEqual(w.editor.conteudo(), "")
        self.assertEqual(w.busca.get_text(), "")
        self.assertEqual(self.linhas(w), [])
        self.assertEqual(w.registros, {t: [] for t in mn.TIPOS})
        # atalhos não agem com o cofre bloqueado
        w.nova()
        # senha errada: mensagem genérica, continua bloqueado
        self.desbloquear(w, "senha errada qualquer")
        self.assertIsNone(w.repo)
        self.assertTrue(w.cad_erro.get_visible())
        self.assertEqual(w.cad_erro.get_label(), "Senha incorreta.")
        self.assertEqual(w.cad_entrada.get_text(), "")
        # senha certa
        self.desbloquear(w, SENHA)
        self.assertEqual(w.raiz.get_visible_child_name(), "app")
        self.assertIn("Segredo do diário", w.editor.conteudo())
        # "fechar e abrir o app": outra janela, outro desbloqueio
        self.fechar_janela(w)
        self.win = w2 = mn.Janela(self.app, None, pasta=self.pasta)
        w2.present()
        self.girar(100)
        self.assertEqual(w2.raiz.get_visible_child_name(), "cadeado")
        self.desbloquear(w2, SENHA)
        w2.modos.set_active_name("diario")
        self.girar(50)
        self.assertIn("Segredo do diário", w2.editor.conteudo())

    def test_bloqueio_nao_descarta_texto_nao_salvo(self):
        w = self.win
        os.chmod(self.pasta / "cofre", 0o500)
        try:
            w.nova()
            self.digitar(w.editor.get_buffer(), "texto que não pôde ser salvo")
            self.girar(1000)
            self.assertEqual(w.estado_atual, "falha")
            self.assertFalse(w.bloquear())
            self.assertEqual(w.raiz.get_visible_child_name(), "app")
            self.assertIn("não pôde ser salvo", w.editor.conteudo())
            w.ajustes["bloqueio_min"] = 1
            w.ultima_atividade -= 120
            w.verificar_inatividade()
            self.assertIsNotNone(w.repo)                 # automático também espera
        finally:
            os.chmod(self.pasta / "cofre", 0o700)
        w.salvar_agora()
        self.assertEqual(w.estado_atual, "salvo")
        self.assertTrue(w.bloquear())

    def test_bloqueio_automatico_e_formulario_preenchido(self):
        w = self.win
        w.ajustes["bloqueio_min"] = 1
        w.modos.set_active_name("diario")
        d = w.fechamento_do_dia()
        d.campos[0].set_text("ainda escrevendo")
        w.ultima_atividade -= 120
        w.verificar_inatividade()
        self.assertIsNotNone(w.repo)                     # adiado: o formulário tem texto
        d.campos[0].set_text("")
        w.ultima_atividade -= 120
        w.verificar_inatividade()
        self.assertIsNone(w.repo)
        self.assertEqual(w.dialogos, [])                 # o diálogo vazio foi fechado

    def test_exportar_com_cofre_pede_confirmacao(self):
        d = self.win.exportar()
        self.assertIsNotNone(d)
        self.assertIn("sem proteção", d.get_heading())
        self.assertIn("NÃO ficam protegidos", d.get_body())
        d.close()

    def test_uso_normal_nao_deixa_texto_puro_no_disco(self):
        w = self.win
        w.nova()
        w.ent_titulo.set_text("Título confidencial")
        self.digitar(w.editor.get_buffer(), "nota confidencial")
        w.modos.set_active_name("exame")
        self.digitar(w.secoes["gratidao"].editor.get_buffer(), "gratidão confidencial")
        w.aplicar_fechamento(date.today(), "dia confidencial", "aprendi algo", "propósito X")
        self.girar(1000)
        w.apagar(w.atual)
        w.modos.set_active_name("pensamento")
        d = w.formulario_pensamento()
        d.tv_texto.get_buffer().set_text("reflexão confidencial")
        d.salvar()
        self.girar(100)
        self.assertTrue(w.bloquear())
        tudo = todos_os_bytes(self.pasta)
        for palavra in ("confidencial", "propósito", "aprendi", ".md", "diario", "exames"):
            self.assertNotIn(palavra.encode(), tudo, palavra)
        self.assertEqual(list(self.pasta.rglob("*.tmp")), [])
        self.assertFalse(w.bt_externo.get_visible())

    def test_lixeira_pela_interface(self):
        w = self.win
        w.nova()
        w.ent_titulo.set_text("Para apagar")
        self.digitar(w.editor.get_buffer(), "x")
        self.girar(1000)
        w.apagar(w.atual)
        self.assertEqual(w.registros["nota"], [])
        d = w.dialogo_lixeira()
        self.assertEqual(len(self.repo.lixeira()), 1)
        d.restaurar(self.repo.lixeira()[0])
        self.assertEqual([r.titulo for r in w.registros["nota"]], ["Para apagar"])
        self.assertEqual(self.repo.lixeira(), [])
        d.close()

    def test_trocar_senha_e_chave_de_recuperacao_pela_interface(self):
        w = self.win
        self.assertTrue(w.bt_bloquear.get_visible())
        d = w.dialogo_trocar_senha()
        d.atual.set_text("senha atual errada")
        d.campos.e1.set_text("nova senha bem comprida")
        d.campos.e2.set_text("nova senha bem comprida")
        d.trocar()
        self.esperar(lambda: not w.ocupado)
        self.assertIn(d, w.dialogos)                     # recusou: senha atual errada
        d.atual.set_text(SENHA)
        d.trocar()
        self.esperar(lambda: not w.ocupado)
        self.girar(50)
        self.assertNotIn(d, w.dialogos)
        self.assertIsNotNone(mn.abrir_cofre(self.pasta, "nova senha bem comprida"))
        # chave de recuperação
        r = w.dialogo_recuperacao()
        r.senha.set_text("nova senha bem comprida")
        r.operar(False)
        self.esperar(lambda: not w.ocupado)
        codigo = r.codigo
        self.assertTrue(w.bloquear())
        w.alternar_recuperacao()
        self.desbloquear(w, codigo.lower())
        self.assertIsNotNone(w.repo)
        self.assertTrue(any(isinstance(x, mn.Adw.Dialog) and x.get_title() == "Trocar a senha"
                            for x in w.dialogos))


@unittest.skipIf(SEM_GUI, "sem sessão gráfica")
class TestInterfaceBiblioteca(InterfaceBase, Base):
    def setUp(self):
        super().setUp()
        self.win = mn.Janela(self.app, self.repo)
        self.win.present()
        self.girar(300)

    def tearDown(self):
        self.fechar_janela(self.win)
        super().tearDown()

    def test_area_de_pensamentos(self):
        w = self.win
        b = w.biblioteca
        w.modos.set_active_name("pensamento")
        self.girar(50)
        self.assertEqual(w.pilha.get_visible_child_name(), "pensamento")
        self.assertEqual(w.pens_atual, b.do_dia(date.today())[0])      # abre no pensamento do dia
        self.assertIn("PENSAMENTO DO DIA", w.pp_rotulo.get_label())
        self.assertEqual(len(self.linhas(w)), len(b.todos()))
        w.busca.set_text("epicteto")
        self.girar(400)            # o campo de busca espera um instante antes de avisar
        linhas = self.linhas(w)
        self.assertTrue(linhas)
        self.assertTrue(all(any("epicteto" in mn.dobrar(str(v)) for v in r.pensamento.values())
                            for r in linhas))
        self.assertLess(len(linhas), len(b.todos()))
        w.lista.select_row(linhas[-1])
        self.assertIs(w.pens_atual, linhas[-1].pensamento)
        self.assertIn(linhas[-1].pensamento["texto"], w.pp_texto.get_label())
        w.usar_como_do_dia()
        self.assertEqual(b.do_dia(date.today()), (linhas[-1].pensamento, True))
        w.pp_bt_fav.set_active(True)
        self.assertIn(linhas[-1].pensamento["id"], b.favoritos)
        # no exame aparece o escolhido
        w.modos.set_active_name("exame")
        self.girar(50)
        self.assertTrue(w.card_pensamento.get_visible())
        self.assertIn(linhas[-1].pensamento["texto"], w.lbl_citacao.get_label())
        w.trocar_pensamento_do_exame()
        self.assertNotEqual(w.pens_exame["id"], linhas[-1].pensamento["id"])
        self.assertTrue(b.do_dia(date.today())[1])

    def test_cadastro_pela_interface(self):
        w = self.win
        w.modos.set_active_name("pensamento")
        d = w.formulario_pensamento()
        d.tipo.set_active_name("verificada")
        d.tv_texto.get_buffer().set_text("Uma citação sem fonte")
        d.entradas["autor"].set_text("Alguém")
        d.salvar()
        self.assertTrue(d.erro.get_visible())             # falta a fonte: não salva
        self.assertIn(d, w.dialogos)
        d.tipo.set_active_name("pessoal")
        d.salvar()
        self.girar(50)
        self.assertNotIn(d, w.dialogos)
        p = w.pens_atual
        self.assertEqual((p["texto"], p["classificacao"], p["autor"]), ("Uma citação sem fonte", "pessoal", ""))
        self.assertTrue(w.pp_bt_excluir.get_visible())
        self.assertTrue((self.pasta / ".pensamentos.json").exists())
        w.excluir_pensamento(p)
        self.assertIsNone(w.biblioteca.por_id(p["id"]))
        # bloquear/desbloquear não se aplica sem cofre; reabrir a janela mantém os dados
        p2 = w.biblioteca.adicionar({"classificacao": "pessoal", "texto": "Fica salvo"})
        self.fechar_janela(w)
        self.win = mn.Janela(self.app, Repositorio(self.pasta))
        self.assertEqual(self.win.biblioteca.por_id(p2["id"])["texto"], "Fica salvo")
        self.assertFalse(self.win.bt_bloquear.get_visible())

    @unittest.skipIf(SEM_NACL, MOTIVO_NACL)
    def test_assistente_cria_o_cofre(self):
        w = self.win
        nota = self.nova()
        nota.titulo, nota.texto = "Antes do cofre", "texto"
        nota.salvar()
        w.sincronizar_disco()
        d = w.assistente_cofre()
        self.assertIsNotNone(d)
        d.campos.e1.set_text("123")
        self.assertFalse(d.entendi.get_active())
        d.criar()                                   # senha fraca: nada acontece
        self.assertFalse(mn.cofre_existe(self.pasta))
        d.campos.e1.set_text(SENHA)
        d.campos.e2.set_text(SENHA)
        d.entendi.set_active(True)
        d.criar()
        self.esperar(lambda: d.pilha.get_visible_child_name() == "fim" and not w.ocupado)
        self.assertIn("Cofre criado e conferido", d.resultado.get_label())
        self.assertTrue(mn.cofre_existe(self.pasta))
        self.assertTrue(nota.caminho.exists())                 # o original continua
        d.abrir()
        self.assertEqual(w.raiz.get_visible_child_name(), "cadeado")
        w.cad_entrada.set_text(SENHA)
        w.desbloquear()
        self.esperar(lambda: w.repo is not None)
        self.assertTrue(w.repo.cifrado)
        self.assertEqual([r.titulo for r in w.registros["nota"]], ["Antes do cofre"])


@unittest.skipIf(os.environ.get("MYNOTES_SEM_GUI") or not (os.environ.get("WAYLAND_DISPLAY")
                                                          or os.environ.get("DISPLAY")),
                 "sem sessão gráfica")
class TestInterface(InterfaceBase, Base):
    """Abre a janela de verdade e exercita os fluxos principais."""

    def setUp(self):
        super().setUp()
        hoje = date.today()
        n = self.nova()
        n.titulo, n.texto = "Primeira nota", "texto"
        n.salvar()
        self.caminho_do_dia("exame", hoje - timedelta(days=2)).write_text(EXAME_ANTIGO)
        self.win = mn.Janela(self.app, self.repo)
        self.win.present()
        self.girar(300)

    def tearDown(self):
        self.fechar_janela(self.win)
        super().tearDown()

    def test_fluxo_diario(self):
        w = self.win
        w.modos.set_active_name("diario")
        self.girar(50)
        hoje = date.today()
        self.assertEqual(w.atual.tipo, "diario")
        self.assertFalse(w.atual.existe())                # dia sem registro não vira arquivo
        w.dia_relativo(-1)
        w.dia_relativo(1)
        self.assertFalse(self.caminho_do_dia("diario", hoje - timedelta(days=1)).exists())
        self.digitar(w.editor.get_buffer(), "Hoje foi um dia calmo.")
        self.assertEqual(w.estado_atual, "salvando")
        self.girar(1000)
        caminho = self.caminho_do_dia("diario", hoje)
        self.assertIn("Hoje foi um dia calmo.", caminho.read_text())
        self.assertEqual(w.estado_atual, "salvo")
        self.assertEqual(len(w.registros["diario"]), 1)
        self.assertTrue(w.calendario.get_day_is_marked(hoje.day))
        w.bt_humor["bom"].set_active(True)
        w.inserir_secao_diario("Aprendizados")
        self.digitar(w.editor.get_buffer(), "Paciência.")
        self.girar(1000)
        texto = caminho.read_text()
        self.assertIn("humor: bom", texto)
        self.assertIn("## Aprendizados\nPaciência.", texto)

    def test_fluxo_exame_e_ligacao_com_diario(self):
        w = self.win
        w.modos.set_active_name("exame")
        self.girar(50)
        self.digitar(w.secoes["gratidao"].editor.get_buffer(), "Pela família")
        w.bt_virtudes["virtudes"]["coragem"].set_active(True)
        w.bt_virtudes["desenvolver"]["temperança"].set_active(True)
        self.girar(1000)
        texto = self.caminho_do_dia("exame", date.today()).read_text()
        self.assertIn("## Gratidão\n\nPela família", texto)
        self.assertIn("virtudes: coragem", texto)
        self.assertIn("desenvolver: temperança", texto)
        # exame antigo mostra as perguntas de Sêneca como seções preservadas
        w.abrir_dia(date.today() - timedelta(days=2))
        self.assertEqual([s.titulo_md for s in w.secoes_extras],
                         ["Que mal curei hoje?", "Em que estou melhor?"])
        self.assertIn("Aceitar", w.secoes["nao_dependia"].editor.conteudo())
        # exame passado: perguntas em branco escondidas até pedir
        self.assertFalse(w.secoes["gratidao"].get_visible())
        self.assertTrue(w.secoes["nao_dependia"].get_visible())
        self.assertTrue(w.secoes["virtudes"].get_visible())       # tem virtudes marcadas
        w.bt_em_branco.emit("clicked")
        self.assertTrue(w.secoes["gratidao"].get_visible())
        # do exame para o diário do mesmo dia, sem copiar texto
        w.abrir_outro_do_dia()
        self.assertEqual((w.modo, w.atual.data()), ("diario", date.today() - timedelta(days=2)))
        self.assertEqual(w.atual.texto, "")

    def test_fechamento_do_dia(self):
        w = self.win
        w.modos.set_active_name("diario")
        self.girar(50)
        hoje = date.today()
        tocados = w.aplicar_fechamento(hoje, "Dia longo.", "Pedir ajuda cedo.", "- [ ] Dormir às 23h")
        self.assertEqual(len(tocados), 2)
        diario = self.caminho_do_dia("diario", hoje).read_text()
        exame = self.caminho_do_dia("exame", hoje).read_text()
        self.assertIn("Dia longo.\n\n## Aprendizados\n\nPedir ajuda cedo.", diario)
        self.assertNotIn("Dormir", diario)
        self.assertIn("## Propósitos para amanhã\n\n- [ ] Dormir às 23h", exame)
        self.assertNotIn("Dia longo", exame)
        self.assertIn("Dia longo.", w.editor.conteudo())     # o diário aberto foi atualizado
        self.assertEqual(w.aplicar_fechamento(hoje, " ", "", ""), [])

    def test_busca_global_abre_resultado_de_outra_area(self):
        w = self.win
        w.busca.set_text("curei")
        w.on_busca()
        linhas = []
        i = 0
        while (row := w.lista.get_row_at_index(i)) is not None:
            linhas.append(row.registro)
            i += 1
        self.assertEqual([r.tipo for r in linhas], ["exame"])
        w.lista.select_row(w.lista.get_row_at_index(0))
        self.assertEqual((w.modo, w.atual), ("exame", linhas[0]))
        self.assertEqual(w.modos.get_active_name(), "exame")
        w.dd_tipo.set_selected(1)          # só notas
        self.assertIsNone(w.lista.get_row_at_index(0))

    def test_apagar_e_desfazer(self):
        w = self.win
        nota = w.atual
        self.assertEqual(nota.titulo, "Primeira nota")
        caminho = nota.caminho
        w.apagar(nota)
        self.assertFalse(caminho.exists())
        self.assertEqual(len(list((self.pasta / ".lixeira" / "notas").iterdir())), 1)
        destino = ".lixeira/notas/" + next((self.pasta / ".lixeira" / "notas").iterdir()).name
        w.desfazer_apagar(nota, destino)
        self.assertTrue(caminho.exists())
        self.assertEqual(w.atual.titulo, "Primeira nota")

    def test_falha_ao_salvar_mantem_texto_e_avisa(self):
        w = self.win
        nota = w.atual
        os.chmod(self.pasta, 0o500)
        try:
            self.digitar(w.editor.get_buffer(), " mais")
            self.girar(1000)
            self.assertEqual(w.estado_atual, "falha")
            self.assertIn(nota, w.pendentes)
            self.assertEqual(nota.texto, "texto mais")
            self.assertNotIn("mais", nota.caminho.read_text())
        finally:
            os.chmod(self.pasta, 0o700)
        w.salvar_agora()
        self.assertEqual(w.estado_atual, "salvo")
        self.assertIn("texto mais", nota.caminho.read_text())

    def test_conflito_nao_sobrescreve_edicao_externa(self):
        w = self.win
        nota = w.atual
        nota.caminho.write_text(nota.caminho.read_text().replace("texto", "do editor externo"))
        tocar_mtime(nota.caminho)
        self.digitar(w.editor.get_buffer(), " do app")
        self.girar(1000)
        self.assertIn("do editor externo", nota.caminho.read_text())
        self.assertEqual(w.estado_atual, "conflito")
        self.assertIn(nota, w.conflitos_avisados)

    def test_nova_nota_vazia_nao_cria_arquivo(self):
        w = self.win
        antes = set(self.pasta.glob("*.md"))
        w.nova()
        w.nova()
        w.modos.set_active_name("diario")
        self.girar(50)
        self.assertEqual(set(self.pasta.glob("*.md")), antes)

    def test_modo_foco(self):
        w = self.win
        w.alternar_foco()
        self.assertFalse(w.split.get_show_sidebar())
        self.assertFalse(w.props.get_visible())
        w.alternar_foco()
        # com a janela estreita (tiling), a lista fica recolhida até ser pedida
        self.assertEqual(w.split.get_show_sidebar(), not w.split.get_collapsed())


if __name__ == "__main__":
    unittest.main()
