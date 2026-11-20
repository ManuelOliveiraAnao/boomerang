#!/usr/bin/env bash
set -euo pipefail
SERVER="${SERVER:?set SERVER=user@host}"
rsync -az ./ "$SERVER:~/shoplite/"
ssh "$SERVER" 'cd ~/shoplite && docker compose up -d --wait'
