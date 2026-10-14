# Segurança do cofre

Este documento explica como o cofre do MyNotes funciona, o que ele protege e o que **não** protege.
O cofre é opcional e precisa do [PyNaCl](https://pynacl.readthedocs.io/) (bindings Python da
[libsodium](https://doc.libsodium.org/)).

> O código do cofre tem testes automatizados, mas **não passou por auditoria de segurança
> independente**. Testes passando não provam que algo é seguro. Revisões são bem-vindas: abra uma
> *issue*.

## Como funciona

### Chaves
- **Chave do cofre:** 256 bits aleatórios (`nacl.utils.random`), gerados na criação do cofre. Ela
  cifra todos os arquivos.
- **Envelope da senha:** no `cofre.json`, a chave do cofre fica cifrada com uma chave derivada da
  senha:
  - derivação por **Argon2id** (`nacl.pwhash.argon2id`), com os parâmetros "SENSITIVE" da libsodium:
    4 passagens, 1 GiB de memória e salt aleatório de 16 bytes;
  - cerca de 1–2 s por tentativa num notebook comum;
  - os parâmetros ficam gravados no `cofre.json`, para que versões futuras possam mudá-los sem
    quebrar cofres antigos.
- **Chave de recuperação** (opcional): um segundo envelope, derivado de um código aleatório de 25
  caracteres. O código aparece uma única vez e não é guardado.
- **Troca de senha:** regrava apenas o envelope. Os arquivos cifrados não mudam, e a nova senha é
  conferida antes de terminar.

### Arquivos
- **Nomes:** cada registro (nota, diário, exame, item da lixeira, tags, seus pensamentos) vira
  `cofre/<32 hex aleatórios>.bin`.
- **Formato:** `IDC1` + nonce de 24 bytes + texto cifrado com **XChaCha20-Poly1305**
  (`nacl.bindings.crypto_aead_xchacha20poly1305_ietf_*`).
- **Nonce:** aleatório a cada gravação.
- **Dados associados (AAD):** `mynotes-cofre/1/blob/<id>`, o que impede trocar o conteúdo de um
  arquivo pelo de outro.
- **Conteúdo cifrado:** um cabeçalho JSON com o caminho lógico (ex.: `diario/2026-10-13.md`) e a
  data de modificação, seguido do Markdown do registro.
- **Tamanho:** é preenchido até múltiplos de 512 bytes (`sodium_pad`), para não revelar o tamanho
  exato.
- **Gravação:** o arquivo vai para um temporário (também cifrado), recebe `fsync`, substitui o
  anterior de forma atômica e é **relido e decifrado** antes de o app mostrar "Salvo".
- **Leitura:** a autenticação é conferida antes de qualquer uso. Um arquivo alterado ou corrompido é
  recusado e avisado, e o app não o apaga.

### Sessão
- Ao desbloquear, os registros são decifrados para a memória, e a busca trabalha ali. Nada é
  gravado em texto puro para isso.
- **Bloquear** (`Ctrl+L`, o cadeado ou o bloqueio automático) limpa telas, lista, busca, histórico de
  desfazer e diálogos, e descarta a chave e o texto decifrado.
- O app **não bloqueia** se isso fizer perder texto ainda não salvo: ele recusa (ou adia, no
  automático) e explica o motivo.
- **Senha errada:** a mensagem é sempre a mesma, "Senha incorreta.". A partir do 3º erro seguido, há
  uma espera crescente antes da próxima tentativa.

## Migração (de texto puro para o cofre)

1. Uma cópia de tudo vai para `.migracao/antes-da-migracao-<data>.zip`. Ela fica em texto puro, como
   os originais.
2. Tudo é cifrado em `.cofre-novo/`, uma pasta temporária, que é aberta de novo com a senha e
   comparada byte a byte com os originais.
3. O `cofre.json` é gravado por último: só então o cofre passa a valer. Uma falha antes disso não muda
   nada, e dá para tentar de novo.
4. O cofre é aberto mais uma vez no lugar definitivo e comparado outra vez. Se essa conferência
   falhar, a migração é desfeita.
5. **Os originais não são apagados.** Depois de conferir, use ☰ → *Remover arquivos antigos em
   texto puro*. Só são removidos os arquivos idênticos ao que está no cofre, e o .zip da migração só
   é removido se você marcar a opção.

## O que fica visível com o cofre bloqueado

- Que existe um cofre do MyNotes, quantos arquivos ele tem e o tamanho aproximado de cada um (em
  blocos de 512 bytes).
- Quando cada arquivo foi gravado pela última vez (data de modificação no sistema de arquivos).
- `ajustes.json`, que guarda só o tempo do bloqueio automático.
- O acervo público de pensamentos, que não tem nada seu.

## Limites

- **A força depende da senha.** Uma senha só de números, mesmo com 12 dígitos, resiste bem menos a
  força bruta do que uma frase.
- **Cofre aberto:** o conteúdo está na memória do app e na tela. Quem usa a sua sessão, um programa
  malicioso rodando como você ou um administrador (root) pode lê-lo.
- **Memória:** o Python não permite apagar com garantia todas as cópias da senha e do texto. Ao
  bloquear, o app as descarta, mas não dá para afirmar que sumiram da RAM ou da swap.
- **Cópias antigas:** arquivos em texto puro de antes do cofre, backups `.zip` antigos, exportações,
  snapshots e cópias em outros discos não ficam protegidos.
- **Apagar não garante sumiço:** um arquivo apagado pode continuar recuperável, especialmente em SSD.
- **Exclusão e versões antigas:** quem consegue gravar na pasta pode apagar um arquivo cifrado ou
  trocá-lo por uma versão antiga do mesmo arquivo, e o app não detecta isso, porque não há um índice
  cifrado com versões. Alteração de conteúdo ele detecta.
- **Área de transferência:** o que você copia do app fica lá até ser substituído.
- **Disco inteiro:** a criptografia de disco (LUKS) protege o sistema todo com o computador
  desligado. O cofre protege só os dados do MyNotes, mas também com o computador ligado e o cofre
  bloqueado. Um não substitui o outro.
- **Senha perdida:** esquecer a senha, sem chave de recuperação, torna os dados **irrecuperáveis**.
  Não existe senha mestra nem porta dos fundos.
