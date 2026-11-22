# Shared build-only shell helpers. No service may start during installation.
export DEBIAN_FRONTEND=noninteractive LC_ALL=C
apt() { apt-get -o Acquire::Retries=3 -o DPkg::Lock::Timeout=300 "$@"; }
fetch() { curl --fail --show-error --silent --location --retry 3 --connect-timeout 20 "$1" -o "$2"; }
install_declared_packages() {
  local packages=()
  mapfile -t packages < <(python3 -c 'import json; print("\n".join(json.load(open("stage.json")).get("packages",[])))')
  if [[ ${#packages[@]} != 0 ]]; then apt install -y --no-install-recommends "${packages[@]}"; fi
}
