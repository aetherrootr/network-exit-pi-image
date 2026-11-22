# Sourced in initramfs after Debian overlayroot, before restoring device STATE.
# The optional proc-file arguments support fixture tests; boot uses real /proc.
limit_root_overlay() {
    local merged_root=${1:?Missing merged root} meminfo=${2:-/proc/meminfo}
    local mounts=${3:-/proc/mounts} upper_mount options
    upper_mount="$merged_root/media/root-rw"
    # Require the standard Debian RAM-backed layout; never remount STATE or a
    # disk upper by accident when an upstream overlayroot implementation changes.
    awk -v target="$merged_root" '
        $2 == target && ($3 == "overlay" || $3 == "overlayfs") { found=1 }
        END { exit !found }
    ' "$mounts" || return 1
    awk -v target="$upper_mount" '
        $1 == "tmpfs-root" && $2 == target && $3 == "tmpfs" { found=1 }
        END { exit !found }
    ' "$mounts" || return 1
    options=$(awk '
        $1 == "MemTotal:" {
            count++
            if ($2 !~ /^[0-9]+$/ || $3 != "kB") invalid=1
            total=$2
        }
        END {
            if (count != 1 || invalid || total <= 0) exit 1
            kib=int(total / 8)
            if (kib > 131072) kib=131072
            kib=int(kib / 4) * 4
            if (kib < 4096) exit 1
            printf "size=%dk,nr_inodes=%d", kib, kib / 4
        }
    ' "$meminfo") || return 1
    mount -t tmpfs -o "remount,$options" tmpfs-root "$upper_mount" || return 1
    printf 'Root overlay limits: %s (RAM-backed; cleared on reboot).\n' "$options"
}
