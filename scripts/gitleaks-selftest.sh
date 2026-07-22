#!/usr/bin/env bash
# Self-test for the shared .gitleaks.toml.
#
# Proves both directions of the gate, which is the only way an allowlist stays
# honest: an allowlist that is never tested against true positives eventually
# suppresses everything and the check goes permanently green while leaking.
#
#   Corpus A (must FAIL)  -- credential shapes QUANTATECH-446 actually found.
#   Corpus B (must PASS)  -- the 446 false-positive classes we allowlist.
#
# Usage:  scripts/gitleaks-selftest.sh [path-to-gitleaks-binary]
# Exits non-zero on the first assertion that does not hold.

set -euo pipefail

GITLEAKS="${1:-gitleaks}"
CONFIG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.gitleaks.toml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

command -v "$GITLEAKS" >/dev/null 2>&1 || { echo "gitleaks not found: $GITLEAKS" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "config not found: $CONFIG" >&2; exit 1; }

fail=0

# ---------------------------------------------------------------------------
# Corpus A — MUST be detected
# ---------------------------------------------------------------------------
# NOTE: every value here is synthetic. Do not "fix" one to look more realistic
# by pasting a real credential -- this file is committed and public.
mkdir -p "$WORK/a/src"
cat > "$WORK/a/src/config.ts" <<'EOF'
const awsKey = "AKIA4SLTQ2WPZRXNVJHD";
EOF
cat > "$WORK/a/src/local.env" <<'EOF'
DATABASE_URL=postgres://app:S3cretPwd123!@db.internal:5432/quanta
REDIS_URL=redis://:hunter2xyzABC@redis.internal:6379
MONGO_URL=mongodb+srv://svc:Qw8vLp2zX9@cluster0.mongodb.net/db
JWT_SECRET=8f3b9c2e7a145d6f8e0b3c9a72d4e15f8b6c0a3d9e2f7b4c
EOF

# Each entry: "<file>:<expected-rule-substring>"
expected_a=(
  "config.ts:aws-access-token"
  "local.env:vioxen-uri-embedded-credential"
  "local.env:generic-api-key"
)

report_a="$WORK/a-report.csv"
set +e
"$GITLEAKS" dir "$WORK/a" --config "$CONFIG" --redact --no-banner \
  --exit-code 2 --report-format csv --report-path "$report_a" >/dev/null 2>&1
a_exit=$?
set -e

if [ "$a_exit" -ne 2 ]; then
  echo "FAIL [A] expected exit 2 (leaks found), got $a_exit — the gate does NOT detect planted secrets"
  fail=1
else
  echo "PASS [A] planted secrets detected (exit 2)"
fi

for want in "${expected_a[@]}"; do
  f="${want%%:*}"; rule="${want##*:}"
  if awk -F',' -v f="$f" -v r="$rule" 'NR>1 && index($3,f) && index($1,r) {found=1} END{exit !found}' "$report_a"; then
    echo "PASS [A] $f -> $rule"
  else
    echo "FAIL [A] expected rule '$rule' to fire on '$f' — it did not"
    fail=1
  fi
done

# The report must never carry a raw secret, even for true positives.
if grep -qE 'S3cretPwd123|hunter2xyzABC|Qw8vLp2zX9|AKIA4SLTQ2WPZRXNVJHD' "$report_a"; then
  echo "FAIL [A] raw secret present in the report — --redact is not covering report output"
  fail=1
else
  echo "PASS [A] report is redacted"
fi

# ---------------------------------------------------------------------------
# Corpus B — MUST stay clean (the 446 false-positive classes)
# ---------------------------------------------------------------------------
mkdir -p "$WORK/b/src" "$WORK/b/__fixtures__" "$WORK/b/tests/fixtures"
printf 'const k = "AKIA4SLTQ2WPZRXNVJHD";\n'  > "$WORK/b/__fixtures__/mock.ts"
printf 'const k = "AKIA4SLTQ2WPZRXNVJHD";\n'  > "$WORK/b/tests/fixtures/seed.ts"
printf 'const K = "AKIA4SLTQ2WPZRXNVJHD";\n'  > "$WORK/b/src/security.rs"
printf 'AIMLAPI_MODEL=gpt-4o-2024-08-06-hq-xLQ92mfPz\n' > "$WORK/b/src/aimlapi.env"
printf '{"nonce":"a8Kd93mZpQx7Lw2vBnRt5YcE1"}\n'        > "$WORK/b/src/ws.json"
printf '{"integrity":"sha512-abc123DEF456ghiJKL789mnoPQR012stuVWX345yz+/=="}\n' > "$WORK/b/package-lock.json"
printf 'export const K = "AKIAIOSFODNN7EXAMPLE";\n'     > "$WORK/b/src/doc.ts"
cat > "$WORK/b/src/.env.example" <<'EOF'
API_KEY=your-api-key-here
DATABASE_URL=postgres://user:changeme@localhost:5432/dev
REDIS_URL=redis://user:<your-password>@localhost:6379
TEMPLATED=postgres://user:${DB_PASSWORD}@localhost:5432/dev
EOF
printf '{"RuleID":"aws-access-token","Secret":"REDACTED"}\n' > "$WORK/b/_meta.json"

report_b="$WORK/b-report.csv"
set +e
"$GITLEAKS" dir "$WORK/b" --config "$CONFIG" --redact --no-banner \
  --exit-code 2 --report-format csv --report-path "$report_b" >/dev/null 2>&1
b_exit=$?
set -e

if [ "$b_exit" -eq 0 ]; then
  echo "PASS [B] false-positive corpus is clean"
else
  echo "FAIL [B] allowlist regression — these should NOT have fired:"
  awk -F',' 'NR>1 && NF>6 { printf "        %s  %s:%s\n", $1, $3, $7 }' "$report_b"
  fail=1
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "gitleaks self-test FAILED"
  exit 1
fi
echo "gitleaks self-test passed"
