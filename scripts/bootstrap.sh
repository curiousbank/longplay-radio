#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${PYTHON:-python}"
profile="${1:-base}"

case "$profile" in
  base|dev) ;;
  *) echo "usage: ./scripts/bootstrap.sh [base|dev]" >&2; exit 2 ;;
esac

if [[ "$profile" == "dev" ]]; then
  "$python" -m pip install -e "$root[dev]"
else
  "$python" -m pip install -e "$root"
fi

"$python" - <<'PY'
from importlib.metadata import version
import genblaze_core

assert genblaze_core is not None
assert version("genblaze-core") == "0.3.8"
assert version("Pillow") == "12.3.0"
print("LONGPLAY RADIO environment ready")
PY

"$python" -m pip check
