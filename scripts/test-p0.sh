#!/usr/bin/env bash
#
# P0 smoke test — shared CLI identity + persisted auth gate.
# Runs all NON-interactive checks against a throwaway local SQLite DB.
# (The interactive login/signup menu needs a real TTY — try `sage login`
#  yourself in the terminal after this passes.)
#
# Usage:  bash scripts/test-p0.sh
#
set -uo pipefail
cd "$(dirname "$0")/.."

# --- isolate: local SQLite + scratch data dir, never touch real ./data ---
export DATABASE_URL=""
export DATA_DIR="$(mktemp -d)/sage-p0"
PY=".venv/bin/python"
SAGE="$PY -m app.main"

pass=0; fail=0
ok()   { echo "  ✓ $1"; pass=$((pass+1)); }
bad()  { echo "  ✗ $1"; fail=$((fail+1)); }
sage() { $SAGE "$@" 2>&1; }

echo "P0 smoke test  (DATA_DIR=$DATA_DIR, DATABASE_URL=<sqlite>)"
echo

# 1) automated suite -----------------------------------------------------------
echo "[1] pytest tests/cli tests/config"
if $PY -m pytest tests/cli tests/config -q >/dev/null 2>&1; then ok "suite green"; else bad "suite failed"; fi

# 2) LOCAL mode (empty passphrase → auto user, no prompt) ----------------------
echo "[2] local mode (SAGE_PASSPHRASE=\"\")"
export SAGE_PASSPHRASE=""
rm -rf "$DATA_DIR"
sage whoami | grep -q "Not logged in" && ok "whoami fresh → not logged in" || bad "whoami fresh"
sage login  | grep -q "Auth disabled" && ok "login → auto local user"      || bad "login local"
sage whoami | grep -qE "user_id" && ok "whoami → persisted user"            || bad "whoami persisted"
[ "$(stat -f '%A' "$DATA_DIR/session.json" 2>/dev/null)" = "600" ] && ok "session file mode 600" || bad "session mode"
grep -q "password" "$DATA_DIR/session.json" && bad "password leaked to disk" || ok "no password on disk"
email_out="$(sage email-personal)"   # exits 1 by design; capture avoids pipefail confusion
echo "$email_out" | grep -q "isn't connected" && ok "email → friendly (no TypeError)" || bad "email path"
sage logout | grep -q "Logged out" && ok "logout clears session" || bad "logout"
sage whoami | grep -q "Not logged in" && ok "whoami after logout → not logged in" || bad "whoami post-logout"

# 3) AUTH-ENABLED guards (non-interactive) -------------------------------------
echo "[3] auth-enabled mode (SAGE_PASSPHRASE set)"
export SAGE_PASSPHRASE="secret123"
rm -rf "$DATA_DIR"
sage whoami | grep -q "Not logged in" && ok "whoami never prompts" || bad "whoami prompt"
out="$(echo '' | $SAGE email-personal 2>&1)"; code=$?
{ echo "$out" | grep -q "Not logged in" && [ $code -ne 0 ]; } \
  && ok "non-TTY + no session → clean exit 1" || bad "non-TTY guard"

# 4) stale session rejected ----------------------------------------------------
echo "[4] stale-session safety"
export SAGE_PASSPHRASE=""
rm -rf "$DATA_DIR"; mkdir -p "$DATA_DIR"
echo '{"user_id":"does-not-exist","username":"ghost"}' > "$DATA_DIR/session.json"
sage login >/dev/null
sage whoami | grep -qv "ghost" && sage whoami | grep -qE "user_id" \
  && ok "stale session replaced (not 'ghost')" || bad "stale session"

# --- report -------------------------------------------------------------------
rm -rf "$DATA_DIR"
echo
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
