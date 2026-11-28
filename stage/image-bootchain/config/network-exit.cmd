# Network Exit A/B selector. Loaded from either root filesystem by distro_bootcmd.
# Boot record banks are relative to partition 3; SPL/U-Boot raw sectors are untouched.
if test "${devtype}" != "mmc"; then
    echo "Network Exit requires an SD/MMC boot device"
    exit
fi
setenv ne_buf ${kernel_addr_r}
setenv ne_seen 0
if part start mmc ${devnum} 3 ne_start; then
    for ne_i in 0 1; do
        setexpr ne_offset ${ne_i} * 0x80
        setexpr ne_sector ${ne_start} + ${ne_offset}
        setenv ne_magic
        setenv ne_seq
        setenv ne_confirmed
        setenv ne_pending
        setenv ne_left
        setenv ne_A
        setenv ne_B
        if mmc read ${ne_buf} ${ne_sector} 0x20; then
            if env import -c ${ne_buf} 0x4000 ne_magic ne_seq ne_confirmed ne_pending ne_left ne_A ne_B; then
                setenv ne_valid 1
                if test "${ne_magic}" != "NEAB1"; then setenv ne_valid 0; fi
                if test "${ne_confirmed}" != "A" && test "${ne_confirmed}" != "B"; then setenv ne_valid 0; fi
                if test "${ne_pending}" != "none" && test "${ne_pending}" != "A" && test "${ne_pending}" != "B"; then setenv ne_valid 0; fi
                if test "${ne_left}" != "0" && test "${ne_left}" != "1" && test "${ne_left}" != "2" && test "${ne_left}" != "3"; then setenv ne_valid 0; fi
                if test "${ne_A}" != "0" && test "${ne_A}" != "1"; then setenv ne_valid 0; fi
                if test "${ne_B}" != "0" && test "${ne_B}" != "1"; then setenv ne_valid 0; fi
                if test -z "${ne_seq}"; then setenv ne_valid 0; fi
                if test ${ne_valid} = 1; then
                    if test ${ne_seen} = 0 || test ${ne_seq} -gt ${ne_saved_seq}; then
                        setenv ne_seen 1
                        setenv ne_bank ${ne_i}
                        setenv ne_saved_seq ${ne_seq}
                        setenv ne_saved_confirmed ${ne_confirmed}
                        setenv ne_saved_pending ${ne_pending}
                        setenv ne_saved_left ${ne_left}
                        setenv ne_saved_A ${ne_A}
                        setenv ne_saved_B ${ne_B}
                    fi
                fi
            fi
        fi
    done
fi
if test ${ne_seen} = 0; then
    echo "Both A/B boot records are invalid. Reflash the factory image."
    sleep 10
    reset
fi
setenv ne_magic NEAB1
setenv ne_seq ${ne_saved_seq}
setenv ne_confirmed ${ne_saved_confirmed}
setenv ne_pending ${ne_saved_pending}
setenv ne_left ${ne_saved_left}
setenv ne_A ${ne_saved_A}
setenv ne_B ${ne_saved_B}
setenv ne_slot ${ne_confirmed}
setenv ne_trialarg
setenv ne_fallbackarg
if test "${ne_pending}" != "none" && test ${ne_left} -gt 0; then
    setenv ne_trial_ok 0
    if test "${ne_pending}" = "A" && test "${ne_A}" = "1"; then setenv ne_trial_ok 1; fi
    if test "${ne_pending}" = "B" && test "${ne_B}" = "1"; then setenv ne_trial_ok 1; fi
    if test ${ne_trial_ok} = 1; then
        setexpr ne_left ${ne_left} - 1
        setexpr ne_seq ${ne_seq} + 1
        setenv ne_seq 0x${ne_seq}
        setexpr ne_nextbank 1 - ${ne_bank}
        setexpr ne_offset ${ne_nextbank} * 0x80
        setexpr ne_sector ${ne_start} + ${ne_offset}
        setenv ne_expected_seq ${ne_seq}
        # Decrement, persist and read back BEFORE any trial kernel is loaded.
        if env export -c -s 0x4000 ${ne_buf} ne_magic ne_seq ne_confirmed ne_pending ne_left ne_A ne_B; then
            if mmc write ${ne_buf} ${ne_sector} 0x20; then
                if mmc read ${ne_buf} ${ne_sector} 0x20; then
                    if env import -c ${ne_buf} 0x4000 ne_magic ne_seq ne_confirmed ne_pending ne_left ne_A ne_B; then
                        if test "${ne_seq}" = "${ne_expected_seq}"; then
                            setenv ne_slot ${ne_pending}
                            setenv ne_trialarg network_exit.trial=1
                        fi
                    fi
                fi
            fi
        fi
    fi
fi
# Keep both kernel and initramfs/DTB in their corresponding root slot.
if part uuid mmc ${devnum}:4 ne_stateuuid; then
    for ne_attempt in primary fallback; do
        if test "${ne_slot}" = "A"; then setenv ne_part 1; else setenv ne_part 2; fi
        if part uuid mmc ${devnum}:${ne_part} ne_rootuuid; then
            setenv bootargs "root=PARTUUID=${ne_rootuuid} rootwait ro panic=10 console=tty1 console=ttyS0,115200 rauc.slot=${ne_slot} ${ne_trialarg} ${ne_fallbackarg} network_exit.state=${ne_stateuuid} overlayroot=tmpfs:recurse=0 debug=vc"
            if load mmc ${devnum}:${ne_part} ${kernel_addr_r} /boot/Image; then
                if load mmc ${devnum}:${ne_part} ${fdt_addr_r} /boot/dtb/allwinner/sun50i-h618-orangepi-zero3.dtb; then
                    if load mmc ${devnum}:${ne_part} ${ramdisk_addr_r} /boot/uInitrd; then
                        booti ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}
                    fi
                fi
            fi
        fi
        if test -n "${ne_trialarg}"; then setenv ne_fallbackarg network_exit.fallback=1; fi
        setenv ne_slot ${ne_saved_confirmed}
        setenv ne_trialarg
    done
fi
echo "No bootable confirmed slot. Reflash the factory image."
sleep 10
reset
