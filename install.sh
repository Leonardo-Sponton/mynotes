#!/bin/sh
# Instala o MyNotes para o usuário atual (sem sudo) em ~/.local, ou em $PREFIX.
#   ./install.sh               instala ou atualiza
#   ./install.sh --uninstall   remove o app (a pasta de dados ~/MyNotes NÃO é apagada)
set -eu

PREFIX="${PREFIX:-$HOME/.local}"
APP_ID="io.github.leonardo_sponton.MyNotes"
AQUI="$(cd "$(dirname "$0")" && pwd)"
BIN="$PREFIX/bin/mynotes"
ACERVO="$PREFIX/share/mynotes/pensamentos.json"
DESKTOP="$PREFIX/share/applications/$APP_ID.desktop"

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$BIN" "$ACERVO" "$DESKTOP"
    rmdir "$PREFIX/share/mynotes" 2>/dev/null || true
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
    echo "MyNotes removido. Seus dados (~/MyNotes) continuam onde estavam."
    exit 0
fi

install -Dm755 "$AQUI/mynotes" "$BIN"
install -Dm644 "$AQUI/data/pensamentos.json" "$ACERVO"
mkdir -p "$(dirname "$DESKTOP")"
sed "s|^Exec=.*|Exec=$BIN|" "$AQUI/data/mynotes.desktop" > "$DESKTOP"
chmod 644 "$DESKTOP"
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

echo "MyNotes instalado em $BIN"
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) echo "Atenção: $PREFIX/bin não está no PATH; abra pelo menu de aplicativos ou use $BIN." ;;
esac
python3 -c "import nacl" 2>/dev/null \
    || echo "Aviso: PyNaCl não encontrado. O app funciona, mas o cofre com senha fica indisponível."
