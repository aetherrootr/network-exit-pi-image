# Shared host-side lifecycle. Stage functions run in this shell so resource state
# survives until cleanup; never invoke them in a conditional that masks errexit.
restore_build_files() {
  local result=0
  if [[ $resolver_saved == 1 ]]; then
    if rm -f "$rootfs/etc/resolv.conf" && cp -a "$build_dir/resolv.conf" "$rootfs/etc/resolv.conf"; then
      resolver_saved=0
    else result=1; fi
  fi
  if [[ $policy_written == 1 ]]; then
    if rm -f "$rootfs/usr/sbin/policy-rc.d" &&
        { [[ $policy_saved == 0 ]] || cp -a "$build_dir/policy-rc.d" "$rootfs/usr/sbin/policy-rc.d"; }; then
      policy_written=0
    else result=1; fi
  fi
  if [[ $qemu_added == 1 ]]; then
    if rm -f "$rootfs/usr/bin/network-exit-build-qemu-aarch64"; then qemu_added=0; else result=1; fi
  fi
  return "$result"
}

unmount_target() {
  local name result=0
  [[ -d $rootfs ]] || return 0
  # Test each mount independently: setup can fail halfway through.
  for name in run proc sys dev; do
    if mountpoint -q "$rootfs/$name"; then
      if ! umount -R "$rootfs/$name"; then result=1; fi
    fi
  done
  if mountpoint -q "$rootfs"; then
    if ! umount "$rootfs"; then result=1; fi
  fi
  return "$result"
}

detach_loop() {
  [[ -n $loop ]] || return 0
  if mountpoint -q "$rootfs"; then
    echo "Root is still mounted; retaining loop device: $loop" >&2
    return 1
  fi
  if losetup -d "$loop"; then loop=''; else return 1; fi
}

record_stage() {
  printf '%s\t%s\t%s\t%s\t%s\n' "$current_stage" "$1" "$((SECONDS - stage_started))" "$2" "logs/$current_stage.log" >> "$build_dir/stages.tsv"
}

finish_stage_log() {
  local result=0
  if [[ -n $stage_log_pid ]]; then
    # Close all writers before waiting for tee, including on an EXIT trap.
    exec 1>&"$build_stdout" 2>&"$build_stderr"
    wait "$stage_log_pid" || result=$?
    stage_log_pid=''
  fi
  return "$result"
}

build_cleanup() {
  local result=$? cleanup_result=0
  trap - EXIT INT TERM
  set +e
  finish_stage_log || cleanup_result=1
  if [[ -n $current_stage ]]; then
    [[ $result != 0 ]] || result=1
    record_stage failed "$result"
    echo "Failed stage: $current_stage (exit $result)." >&2
    [[ ${GITHUB_ACTIONS:-} != true ]] || echo '::endgroup::'
  fi
  restore_build_files || cleanup_result=1
  unmount_target || cleanup_result=1
  detach_loop || cleanup_result=1
  if [[ $cleanup_result != 0 ]]; then
    echo "Resource cleanup failed. Inspect mounts and loop devices for: $build_dir" >&2
    [[ $result != 0 ]] || result=1
  fi
  if [[ $result != 0 ]]; then
    echo "Build incomplete. Working directory and logs retained at: $build_dir" >&2
  else
    echo "Build complete: $final"
    echo "Stage logs and journal: $build_dir"
    echo 'Hardware boot and end-to-end exit tests are still required.'
  fi
  exit "$result"
}

initialize_build() {
  mkdir -p "$project/work"
  exec {build_lock}> "$project/work/build.lock"
  flock -n "$build_lock" || { echo 'Another build is already running in this project.' >&2; exit 1; }
  build_dir=$(mktemp -d "$project/work/build.XXXXXXXX")
  mkdir -p "$build_dir/logs"
  rootfs=$build_dir/root
  image=$build_dir/custom.img
  factory=$build_dir/factory
  slot_image=$build_dir/rootfs.img
  final=$project/output/factory
  loop=''
  part=''
  resolver_saved=0 policy_saved=0 policy_written=0 qemu_added=0
  current_stage='' stage_log_pid='' stage_started=0
  exec {build_stdout}>&1 {build_stderr}>&2
  printf 'stage\tevent\telapsed_seconds\texit_code\tlog\n' > "$build_dir/stages.tsv"
  trap build_cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  echo "Build journal: $build_dir/stages.tsv"
}

run_build_stage() {
  local id=$1 context=$2 title=$3 description=$4 position=$5 total=$6
  local stage_file=$project/${7:-stage/$id/install.sh}
  current_stage=$id
  stage_started=$SECONDS
  record_stage running 0
  [[ ${GITHUB_ACTIONS:-} != true ]] || echo "::group::$id - $title"
  exec > >(trap - EXIT INT TERM; exec tee "$build_dir/logs/$id.log") 2>&1
  stage_log_pid=$!
  printf '[%02d/%02d] %s (%s)\n%s\n' "$position" "$total" "$title" "$context" "$description"
  # Layers and controller components are optional. The catalogue makes a missing
  # layer a no-op; installers still execute in their declared context.
  if [[ -d $project/stage/$id/rootfs_layer ]]; then
    python3 "$project/image_builder/stage_catalog.py" layer --root "$project" --stage "$id" --target "$rootfs"
  fi
  if [[ $context == host-shell ]]; then
    local ENODECTL_BUILD_HOST_SHELL=1
    unset -f build_stage
    source "$stage_file"
    build_stage
  elif [[ $context == host ]]; then
    ENODECTL_PROJECT="$project" ENODECTL_TARGET_ROOT="$rootfs" ENODECTL_BUILD_DIR="$build_dir" \
      ENODECTL_IMAGE_VERSION="$version" ENODECTL_OTA_CERTIFICATE="$certificate" \
        bash "$stage_file"
  elif [[ $context == chroot ]]; then
    if [[ ! -d $rootfs/run/enodectl-build/stage ]]; then
      python3 - "$project/stage" "$rootfs/run/enodectl-build/stage" <<'STAGE_FILES'
import shutil,sys
shutil.copytree(sys.argv[1],sys.argv[2],ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
STAGE_FILES
    fi
    chroot "$rootfs" /usr/bin/env ENODECTL_BUILD_CHROOT=1 ENODECTL_STAGE_ROOT=/run/enodectl-build/stage \
        /bin/bash -c 'cd -- "$1"; exec /bin/bash ./install.sh' stage "/run/enodectl-build/stage/$id"
  else
    echo "Unknown stage context: $context" >&2
    return 2
  fi
  finish_stage_log
  record_stage completed 0
  printf 'Completed %s in %ss.\n' "$id" "$((SECONDS - stage_started))"
  current_stage=''
  [[ ${GITHUB_ACTIONS:-} != true ]] || echo '::endgroup::'
}
