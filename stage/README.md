# Stage contract

[`image_builder/stages.json`](../image_builder/stages.json) is a plain JSON array
of stage names in execution order. It has no schema/version or `stages` wrapper.
Every entry names a directory here, including image preparation, software
installation and factory assembly. Dependency order is validated; the runner
does not silently reorder stages. Empty plans, duplicate names and invalid
paths are rejected. Installers must run through the root `build.sh` entrypoint.

Each stage requires only:

* `stage.json`: identity, title/description, context, dependencies and runtime registrations.
* `install.sh`: the single execution entrypoint.

Add these optional assets only when needed; do not create placeholder files or
empty directories:

* `rootfs_layer/`: owned files, copied into the mounted image before installation.
* `enodectl_component/`: Python configuration, commands and Textual presentation.
* `requirements.txt`: runtime Python dependencies bundled by the enodectl stage.
* `config/`: source configuration owned by this stage, consumed by its installer
  or build helper. It is not automatically copied into the root filesystem.

Keep stage-specific source configuration with its owner. The base download
source/checksum lives in `image-inputs/config/base-image.json`; the download
helper and image input validator both read it there. The OTA release source is
`ota/config/update-source.json`, installed by ota's own installer at
`/usr/share/network-exit/update-source.json`. Reserve a top-level `config/`
directory for configuration genuinely shared by multiple stages; none is
currently needed. Device-specific runtime settings and secrets stay on STATE.

`image-bootchain/config/network-exit.cmd` is the U-Boot A/B selector source.
Its installer compiles it with `mkimage` into the image's `/boot/boot.scr` and
copies the source to `/boot/boot.cmd`, after package updates have finished.
Factory assembly includes those files with the matching kernel, DTB and
initramfs in both root slots. At boot the selector reads redundant control
records, chooses the pending/confirmed slot, persists a trial decrement before
loading its kernel, and falls back when attempts are exhausted. The raw
SPL/U-Boot binary still comes from the Armbian base and is preserved by factory
assembly. Relocating this source does not change the on-card `/boot` paths.

Missing optional assets are skipped. An installer-only stage can consist of two
files. A controller component does not require a filesystem layer or dependency
file; a layer does not require Python code. Declared runtime entrypoints still
must resolve to a selected component. Existing optional assets must have the
correct file/directory type and cannot be symlinks.

The `context` determines how the runner calls `install.sh`:

| Context | Execution |
| --- | --- |
| `host-shell` | Source the script and call `build_stage` in the shared runner shell; used by `image-*` stages to retain mount/loop/resource state |
| `host` | Run a separate Bash process on the builder with the target root, workspace, version and public certificate in `ENODECTL_*` variables; used by the shiv packager |
| `chroot` | Run Bash inside the ARM64 image with the guarded stage root and stage working directory |

`image-inputs`, `image-workspace`, `image-rootfs` and `image-chroot` prepare the
working image. `image-bootchain`, `image-finalize`, `image-factory`,
`image-compress` and `image-manifests` finish it. They use the same plan and
metadata validation as software stages. Shared runner mechanics remain under
`image_builder/`; there is no second stage directory there. The root
`build.sh` delegates to `image_builder/build.sh`. Root `package-release.py`
delegates to `image_builder.package_release`. Python helper CLIs are invoked
with `python3 -m image_builder.<module>` from the project root.

Chroot installers inherit a service-start inhibitor from image preparation. Do not use
`systemctl start`, download software on first boot, or run commands on the builder
that change its accounts, network or service configuration.

The minimal metadata is:

```json
{
  "id": "example",
  "title": "Install example",
  "description": "Install and configure the example capability",
  "context": "chroot",
  "requires": ["base-system"],
  "packages": ["example-package"]
}
```

The shell entrypoint normally starts with the same guard as the other installers,
sources `${ENODECTL_STAGE_ROOT}/common.sh`, and calls `install_declared_packages`.
External APT repositories and their keys are owned by the corresponding stage.
Configuration files and systemd units belong in its layer; Python application
code belongs in `enodectl_component/`, not in a second copy under the layer.
Host-shell scripts check `ENODECTL_BUILD_HOST_SHELL=1` before defining their
`build_stage` function; they must not launch themselves as standalone builders.

