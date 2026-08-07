#!/usr/bin/env bash
# Sincroniza os PDFs de ~/Documents/fin para a VPS e importa no ofin.
# Fonte única dos extratos/faturas: ~/Documents/fin (NÃO existe mais upload web).
# Idempotente: import_pdf deduplica por sha256, só os meses novos entram.
#
# Uso:  bash scripts/sync-import.sh
set -euo pipefail

SRC="${OFIN_FIN_DIR:-$HOME/Documents/fin}"
VPS="${OFIN_VPS:-opc@mandragora-vps}"
INBOX="/home/opc/ofin/fin-inbox"

echo "→ rsync $SRC/*.pdf  →  $VPS:$INBOX"
ssh "$VPS" "mkdir -p $INBOX"
rsync -a --delete "$SRC"/*.pdf "$VPS:$INBOX/"

echo "→ importando no container ofin (efêmero, herda DATABASE_URL do compose)"
ssh "$VPS" "cd /home/opc/ofin && docker compose run --rm -v $INBOX:/inbox:ro ofin \
  python -m ofin.parsers.cli commit /inbox"

echo "✓ pronto. Confira /documents e o dashboard."
