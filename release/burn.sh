#!/bin/bash
set -euo pipefail
package_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo 'Python 3.11 or newer is required.' >&2; exit 1; }
exec python3 "$package_dir/lib/burn.py" "$@"