Runtime registrations use trusted `module:function` references:

* `commands`: `name`, `help`, optional `register`, `handler`, and optional `root` (default true).
* `service`: name, label, units, mandatory flag, `view` factory and optional-service validation/lifecycle hooks.
* `required_check`: a required capability's readiness check before first-boot completion.
* `before_services`: apply protection before selected services start.
* `before_complete`: checks before persisting setup completion.
* `status`: local status hook.
* `private`: root-scoped callables or script modules for installed systemd wrappers.
* `verify`: command checks applied only when the stage is selected.

The runtime registry is compiled into the archive, not loaded from STATE. There
is no live plugin download or first-boot Python installation. Root/private module
registration is privileged application code. Keep module names unique; duplicate
Python files, CLI registrations and image file ownership fail the build.

The core's responsibilities are CLI dispatch, registry loading and shared terminal
widgets. SSH, service management, OTA, reboot and watchdog behavior are provided
by their respective stages. Non-Python binaries, kernel support, CPython and its
standard library remain Debian/Armbian image assets. Runtime Python wheels are
resolved for ARM64 and the image's Python version by the builder.

`enodectl config` enters the shared Textual dashboard after any required first-boot account/password preparation.
Each configurable service registers a `service.view` factory returning a
Textual screen. SSH and Tailscale set `mandatory: true` and
`managed_startup: true`; fixed system services are also mandatory but are not
indiscriminately started by the dashboard. Optional services alone expose a
startup toggle. System policy/status pages are read-only.

Keep presentation and operations separate. For example,
`tailscale/enodectl_component/tailscale_view.py` declares the fields and screen, while
`tailscale_config.py` validates/saves state and invokes the client. The shared
`enodectl_core/textual_ui.py` renders forms, confirmations and details; it does
not own provider settings. `service-manager/enodectl_component/service_manager.py` owns
startup choices and completion, while `service_tui.py` draws the dashboard.
SSH uses `ssh_view.py` with `ssh_editor.py` and `ssh_access.py` backends.
OTA uses `ota_status_view.py` for its install form, `ota_update_config.py` for
form actions, `update.py` for normal signed installation and `ota_recovery.py`
for explicitly confirmed local signature-bypass recovery. Required system
components retain their non-disable policy; the OTA page exposes an update
action while other fixed policy pages remain read-only.

Use ASCII backticks to mark exact input phrases, literal field values, command
suggestions and key names in user-facing prompts. Typed confirmations must show
the complete phrase and say to omit the backticks. Keep the accepted input
unchanged; display delimiters are never part of a confirmation value. Use literal
text rendering in the TUI, and quote shell messages so backticks cannot execute
command substitutions.

Loading a form must be read-only and reuse existing device identities. Sensitive
fields load blank, with documented blank-to-retain behavior. Writes need an
explicit confirmation and run in workers rather than blocking the UI. Network
changes retain the independent systemd rollback timer even if the UI disconnects.
All runtime Textual dependencies are declared in enodectl's `requirements.txt`
and included in shiv; the device never installs them at first boot.

Run `python3 -m image_builder.check_stages` to validate sources, `bash build.sh
--list-stages` to inspect order, and `python3 -m image_builder.stage_catalog assets` to
inspect file ownership. Full image and physical-board validation remain required.

The network stage owns initial-static/operational-DHCP wired profiles and mDNS defaults and the Wi-Fi form/backend. Its initramfs init-bottom script runs after system-state's STATE bind mounts, prepares private Wi-Fi files and restores unconfirmed changes before the Netplan generator. Wi-Fi credentials are STATE-backed; Ethernet defaults remain versioned in the image. The shared 90-second network journal commits or rolls back Wi-Fi and firewall settings together. Tests run the real Netplan generator only under disposable roots and never apply configuration to the builder's network.
