#!/bin/bash
# Public image build entrypoint; implementation lives in image_builder/.
set -euo pipefail
exec bash "$(dirname -- "${BASH_SOURCE[0]}")/image_builder/build.sh" "$@"
