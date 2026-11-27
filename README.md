# Embedded Node Controller

A configurable Orange Pi Zero 3 appliance providing remote connectivity and an optional home broadband exit. The device image is independent of a country, proxy protocol or external server distribution. This repository maintains the appliance, factory flashing package and signed A/B OTA package. Remote servers and client deployments are outside its scope.

The management tool is **Embedded Node Controller**, invoked as `enodectl`. Initialization, local status, image updates, bootchain selection and operating-system reboot requests share this entry point. Internal service/data paths retain their existing `network-exit` names.

**Status: factory and signed OTA packages have been built and verified on an ARM64 GitHub Actions runner. Board boot, A/B rollback and watchdog reset remain untested; the boot selector is not yet hardware-validated.**

All shipped prompts and project documentation use ASCII English.

## Capabilities and initialization

Tailscale, WireGuard tools, Cloudflare Tunnel, sing-box and ddclient are installed during the image build. First boot never downloads or installs these components. Tailscale is required for administrative connectivity; the others are optional and remain disabled until selected. DDNS and the optional host firewall can be configured after deployment through enodectl.

The ordinary login account is `pi` (UID/GID 1000). Direct root SSH and ordinary root console login are disabled. The pi password stays locked and is never requested by setup. Root starts with a locked password; setup requires a per-device root recovery password. No shared usable password is shipped. The node wizard replaces Armbian's original root-login setup.

The device provides automatic first-boot setup through a Textual terminal interface with English ASCII labels. For SSH provisioning, supply your public key while flashing, then log in:

```bash
./burn.sh /dev/xxx --ssh-key ~/.ssh/id_ed25519.pub
ssh pi@DEVICE_ADDRESS
```

The first interactive `pi` login automatically opens setup; no setup command is required. The OS password program first sets the root recovery password. The Textual dashboard then opens for Tailscale authorization, SSH access and optional components. Configure the selected services, apply their startup choices, and choose `Exit`. Completion requires local Tailscale authorization, SSH access and successful service operations. After successful setup, the same SSH session continues to a normal shell. Interrupted or failed setup is offered again at the next interactive login. Completed setup is recorded on STATE, so reboot and compatible OTA updates do not repeat it.

If the provisioned login key is unavailable, connect a local display/keyboard or serial console and press `Enter` at the first-boot setup prompt. The console runs the same wizard before normal login and offers a retry if setup fails, including when the root recovery password was saved but network setup is incomplete. Waiting for `Enter` prevents an unattended console from claiming the setup lock ahead of SSH. Only one setup session may run at a time. The installed `passwd` program owns root password input; the password never enters command arguments or logs. Password/account records are persisted on STATE immediately.

The pi account is a key-authorized administrator with passwordless sudo, including `sudo enodectl config` and `sudo -i`. The public `enodectl` command is also available directly on pi's ordinary PATH: administrative commands such as `enodectl config` use the existing sudo policy automatically, while help remains unprivileged. Private backends require explicit root invocation. No pi password is needed for first-boot setup, resuming setup or later administration. The former narrow bootstrap sudo grants are no longer shipped. The root recovery password is still required during setup and persisted on STATE; it is not used to authenticate normal sudo requests. Only provision public keys belonging to trusted administrators: their holders have full root capabilities through sudo.

The automatic entry is `/etc/profile.d/90-enodectl-firstboot.sh`, which invokes `/usr/lib/network-exit/login-bootstrap` only for interactive `pi` terminal logins. Non-interactive SSH commands, SFTP and batch jobs do not launch a wizard. A root-owned public hint at `/run/network-exit/setup-complete` avoids repeated prompts without exposing the private configuration directory. Whenever the privileged automatic entry is invoked, it rechecks the authoritative STATE completion marker under the setup lock. A missing runtime hint on an already configured node therefore does not rerun setup. Failed automatic setup ends that interactive login; reconnect to continue. This login behavior is not a restriction on a trusted administrator's full sudo capabilities.

SSH permits only public-key authentication for pi. `AuthenticationMethods publickey`, `PasswordAuthentication no`, `KbdInteractiveAuthentication no` and `PermitEmptyPasswords no` are enforced while PAM account/session handling remains enabled. The password is locked rather than deleted or made empty, and no account-expiry lock is introduced. Boot preparation also locks a usable pi password restored from an older STATE snapshot and saves that policy. Direct `su` from an ordinary account directs the user to sudo; privileged services retain a private vendor implementation. Root's recovery password does not enable root SSH or ordinary console login. Once setup is complete, a password-based pi console login is unavailable; normal administration uses SSH public keys. Initial local/serial console setup remains available before login.

Initial provisioning supports a direct Ethernet cable from the operator computer. Before the first successful setup the node provides temporary address `192.168.77.1/24`; set the computer to `192.168.77.2/24` with no gateway/DNS on that connection, then run `ssh -t pi@192.168.77.1`. After setup the temporary address is removed and ordinary Ethernet uses DHCP. Confirmed Wi-Fi settings persist and reconnect automatically. A factory-provisioned SSH key is required. These physical-network paths still require board testing.

The first-boot wizard authorizes the Tailscale client through the selected control plane and offers:

- Optional SSH public-key import from an operator-supplied HTTPS URL into `pi`.
- Optional Tailscale exit-node advertisement; administrative access uses ordinary OpenSSH.
- Optional WireGuard broadband forwarding, using a peer maintained outside this repository.
- Optional Cloudflare Tunnel, using a token supplied by the operator.
- Optional sing-box, importing a configuration or creating an authenticated private proxy. The generated listener defaults to localhost; its password is stored in the root-only configuration.

The Tailscale client supports either the official Tailscale control plane or an operator-managed Headscale server. During first-boot setup, select `official` (the first-setup default) or `headscale`, then supply its HTTPS origin, for example `https://headscale.example.com:8443`. The URL must use a certificate trusted by the device and cannot contain credentials, a path, query or fragment. No TLS verification bypass is provided. Authorization follows the selected server's interactive registration flow; this repository does not deploy or administer the Headscale server.

The selected provider and URL are saved in `/etc/network-exit/tailscale-control.json` on STATE with mode 600. The client's own preferences and identity persist in its STATE-backed data directory. Ordinary boot starts `tailscaled` using that state; it does not log in again or contact a different control plane on failure. Reopening configuration loads the saved choice. Older initialized devices without this new file default to the control server read from the local client preferences, including existing Headscale connections. Applying a login that needs authorization displays its URL in a scrollable dialog; open it on an operator computer, then use `Check status` and `Exit` after authorization succeeds. If the URL is not yet available, the dialog includes the client's reported health errors (such as DNS failure). A direct setup-only Ethernet connection is not an Internet uplink; configure Wi-Fi or connect Ethernet to a network providing DHCP, DNS and Internet access before requesting authorization.

Switching an existing identity between control servers prompts for explicit confirmation only when the live control plane actually changes, defaults to cancellation, and uses `--force-reauth`. Ordinary configuration edits do not show a reauthentication field or additional confirmation. A switch can disconnect a Tailscale-carried SSH session, so use an independent LAN/console session. Selecting `official` explicitly supplies the official server URL, rather than retaining a previous Headscale URL. Failed or interrupted authorization leaves initialization incomplete and retains the requested URL for retry; it never automatically falls back to the other provider. Confirmed authorization requires the local client to be Running on the selected control server. Both providers use ordinary OpenSSH (`--ssh=false`); approve any advertised exit routes and access policies on the selected control plane.

Initialization is marked complete only after the selected enable/disable operations succeed, Tailscale is authorized and an SSH authorization is still present. Publishing changed component choices clears any earlier completion marker first. A service-setup failure leaves the device incomplete, and OTA installation remains blocked until initialization succeeds.

Every initialized device has its own identities and secrets. Factory images contain no shared WireGuard key, Tailscale state, SSH host keys or Cloudflare token. Complete authorization, restrict tailnet access, reboot, and verify connectivity before shipping. Ethernet retains DHCP; an optional Wi-Fi profile reconnects automatically. At the destination, Ethernet or a reachable preconfigured Wi-Fi network provides connectivity when outbound connections are permitted and credentials remain valid.

Manage service configuration and startup choices through the Textual dashboard:

```bash
sudo enodectl config
sudo enodectl status
sudo enodectl watchdog status
```

Select a row and choose `Configure / view` to edit its current configuration or read its system policy. All configurable services have repeatable forms: Tailscale/Headscale, SSH keys and CA trust, WireGuard peers, Cloudflare token, sing-box JSON, DDNS and network/firewall policy. Opening a form does not install software, generate a new identity or reset settings. Each form applies only after confirmation; Back or Exit asks before discarding edits. Cloudflare/DDNS secrets load as blank masked fields; leaving them blank retains the existing secret for the same provider. WireGuard keeps its existing private key. The sing-box editor contains the full JSON, including any embedded credentials, so use a trusted terminal.

The dashboard is a read-only overview of saved startup choices and current systemd runtime states. Rows appear immediately and runtime states are refreshed in the background every five seconds without moving the current selection. Each optional service page has a Startup ON/OFF selector. Apply configuration saves the profile and applies the selected startup state after a single confirmation. SSH, Tailscale and system components cannot be disabled through either the UI or backend. Fixed policy pages are read-only; the OTA page also exposes explicit signed installation and recovery actions. Enabling validates every newly selected service before performing changes. Failed operations reload saved choices and runtime status; earlier operations may already have completed. An apply failure leaves setup incomplete until retried and confirmed healthy.

Detailed status and recent current-boot logs are available in each service's configuration page, not the dashboard. Set Startup ON while applying a Cloudflare token to start the tunnel, or OFF to save without starting it; then inspect its registration logs. Exit health checks require a successful HTTPS request to Cloudflare's public site, Tailscale authorization and active required/selected service units. RAUC is on-demand and need not remain active. A failed check displays errors; explicitly exiting despite them returns an error rather than reporting successful setup.

`sudo enodectl config` opens the same dashboard after initial account setup. The old `config ssh`, `config service` and `config network` routes are removed. Use Up/Down to select a service or move between fields, Enter to open the selected service, and Right to reach the action buttons. Left/Right selects buttons, cycles choices, or moves the cursor in a single-line field. Enter opens a choice menu, advances a single-line field or activates a button; use the arrow keys and Enter inside choice menus. For multiline JSON, Enter starts editing and Escape returns to navigation. Escape returns from screens, the dashboard refreshes automatically (F5 can request an immediate refresh), and Ctrl+Q or Exit performs the exit health check. The single Exit action also completes initial setup when needed. No Tab navigation is required. Mouse clicks, scrolling and dragging use SGR reporting without continuous hover traffic; malformed UTF-8 input bytes are discarded without ending the input thread or corrupting valid UTF-8 characters. Check status opens a responsive dialog immediately, offers refresh and Back, and reports a timeout instead of blocking navigation. A terminal around 110 columns by 36 rows is recommended. Textual and its Python dependencies are bundled in the shiv archive during the build; no device-side pip/download is needed. ARM64 runtime and memory use still require board testing.

Optional components are `wireguard`, `cloudflared`, `singbox` and `ddns`. A previously unconfigured component needs its settings before it can be enabled. Changes to administrator accounts or the root recovery password should be followed by `Exit` to save their records. Keep the pi password locked.

## Network policy and optional firewall

```bash
sudo enodectl config
```

Select `Network / Wi-Fi / firewall`, then `Configure / view`. This form manages the broadband interface, optional host firewall, SSH source networks, additional inbound TCP/UDP ports and persistent Wi-Fi. `Check status` displays addresses, IPv4/IPv6 routes and resolver settings. It uses the base image's Netplan/systemd-networkd stack with wpa_supplicant; all dependencies and firmware are preinstalled. Ethernet and Wi-Fi use DHCP for addresses, DNS and default routes. Static-address/custom-DNS editing is not part of this form. Forwarding follows configured WireGuard/Tailscale exit capabilities instead of a separate unrestricted router switch.

Choose `Scan Wi-Fi networks` to list nearby visible SSIDs, sorted by signal strength, with their advertised WPA2/WPA3 Personal security modes. Use Up/Down and Enter to select a supported network, then enter its password. A scan temporarily raises an administratively down radio and restores that state afterward; it does not connect to or save a network. Hidden networks can still be entered manually. Open, enterprise and other unsupported security modes are labeled rather than silently accepted. Configuration and scan failures appear in a scrollable error dialog; passwords and captured command output are not dumped into the dialog. Applying Wi-Fi settings still uses the existing timed trial and confirmation flow.

### Direct Ethernet provisioning and persistent Wi-Fi

The sequence is: flash with an SSH public key -> direct Ethernet connection -> configure the node -> ordinary DHCP networking. Before the first successful setup, the built-in Ethernet interface has temporary management address `192.168.77.1/24`. DHCP also remains enabled during this phase so initialization can use a router or shared connection when one is available. The static address is a provisioning aid, not an ongoing maintenance address. The node does not act as a DHCP server or advertise itself as the computer's default gateway.

1. Flash with `--ssh-key FILE`, connect an Ethernet cable between the computer and node, and power on the node. Set the computer's Ethernet IPv4 address to `192.168.77.2`, netmask `255.255.255.0`, with no gateway or DNS on that connection. The computer can retain Wi-Fi for its own Internet access.
2. Run `ssh -t pi@192.168.77.1`. The first login opens setup automatically. Open `Network / Wi-Fi / firewall` -> `Configure / view` to configure Wi-Fi if desired. Wired mDNS at `enode.local` is a convenience when supported by the computer; it is not required for the fixed-address setup path.
3. Enable Wi-Fi, use `auto` for a single radio or enter its interface name, and fill in the SSID, WPA2 Personal or WPA3 Personal authentication, password and optional hidden-network/country settings. For example, use `JP` for a deployment in Japan. The selected radio/driver must support the chosen authentication mode. Passwords are masked; a blank password retains the saved secret only for the same SSID and authentication mode.
4. Apply the form and keep the Ethernet cable connected. Before the 90-second trial expires, test a NEW SSH connection through the Wi-Fi address or an already authorized Tailscale connection, then confirm. Cancellation, timeout or reboot restores the previous network/Wi-Fi configuration. Complete Tailscale authorization and choose `Exit` once Internet access is available.
5. After successful setup, the background network controller retires `192.168.77.1` on its next check. That SSH connection can disconnect. Reconnect through the router-assigned DHCP address, configured Wi-Fi or Tailscale. A permanent STATE provisioning marker prevents a later configuration failure from reopening the factory static address. Reboots of initialized devices start directly in DHCP mode.

Wi-Fi settings and their generated Netplan file live under STATE's private `config/connectivity/` directory (0700, files 0600). `/etc/netplan/90-enode-wifi.yaml` points into this persisted configuration. A network-owned initramfs hook selects the initial-static or ordinary-DHCP wired profile and restores unconfirmed Wi-Fi changes before PID 1's Netplan generator runs. RAM OverlayFS resets and compatible OTA updates retain the confirmed Wi-Fi profile. Wi-Fi is a client connection to an existing access point; there is no captive portal or Wi-Fi hotspot. An older fallback system image that predates this feature retains its original Ethernet-only behavior.

Ethernet DHCP route metrics are 100 and Wi-Fi DHCP metrics are 600; Ethernet's IPv6 RA metric is also 100, ahead of networkd's default Wi-Fi RA metric. This prefers wired routing when both links provide a default route and permits Wi-Fi routing when the wired route disappears. It is not an Internet-health probe or a seamless session-preserving failover. The uplink refresh timer checks approximately every 15 seconds and updates managed firewall/WireGuard NAT only when the selected uplink changes. Tailscale uses the available OS routes to reconnect. Initial-static provisioning, mDNS discovery, real Wi-Fi association and handover still require board testing.

The host firewall is preinstalled but OFF by default, so initial SSH provisioning remains available. When enabled, its input and forward chains default to drop. It allows loopback, established/related traffic, necessary DHCP and ICMP/ICMPv6, link-local mDNS, Tailscale traffic subject to Tailscale's own policy, and UDP 41641 for direct Tailscale connections. SSH on port 22 is permitted from the configured source CIDRs on physical management interfaces, including Ethernet and Wi-Fi; defaults cover RFC1918 IPv4, IPv6 ULA and link-local networks. Existing explicit CIDR lists are preserved. Additional listener ports must be explicitly configured on the selected broadband interface. Outbound traffic is unrestricted. There is no automatic router port forwarding or UPnP.

Only the `enode_host`, `ne_home_guard` and `ne_home_nat` tables are managed. Updates use a checked, atomic nftables transaction and never flush the entire ruleset or Tailscale's rules. Global forwarding is enabled only after rules are installed. Tailscale retains its default netfilter integration. Allowed forward paths are limited to selected exit services; the independent WG guard additionally restricts tunnel sources, private destinations and return traffic. Turning the host firewall OFF does not remove WG guard/NAT tables. Stopping a tunnel retains its protection.

`auto` chooses the lowest-metric main IPv4 default-route interface, then an existing WG uplink hint, then an available non-tunnel interface. Select an explicit interface to pin deployments to one uplink. The validated interface is used for the managed firewall/WG NAT and DDNS address discovery. Tunnel and loopback interfaces are rejected.

Network changes are trials: the previous configuration and generated WG rules are saved on STATE and an independent systemd timer restores it after 90 seconds unless confirmed. Keep the old session open, test a NEW SSH connection, then confirm. Existing established sessions are allowed, so keeping the old connection alone does not prove new access works. Cancel/disconnect leaves the rollback timer active; a reboot also restores an unconfirmed configuration before applying network policy. A failed nftables validation leaves existing rules in place. Rollback retries on failure; this remains subject to working storage/systemd/kernel and requires on-board validation.

At boot `network-exit-network.service` restores policy after STATE and before SSH/Tailscale/exit services. WG requires successful network policy and guard setup; Cloudflare Tunnel, sing-box and DDNS also require the network-policy service. SSH and the mandatory Tailscale client remain available for recovery if policy setup fails. No new network packages are fetched at boot. The required Tailscale management client remains separate from optional exit forwarding.

## Optional DDNS

Open `sudo enodectl config`, select DDNS and `Configure / view`, set Startup ON and apply the profile to start the timer. Supported profiles are Cloudflare API token, DuckDNS token and an HTTPS DynDNS2 provider. One profile updates either an A record or an AAAA record; the provider must support the selected family. Cloudflare needs the zone and complete hostname; DuckDNS uses the complete `.duckdns.org` hostname; DynDNS2 needs the HTTPS server hostname and login. Tokens/passwords are masked in the editor, retained when left blank only for the same provider, and never supplied in command arguments or emitted to the journal.

The profile is stored as root-only `/etc/network-exit/ddns.json` on STATE. Cache and sanitized status live in `/var/lib/network-exit/ddns`. The image ships no profile or shared provider credentials. `network-exit-ddns.timer` starts only after selection, checks approximately every minute and honors the configured 300-86400 second update interval. Stopping DDNS stops its timer and any active update; it does not delete the provider's DNS record. Profile changes clear the old provider cache.

Address discovery uses HTTPS ipify through curl bound to the selected broadband interface, bypasses proxy environment variables, rejects nonglobal/wrong-family addresses and has no fallback to a tunnel. ddclient receives that explicit address, requires HTTPS and runs once under systemd with timeouts. Provider responses are captured privately; status reports last attempt, last successful check/update, address/interface and a generic error without credential contents. Use `Check status` in the DDNS form; an active timer means scheduling is enabled, not that the provider accepted an update.

DDNS does not open firewall ports, bypass CGNAT, configure the home router or make a private network publicly reachable. The current outbound WG connection and Tailscale management do not require DDNS. Real provider updates, interface selection and access from the destination network still require acceptance testing.

Services are managed by systemd: `tailscaled`, `wg-quick@wg-home`, `network-exit-guard`, `network-exit-cloudflared`, and `network-exit-singbox`. Selected services are restored from persistent choices on every boot. Long-running connection services retry failed startup every 10 seconds; DDNS uses its bounded one-shot timer schedule. WireGuard's `active (exited)` status is normal for a kernel tunnel; keepalive and protocol retries handle temporary network loss. No service downloads packages at startup.

## Bootchain states and slot control

```bash
sudo enodectl status
sudo enodectl bootchain status
sudo enodectl bootchain switch B
sudo enodectl reboot
# Explicitly combine selection and reboot:
sudo enodectl bootchain switch A --reboot
```

`status` combines bootchain and installed service status without checking the Internet. `bootchain status` returns JSON with the running version, current/confirmed/pending slots, validity flags, remaining future boot attempts and the active trial deadline. The states are:

| State | Meaning |
| --- | --- |
| `confirmed` | The running slot is confirmed and no other slot is pending |
| `pending-reboot` | Another slot is selected for the next trial boot |
| `trial` | The running slot is pending health confirmation |
| `fallback` | The bootloader could not load the selected trial and started the confirmed slot; attempts remain |
| `rolled-back` | Trial attempts were exhausted and the confirmed slot is running |
| `recovery-pending` | While an unconfirmed slot is running, the operator selected the confirmed slot for the next boot |
| `inconsistent` | Slot validity, current slot and boot metadata do not agree |

Missing or unreadable boot metadata is reported as an error; it is never assumed to be confirmed. Zero remaining attempts does not itself mean success: the final attempt may still be running and waiting for confirmation.

`bootchain switch` changes only next-boot selection through RAUC. It does not install an image or alter the running filesystem, and it retains the previous confirmed slot. The target must be a valid existing slot, must differ from the running slot, and receives the same three-attempt health-confirmed trial as an image update. Selecting an already pending slot preserves its remaining attempts. Retrying an exhausted slot requires `--retry-failed`. A switch away from the currently running trial does not cancel that trial's existing deadline. Add `--reboot` only when an immediate controller reboot is intended.

## Reboot and power policy

Use `sudo enodectl reboot` for an ordinary reboot. `--reason NAME` records a reason; `--force` skips normal service shutdown for recovery. Requests are recorded under `/run/enodectl` and, when STATE is mounted and writable, in `/var/lib/network-exit/last-reboot.json`. Logging failure does not intentionally block recovery.

The image replaces the normal `reboot`, `poweroff`, `halt`, `shutdown` and `telinit` frontends with English instructions directing the operator to `enodectl`. Poweroff is not supported by the controller. A guarded `systemctl` retains ordinary service management and read-only queries, while rejecting direct power operations and activation of power/reboot targets. Vendor frontends are diverted with dpkg so package updates do not silently restore them; the controller uses a private systemctl backend.

Poweroff, halt, kexec and sleep targets are masked. Power/reboot/sleep keys, lid actions and Ctrl-Alt-Del reboot shortcuts are disabled. Image-update and slot-switch `--reboot` actions, health-check failures and trial-deadline failures use the controller reboot API/command. If the controller itself cannot execute a recovery request, systemd retains a forced-reboot fallback. U-Boot resets, kernel panic, the hardware watchdog and physical power cycling remain independent recovery paths.

This is an operational policy, not a security boundary against full root access: root can deliberately use low-level interfaces or change the policy. The systemd manager and its internal shutdown implementation remain present because the operating system and controller require them. No host power commands are replaced by source tests or local packaging.

## SSH public-key import

First-boot configuration includes a Textual SSH-access page. Open the service dashboard later and select SSH / public keys and CA:

```bash
sudo enodectl config
```

The table distinguishes ordinary login keys and trusted SSH user CA keys by purpose, with SHA256 fingerprints, types and comments. Up/Down selects rows, Enter opens key details, Right reaches the action buttons, and Escape goes back. Select a key to inspect or remove it, or choose an Add action to import from a device-local file, HTTPS URL such as `https://github.com/YOUR_ACCOUNT.keys`, or a pasted OpenSSH public-key line. Several keys can be imported from one file or URL. All UI labels are ASCII English and existing comments are escaped for display so they cannot inject terminal control sequences.

An Add form stages its validated import; `Save authorizations` and confirmation publish the complete SSH draft. Cancel discards staged edits. At least one validated authorization must remain across the ordinary-key and user-CA lists. The menu rejects removing the final entry, saving an empty list, and completing first-boot configuration without an authorization. A CA-only configuration is allowed and requires a valid certificate and a correct device clock for login. This guard checks stored public-key material; it cannot prove possession of a corresponding private key, satisfy existing key restrictions, or guarantee network reachability. Test a new SSH connection before closing the existing one.

Duplicate key material is shown once per purpose; removing that row removes all its occurrences for that purpose. The same public key can remain in the other purpose. Existing comments and authorized_keys restrictions are preserved, and an unrestricted copy is not added over an existing restricted key. Unrecognized existing lines are preserved but not counted as authorizations.

SSH-access menus share a STATE-backed lock. A stale menu refuses to overwrite intervening changes from another session and reloads the current list. When changing both files, a nonempty replacement is written and synced before clearing the other kind of access. Each file update is atomic; a failure between writes can leave a mix of the old and new authorizations, which is reloaded for review. This avoids an intentionally empty authorization gap but does not make SD hardware or multi-file writes atomic. Full root access can still bypass these tool-level safeguards.

URLs are fetched only for an explicit import and are not polled on boot. Use sources controlled by trusted administrators: imported login keys and CA trust grant access to pi's passwordless sudo privileges.

Use the SSH page's Add actions under `enodectl config` for all on-device imports; no standalone importer or separate add-key subcommand is shipped. The supported SSH login target is `pi`. The menu requires mounted persistent STATE and does not create accounts or relax the SSH policy. Factory-card personalization can still provision the initial pi public key before first boot.

Remote downloads require HTTPS, including redirects, and are limited to 64 KiB with a network timeout. All keys are validated with the installed `ssh-keygen` before saving; private keys, HTML and imported authorization options are rejected. Repeated imports deduplicate by key material, including entries with existing restrictions. Keys removed from the remote source are not removed locally unless selected for removal in the menu. Review SHA256 fingerprints and confirm Save to apply the draft. No SSH daemon restart is needed.

The `pi` account's `.ssh/authorized_keys` resides on STATE through the persistent home mount, survives reboot and system-slot updates, and uses mode 600 with a mode 700 `.ssh` directory. Symlinks and hard-linked authorization files are rejected. SSH access and persistence still require device-level acceptance testing.

## SSH user CA and hardware-backed keys

The SSH-access page in first-boot setup or `sudo enodectl config` can optionally trust your SSH user CA. Choose `Add CA key`, review its fingerprints and save the authorizations. Add old and new CA keys together for a rotation; remove a selected CA row when it is no longer trusted. Removing all CA entries is allowed only if an ordinary login key remains. Private keys, certificates, PEM certificates and imported authorized_keys options are rejected.

Trust is stored in `/etc/network-exit/ssh-user-ca.pub` on STATE and survives reboot and compatible A/B OTA. It starts empty, with no default CA trusted. Only public keys are stored; the SSH CA private key stays with the operator and is separate from the OTA signing identity. There is no automatic download or CA refresh on boot. SSH reads the trust file for new authentications, so changing the public trust set does not require a service restart.

OpenSSH accepts a user certificate only when its signature is trusted, its validity interval includes the device time, and its principals include `pi`. Password authentication and root SSH remain disabled. Ordinary public-key login continues alongside certificate login. A CA authorized this way can issue administrative credentials to all devices trusting it; the certificate grants full pi sudo access. Use short validity periods for temporary helpers and keep the device clock correct. Expiry or removing a CA prevents subsequent authentication using affected certificates, but does not remove ordinary authorized keys, disconnect an already established session or undo changes made by an administrator.

For example, a friend generates a key on their own computer and sends only its `.pub` file. The operator signs it on the operator computer:

```bash
ssh-keygen -s /secure/maintenance-ca -I on-site-repair -n pi -V -5m:+2h friend.pub
```

Return `friend-cert.pub` to the friend, who connects on the home LAN using their own private key:

```bash
ssh -i ./friend -o CertificateFile=./friend-cert.pub pi@DEVICE_LAN_ADDRESS
```

If the corresponding CA private key is available through a local SSH agent, including a configured hardware-backed agent, identify it by its public half:

```bash
ssh-keygen -U -s ./maintenance-ca.pub -I on-site-repair -n pi -V -5m:+2h friend.pub
```

The same OpenSSH public-key content can be configured both as an ordinary login key and as a CA trust key. These are explicit, separate authorizations: adding a key to `authorized_keys` does not make it a CA. For FIDO2 YubiKey authentication, the existing public-key importer and CA importer accept `sk-ssh-ed25519@openssh.com` and `sk-ecdsa-sha2-nistp256@openssh.com`. The matching private credential must actually be held by the authenticator. An existing software public key does not become hardware-backed just by plugging in a YubiKey. OpenPGP/PIV-backed SSH keys can use their normal SSH public-key encoding and a correctly configured local signing agent. The YubiKey stays on the login/signing computer; the appliance verifies signatures and needs no YubiKey middleware.

This is OpenSSH user-certificate authentication, not an X.509/TLS certificate. It helps when the remote tunnel is down but LAN SSH still works. A broken SSH daemon or unusable local networking still requires physical console/card recovery. Such a recovery menu is not implemented here. Hardware signing, touch/PIN prompts and real board certificate login remain acceptance tests.

## Factory and OTA packages

The GitHub Actions image build produces two separate products:

| Product | Purpose |
| --- | --- |
| `network-exit-factory-VERSION-orangepizero3.tar.gz` | Initialize/reflash a whole SD card; includes shared-rootfs components, writer, manifest and checksums |
| `network-exit-ota-VERSION-orangepizero3.raucb` | Signed root-slot update for an already initialized compatible A/B device |

The card must hold the reconstructed factory layout (currently about 10 GiB); the writer checks the exact byte count recorded in its manifest. **16 GB or larger** is recommended. Card capacity is detected rather than fixed to a particular model or size. Extract the factory archive, enter its directory and run on Linux with Python 3.11+:

```bash
./burn.sh --verify-only
./burn.sh /dev/xxx --ssh-key ~/.ssh/id_ed25519.pub
```

Use the whole-card path. The writer checks image integrity, device type, capacity, read-only and mounted status; requests sudo; requires typing the full confirmation phrase; writes and reads back the image. For `/dev/sda`, enter exactly `ERASE /dev/sda` without the surrounding backticks. Entering only `ERASE` cancels without writing. All existing card data is overwritten. `--dry-run` inspects the proposed operation without writing.

Factory transport format 2 stores exactly four XZ-compressed payloads under `payload/`: `boot-prefix.img.xz`, `rootfs.img.xz`, `boot-control.img.xz` and `state.img.xz`. `release.json` maps the single rootfs payload to both independent on-card system slots. It records compressed and raw SHA256 values, exact decompressed sizes and a contiguous write map covering the complete minimum image. The writer checks this map against the actual MBR, verifies every payload before erasure, streams all regions including zero bytes to the card, then independently reads back A, B and every other region before provisioning SSH keys. Payload paths, overlaps, gaps, unsupported layouts and symlinks are rejected. No full raw disk image, mount, target-side download or formatting is needed for ordinary flashing. Use the included Linux writer; generic `.img.xz` flashing tools cannot consume this component package. The writer retains support for legacy format-1 whole-image manifests.

Both factory and OTA products originate from the same built `output/rootfs.img`; packaging checks their rootfs hashes match. Each product contains its own complete offline payload. OTA remains a signed RAUC bundle and requires no device-side format change. Packaging rejects either release asset at or above GitHub's 2 GiB per-file limit before uploading artifacts or creating a Release draft, and logs each final product size.

`--ssh-key FILE` is required for flashing and `--dry-run`. It personalizes only the freshly written STATE filesystem with validated public keys for `pi`. The Linux writer also needs `ssh-keygen`, `losetup`, `debugfs` and `e2fsck`; no host filesystem is mounted. Factory image readback is completed before personalization, then the key contents, ownership, permissions and ext4 consistency are checked. The personalized card intentionally differs from the generic image checksum in STATE. Keys are not embedded in public factory releases or fetched automatically by the device. `--verify-only` checks the package without a target device or SSH key. No real card write is performed by source tests.

The factory layout preserves the original first 4 MiB containing SPL/U-Boot, except the MBR partition records:

| Partition | Size | Contents |
| --- | --- | --- |
| 1 ROOT_A | 4092 MiB | System, kernel, DTB, initramfs and boot script |
| 2 ROOT_B | 4092 MiB | Matching second system slot |
| 3 BOOT_CONTROL | 1 MiB | Two CRC-protected boot-state records |
| 4 STATE | 2048 MiB initially; expands to remaining card space | Device identities, configuration, staged updates and persistent user data |

The factory image has a fixed minimum size and can be written to any supported card large enough to hold it. On boot, the state-preparation service reads the actual card capacity, checks the partition layout and extends only the final STATE partition with the distribution's `growpart`, then expands its existing ext4 filesystem with `resize2fs`. The system slots and boot-control partition stay fixed. Less than 1 MiB may remain for sector alignment. No size such as 128 GB is assumed; the inherited 512-byte-sector MBR layout supports cards up to 2 TiB.

Expansion tools are preinstalled. No formatting or first-boot download occurs. An incomplete expansion retains existing data and is retried on subsequent ordinary boots, including when the partition was expanded but its filesystem was not. Expansion is skipped during a bounded OTA trial. A failed expansion is logged and does not prevent service preparation using the existing STATE capacity. Store general persistent files under `/var/lib/network-exit/data`; credentials keep their existing protected directories. Capacity can be inspected with `df -h /var/lib/network-exit`.

The Armbian root auto-resize service stays masked. This revised partition order is `ab-v2`, with OTA compatibility `network-exit-orangepizero3-v2`. Previous single-partition or `ab-v1` layouts require a new factory flash; root-only OTA cannot move their partitions. Subsequent compatible OTA updates retain the expanded STATE partition and its contents.

## Read-only system and persistent state

Debian's standard `overlayroot` supplies a read-only system lower layer and a RAM-backed upper layer, cleared on every reboot. STATE is a separate writable ext4 partition. An initramfs hook runs after overlayroot, mounts STATE and restores:

- `/etc/network-exit` and `/etc/wireguard` configuration.
- Tailscale identity, SSH host keys and machine ID.
- Administrator account snapshots, `/home` and root SSH credentials.
- RAUC metadata and private temporary OTA staging files.

The configuration directories use root-only permissions. This does not isolate secrets from other root processes. Arbitrary edits elsewhere under `/etc` and runtime package installations disappear on reboot. Use initialization and component helpers for supported persistent changes. Logs are not persisted by default. Never run APT upgrades on deployed appliances; rebuild and install a complete signed image instead.

The RAM upper has a startup-enforced byte limit of one third of detected `MemTotal`, capped at 256 MiB and rounded down to 4 KiB. For example, 512 MiB of detected RAM allows about 171 MiB, while 768 MiB or more allows 256 MiB. This ensures the nominal 1 GiB Zero 3 reaches the full 256 MiB budget even when Linux reports less than 1 GiB of usable RAM. This is a maximum, not a preallocation. The inode ceiling is one inode per 4 KiB of that budget (at most 65536), also bounding growth from many tiny files.

Armbian's independent boot-time and daily package-update check is disabled, along with APT's on-disk binary package caches. On the 1 GiB Zero 3, the vendor check consumed 119 MiB of the 122 MiB RAM upper, preventing the first-boot wizard from updating the root password even though the device still had free RAM. Build verification checks that the cron job is absent and both binary cache paths are disabled. This does not change the RAM budget or the signed image update mechanism.

Debian overlayroot creates its standard tmpfs first. Before the root is made writable for device preparation, `overlay-limit.sh` validates the overlay and its `tmpfs-root` backing mount, then remounts the backing filesystem with explicit `size` and `nr_inodes`. Unexpected layouts, unreadable memory information or a failed remount stop state preparation rather than silently allowing an uncapped upper. Initramfs uses GNU Bash, GNU coreutils, grep, sed and gawk, with util-linux for mount operations. BusyBox is disabled. The generated archive is extracted and its shell, tool versions and account restoration commands are exercised during the build. Initramfs and `systemd-remount-fs.service` select the classic `mount(2)` API to avoid the OverlayFS reconfiguration error observed with util-linux on the board. A one-time serial boot with that setting successfully mounted STATE and reached the first-boot setup prompt. No extra disk partition or boot-time deletion of STATE is introduced.

Reaching either limit returns `ENOSPC` to upper-layer writes and can disrupt services that need those writes. The separate STATE mount remains writable if it has space; keep bulk files and OTA bundles there. Rebooting clears the RAM upper; filling it does not itself request an automatic reboot. This budget does not cap process heaps, kernel memory, `/run`, `/dev/shm`, or any separate `/tmp` tmpfs, and cannot guarantee that the whole device will never run out of memory. Inspect the upper with `df -h /media/root-rw` and `df -i /media/root-rw`, and overall memory with `free -h`. Board-level boot and long-running memory tests are still required.

STATE is shared across both slots and is **not rolled back**. Future configuration migrations must preserve compatibility with the previous system, or provide a reversible migration before rollout. Bootloader, partition-layout and STATE-format changes are not included in root-slot OTA updates; use a new factory image for those changes. Keep signing trust compatible with the previous slot when rotating keys.

## Operator-controlled updates

```bash
enodectl update
enodectl update check
sudo enodectl status
sudo enodectl update install
sudo enodectl update install /path/to/network-exit-ota-VERSION-orangepizero3.raucb
sudo enodectl reboot
```

`enodectl update` defaults to checking the latest published stable image in `stage/ota/config/update-source.json` and reports whether its version differs. It neither installs nor reboots. It does not scan upstream software CVEs. Operators decide when to build, publish and install an image. A different version is reported without assuming an ordering between arbitrary version labels.

`install` explicitly downloads the latest bundle, or accepts a local bundle. It stages a private copy on STATE, verifies its signature, and asks RAUC to install only to the inactive root slot. RAUC enforces the board compatibility string. It does not overwrite the running slot, STATE, boot-control partition or raw SPL/U-Boot. Add `--reboot` only when an immediate reboot is intended. No timer or service performs update installation.

Updates require the factory-provisioned public certificate. The signing private key is never included in the device or packages. SHA256 files detect corruption; the RAUC signature authorizes an OTA image. The pinned base kernel lacks `CONFIG_DM_VERITY`, so bundles explicitly use RAUC's signed `plain` format with root-only local staging. They do not provide live dm-verity protection of the running root filesystem. Bundles must fit in the currently available STATE space with a 64 MiB reserve.

For an explicitly trusted local recovery package, an administrator can bypass
signature authentication for one installation:

```bash
sudo enodectl update install /path/to/recovery.raucb --skip-signature-verification
```

The command requires an interactive terminal and the exact phrase
`INSTALL UNVERIFIED OTA`, entered without the surrounding backticks. The CLI
and TUI both mark the complete phrase this way. In `sudo enodectl config`, select `A/B update services`
and `Configure / view` to open the OTA form. Signature bypass defaults to No;
selecting Yes requires a local path, the same typed phrase and a final Apply
confirmation. The form resets the bypass choice after success or failure and
never reboots automatically. No verification override is saved as configuration.
The normal CLI path and a freshly opened form always require valid signatures.

Recovery accepts only the current board's `plain`, single `rootfs.img` bundle
profile with no hooks, custom handlers or extra manifest sections. An unsigned
SquashFS bundle or one signed by an untrusted/expired key can be used. It does
not establish who produced the image: the supplied filesystem can execute
arbitrary privileged code when booted. Only use this option for bytes obtained
through an independently trusted process. This recovery path is distinct from
RAUC's normal signed installer; `rauc info --no-verify` alone would not bypass
the signature check performed by `rauc install`.

The device must be running its confirmed slot with initialized STATE, without
an active trial. Recovery validates the physical factory partition layout,
board/version, exact root-slot size, image SHA256 and ext4 consistency. It
extracts two bounded members into private STATE storage; allow space for the
local bundle, a full 4092 MiB root image and the reserve. It rejects an in-use or
read-only target, invalidates only the inactive slot before writing, and reads
back its entire payload before arming the usual three health-confirmed trial
boots. Failures retain the confirmed slot. SHA256 checks against an unsigned
manifest detect corruption, but provide no publisher authentication.

Each attempt that reaches writing preparation records its bundle/image hashes,
version, target slot and result under `/var/lib/network-exit/ota-recovery/`.
The installed image supplies its own subsequent OTA trust certificate; this
operation does not edit the running slot's trust file. A/B fallback restores
the previous slot and that slot's original trust. Recovery does not update
RAUC's per-slot installation history; both slots explicitly use
`install-same=true` so later ordinary installations still write their payload.
Real block-device recovery and interruption/rollback tests remain required.

After installation, for example A to B:

1. RAUC marks B bad before writing it. After a successful write, its `set-primary` backend operation restores B bootability and marks it pending with three trial attempts in one durable boot record; no intermediate `set-state good` call is required. A remains confirmed. Manual `enodectl bootchain switch` still rejects an invalid slot.
2. Before loading a B kernel, U-Boot decrements the counter, writes the alternate boot record and reads it back. A write/readback failure keeps A selected. Only a successfully armed trial receives `network_exit.trial=1`; direct fallback to A clears this flag.
3. B starts with its own kernel, DTB and initramfs. After 90 seconds, a health service checks STATE, SSH and the mandatory Tailscale daemon/API. Internet reachability and optional tunnel handshakes are not boot gates. Ordinary confirmed boots and factory initialization do not run trial health/deadline checks.
4. Health checks require mounted STATE, valid SSH configuration, active managed network policy/SSH/Tailscale services and a responsive local Tailscale API. A successful check calls `rauc status mark-good`, confirming B. A failed or timed-out health check requests a controller reboot. An independent, early systemd timer expires at 300 seconds relative to boot, regardless of whether network or STATE preparation has completed. At expiry, a helper reads boot control directly from the kernel's STATE PARTUUID; an unconfirmed trial or failed/unavailable check invokes `enodectl reboot --force` through its recovery service. This helper has a 15-second start timeout and no network/STATE service dependency. Confirmation and expiry share a decision lock; the backend rejects confirmation at or after the deadline. A confirmed system is retained, including when another update has already been staged for the other slot.
5. Three unconfirmed trial boots exhaust the counter; the next boot selects A. A failed trial is retained in status. Another installation then requires explicit `--retry-failed` after investigation.

The boot selector enables verbose initramfs diagnostics (`debug=vc`) by default and uses the 115200 baud serial port as the primary userspace console, while retaining kernel output on HDMI. Kernel panic requests reboot after 10 seconds. PID 1 uses the sunxi hardware watchdog with a 16-second runtime and late-reboot timeout. This covers a kernel/PID 1 stall only once the watchdog is armed. The trial deadline also requires systemd to be running; it cannot act while the kernel or initramfs is stuck before PID 1 starts. We retain the base bootloader/kernel and do not add a continuous U-Boot-to-kernel watchdog handoff. Early hard hangs are an accepted on-site power-cycle recovery case. An LTS kernel does not eliminate hardware, power or driver failures. A power cycle starts the next counted trial; after three unconfirmed attempts the boot selector returns to the confirmed slot. Physical card failure or shared STATE damage may require factory reflashing. There is no GPIO reset mode.

The two boot records use separate 16 KiB CRC-protected environment-format banks, 64 KiB apart inside partition 3. The newest valid sequence wins. Updates never modify both banks together. SD-controller behavior during power loss must still be tested; redundant records do not make SD hardware atomic.

## Optional WireGuard exit contract

```text
Externally managed peer -> WireGuard -> appliance -> NAT -> broadband router -> Internet
Administrator -> Tailscale -> OpenSSH on appliance
```

The device initiates an outbound peer connection and keeps its DHCP default route. The fixed contract is:

| Item | Value |
| --- | --- |
| Device tunnel | `wg-home`, `10.77.0.2/30`, MTU 1280, `Table = off` |
| Authorized peer tunnel source | `10.77.0.1/32` |
| Endpoint | Operator-supplied host and UDP port; default 51820 |
| Persistent keepalive | 25 seconds |
| Forwarding | Peer IPv4 traffic NATed to the selected Ethernet uplink |
| Restrictions | Reject other tunnel sources, private/LAN/multicast destinations, IPv6 forwarding and unsolicited local service access |

The guard replaces only its own `ne_home_guard` and `ne_home_nat` nftables tables. It keeps firewall rules installed when the tunnel stops. Existing firewall policies still apply. Check that the fixed subnet does not conflict with either network.

`network-exit-wg keygen` and `network-exit-wg home --help` remain available for reviewing device-only configuration before applying it. No server or client profiles are generated. Only the external peer can select which of its sing-box/proxy traffic enters this tunnel. The appliance authenticates the peer and packet addresses; it cannot identify the originating application on the peer.

A custom appliance sing-box listener on a WireGuard address additionally requires an explicit local firewall policy: the generated WireGuard guard blocks unsolicited service access. The wizard defaults to localhost, which can also be exposed through an operator-configured authenticated Cloudflare Tunnel. Custom sing-box configurations using privileged listeners or TUN require separate service capability review; the shipped service uses `DynamicUser` and no network administration capability.

## Building and signing

GitHub Actions prebuilds each pull request on an ARM64 runner using the same image assembly and package verification workflow as releases. The `Prebuild PR image` check uploads `image-packages` and stage logs; successive commits cancel obsolete prebuilds for the same PR. Test packages use a freshly generated, short-lived signing certificate and a `pr-` version. Production OTA signing secrets are reserved for release and manual builds. PR artifacts are suitable for a separate factory boot test and their OTA bundles are trusted only by images built with the matching test certificate.

GitHub Actions publishes builds on a pushed `v*` tag or `workflow_dispatch`. A tag build uses that tag as its version and publishes the factory and OTA packages after successful validation. For the first release, merge the reviewed PR into main, configure the OTA signing secrets, then create and push a version tag such as `v0.1.0` on the merged commit. Tag creation and merging are explicit operator actions. A manual build uploads artifacts by default; publication requires the separate `publish_release` input and version tag. No scheduled build or upstream binary branch is maintained. Repository source contains no upstream image/package binaries.

Configure `OTA_SIGNING_CERT` and `OTA_SIGNING_KEY` secrets before building both products. Use a long-lived protected signing identity, retain a secure backup, and keep its certificate valid for the deployed lifetime. Signing material is written only to private runner-temporary files and removed after the job. The public certificate is embedded in the root image. Do not place the signing private key in this repository.

A networked Linux builder needs root, loop/mount permissions and ARM64 execution. Native ARM64 is recommended. On Debian/Ubuntu install builder tools (x86_64 also needs QEMU/binfmt):

```bash
sudo apt-get install -y xz-utils util-linux fdisk e2fsprogs python3 python3-venv curl coreutils u-boot-tools openssl rauc squashfs-tools
# x86_64 only:
sudo apt-get install -y qemu-user-static binfmt-support
sudo update-binfmts --enable qemu-aarch64
```

Build using your public certificate and a protected signing key:

```bash
(
  set -euo pipefail
  source_dir=$(mktemp -d "${TMPDIR:-/tmp}/network-exit-source.XXXXXXXX")
  trap 'rm -rf -- "$source_dir"' EXIT
  base=$(python3 -m image_builder.fetch_base --output-dir "$source_dir")
  sudo bash build.sh "$base" v0.1.0 /secure/ota-cert.pem
  sudo chown -R "$(id -u):$(id -g)" output
  python3 package-release.py --version v0.1.0 \
    --certificate /secure/ota-cert.pem --key /secure/ota-key.pem
)
```

The build plan lives in `image_builder/stages.json`: a plain JSON array of stage names, with no schema/version or `stages` wrapper. Order comes from this array; execution context, dependencies and service registrations stay in each stage's metadata. `image_builder/plan.sh` loads the array; `runtime.sh` retains shared state, logging, the build lock and mount/loop cleanup. Empty plans, duplicate names, invalid paths and missing/out-of-order dependencies are rejected. Inspect the complete plan without root or build tools:

```bash
bash build.sh --list-stages
python3 -m image_builder.stage_catalog assets
python3 -m image_builder.check_stages
```

Software assets are owned by stages instead of a global source rootfs directory:

```text
build.sh                    # Public build entrypoint
package-release.py          # Public factory/OTA packaging entrypoint
image_builder/
  stages.json               # Ordered JSON array of stage names
  build.sh                  # Build command implementation
  plan.sh                   # Plan loading and display
  runtime.sh                # Dispatch, journal and resource cleanup
  stage_catalog.py          # Dependency, component and layer validation
  fetch_base.py             # Fresh base-image download
  assemble_factory.py       # Shared-rootfs components and optional raw A/B assembly
  package_enodectl.py       # shiv assembly
  package_release.py        # Factory archive and signed OTA packaging
  check_stages.py           # Static source/config validation
stage/
  common.sh                 # Build-only shell helpers
  image-rootfs/
    stage.json              # host-shell: source, expand and mount the image
    install.sh              # Shared-shell build_stage entrypoint; no placeholders
  image-inputs/
    stage.json
    install.sh
    config/base-image.json  # Pinned download source/checksum owned by this stage
  image-bootchain/
    stage.json
    install.sh
    config/network-exit.cmd # U-Boot A/B selector source; compiled to /boot/boot.scr
  ota/
    config/update-source.json # OTA release source, installed by ota/install.sh
    ...
  tailscale/
    stage.json              # Dependencies, package names, service/view hooks
    install.sh              # Unified build installation entrypoint
    enodectl_component/     # Tailscale configuration and interaction code
    rootfs_layer/           # Files copied into this stage's image paths
  enodectl/
    stage.json
    install.sh              # Host-side shiv build
    build-requirements.txt  # Packaging tools; not installed on the node
    requirements.txt        # Present because this stage declares Textual
    enodectl_component/
      enodectl_core/        # Generic CLI dispatcher, registry and terminal widgets
    rootfs_layer/           # Secure launcher
```

Only `stage.json` and `install.sh` are required. `enodectl_component/`, `rootfs_layer/` and `requirements.txt` are discovered and processed only when present. Empty files/directories and `.gitkeep` placeholders are unnecessary. For example, `stage/cloudflared/` has no `requirements.txt`: it declares no additional Python dependency. A present layer is copied before its installer runs. Package installation, upstream APT source setup and service defaults belong to that stage's installer. Chroot installers use `stage/common.sh`; the enodectl installer runs in a builder subprocess. Image operations use `host-shell` so mounts, loop devices and cleanup flags stay in the shared runner. Stage declarations are validated before mounting an image: missing/out-of-order dependencies, duplicate stage IDs, duplicate CLI/private registrations and overlapping layer files are rejected. Layers cannot traverse symlinks in the target image.

Source settings for one stage belong in its optional `config/` directory. They are consumed explicitly by that stage or its build helper; this directory is not a second filesystem overlay. Only settings shared across stages belong in a top-level `config/`, which is currently unnecessary. Runtime device configuration and identities remain on STATE.

| Stage | Owned capability |
| --- | --- |
| base-system | Signed Debian package refresh and shared base utilities |
| service-manager | Service registry, service/config/status commands, first-boot orchestration and Textual dashboard |
| system-overlay | RAM OverlayFS policy and initramfs hook |
| ota | Signed RAUC updates, bootchain commands, trial records/deadlines and health confirmation |
| system-state | Persistent STATE preparation, account/state restore and card-capacity growth |
| system-access | pi/root account policy, OpenSSH public keys/CA, SSH TUI and automatic setup entrypoints |
| system-watchdog | PID 1 watchdog policy and watchdog status command |
| network | Initial-static/DHCP provisioning, wired mDNS, STATE-backed Wi-Fi, Netplan/networkd, network TUI, firewall, rollback and uplink refresh |
| tailscale | Official package source, mandatory service and official/Headscale setup |
| wireguard | WireGuard tools, tunnel generation, guard/NAT and configuration hooks |
| cloudflared | Official package source, Tunnel service and token configuration |
| singbox | Official package source, proxy service and configuration |
| ddns | ddclient installation, profile TUI, private credentials and timer updates |
| enodectl | Core application and shiv assembly of selected stages/dependencies |
| system-power | Reboot command, vendor frontend diversions and poweroff restrictions |
| system-verify | Checks for the selected modules and assembled initramfs/SSH policy |

The five system stages are separate as an explicit organization choice. Image operations are also stages: `image-inputs`, `image-workspace`, `image-rootfs`, `image-chroot`, `image-bootchain`, `image-finalize`, `image-factory`, `image-compress` and `image-manifests`. The U-Boot selector source belongs to `stage/image-bootchain/config/network-exit.cmd`; build mechanics, downloads, validation, raw partition assembly and packaging live in `image_builder/`. The root build and packaging entrypoints delegate there. Runtime OTA software is owned by the ota stage.

The default composition contains 25 stages, all under `stage/` and selected by the same list: nine image stages, 15 ARM64 chroot stages and one builder-process enodectl stage. Add/reorder IDs in `image_builder/stages.json` while satisfying dependencies and execution context. Removing optional capabilities from a new image removes their Python code, layer assets, service entries and checks. Removing a capability already configured on an existing STATE requires a separate state-migration review; it is not an automatic uninstall mechanism.

The shiv packager, `image_builder/package_enodectl.py`, combines core and selected stage Python sources, rejects duplicate Python assets and bundles their declared runtime dependencies, including Textual. Dependency wheels target the ARM64 image's Python version; incompatible/native host wheels are not copied into the device bundle. CPython, its standard library and OS executables remain image packages. Packaging tools live in a private build virtualenv. No pip/shiv installation or dependency download occurs on the deployed device.

UI and operations are separate files within each stage. For example, `stage/tailscale/enodectl_component/tailscale_view.py` declares Textual fields, while `tailscale_config.py` owns control-plane validation, persistence and authorization. Shared forms live in `stage/enodectl/enodectl_component/enodectl_core/textual_ui.py`; service state changes live in `stage/service-manager/enodectl_component/service_manager.py`, separately from `service_tui.py`. See [stage contract](stage/README.md) for registration details.

A local controller-only packaging check needs no root, image mount or card:

```bash
python3 -m venv /tmp/enodectl-tools
/tmp/enodectl-tools/bin/python -m pip install --no-cache-dir -r stage/enodectl/build-requirements.txt
/tmp/enodectl-tools/bin/python -m image_builder.package_enodectl --target /tmp/enodectl-review.pyz
python3 -I /tmp/enodectl-review.pyz --help
```

This checks the controller bundle; it does not produce a factory or OTA image. The default application includes Textual and its dependencies. Dependencies with native extensions require ARM64 runtime validation.

The public `/usr/local/bin/enodectl` entrypoint delegates to the installed `/usr/local/sbin/enodectl` launcher, which invokes `/usr/lib/enodectl/enodectl.pyz` with isolated Python and strips Python/shiv environment overrides. Root uses a root-owned `/run/enodectl/shiv` extraction cache; pi uses its own home cache. Shiv extracts already bundled files locally on first invocation; the root cache is recreated on boot. No credentials or device identity are stored there. Private systemd/vendor entrypoints delegate to the same archive. Shiv's no-modify check detects changed extracted Python files. Software configuration and identity still live on STATE.

`output/enodectl-manifest.json`, also included in the factory archive, records selected stages, layer ownership, the shiv version and bundled Python dependency versions. It complements the APT package manifest. The same manifest is installed beside the application. `enodectl status` prints compact bootchain (including current, confirmed and pending slots) and key-service tables using local state only. `enodectl config` shows the current bootchain at the top of the dashboard. `enodectl bootchain status` retains its detailed JSON output.

Progress shows the current stage and completion time. Each invocation retains `work/build.XXXXXXXX/logs/<stage>.log` and a `stages.tsv` journal with running/completed/failed events, elapsed seconds and exit codes. Actions saves these files as the `build-stage-logs` artifact even when the build fails. A failure stops subsequent stages and triggers common restoration, unmount and loop-detach cleanup. Cleanup errors make the build fail and report resources requiring inspection. Only the final stage moves completed files into `output/`, publishing the verified `output/factory/` component directory last.

Stages share a mounted working image and must run in order. Skipping stages and resuming a failed workspace are not supported. Review the failed log, resolve the cause and start a new build; retained directories are diagnostic copies. Do not delete a workspace until any reported mounts and loop devices have been released. Chroot stages must be invoked through the runner, which supplies their target context.

For offline base-image inspection, pass the already downloaded `.img.xz` directly to `build.sh`; software package installation still requires builder network access. Each Actions build downloads a fresh SHA256-verified base into runner temporary storage. It does not reuse a project or Actions cache. Software is fetched from official signed APT repositories, and installed packages receive available build-time upgrades. Running appliances do not execute unattended APT upgrades. Versions are recorded in `output/package-manifest.tsv`; builds at different times are not byte-reproducible.

`stage/image-inputs/config/base-image.json` pins Armbian community build `26.11.0-trunk.130`: Debian 13 Trixie ARM64, Orange Pi Zero 3, kernel `6.18.63-current-sunxi64`. Build `26.11.0-trunk.137` panicked in BL31 during PSCI initialization on a 1 GiB board, before the A/B selector could run. The November 12 boot firmware predates Armbian's H616 native PSCI suspend change ([upstream PR #11317](https://github.com/armbian/build/pull/11317)). On November 26, 2026, the original `trunk.130` image passed complete SD card write/readback verification and booted successfully on the affected board. The rebuilt factory image and A/B selector still require board testing. The factory assembler uses the prefix captured from the SHA256-verified base before package updates. Changing this base requires reviewing its SHA256, bootloader commands, kernel options, DTB and partition layout. Builds retain `work/build.*` for inspection and refuse to overwrite an existing final component directory. The factory layout expands to about 10 GiB on the card; the default builder compresses its rootfs only once and avoids assembling a duplicate-rootfs raw disk file. Allow substantial runner disk space for working copies and both products.

## Verification and remaining acceptance work

```bash
python3 -m venv /tmp/enodectl-test-tools
/tmp/enodectl-test-tools/bin/python -m pip install --no-cache-dir -r stage/enodectl/build-requirements.txt -r stage/enodectl/requirements.txt
ENODECTL_TEST_SHIV_PYTHON=/tmp/enodectl-test-tools/bin/python python3 -m unittest discover -s tests -v
python3 -m image_builder.check_stages
for script in build.sh image_builder/*.sh stage/*/install.sh stage/common.sh release/burn.sh; do
  bash -n "$script"
done
```

Tests validate stage order, dependencies, asset collisions, installer routing and a standalone shiv build containing Textual plus a fixture dependency. The bundled plugin runs after source deletion; omitted optional modules stay out and modified extraction-cache code is rejected. Textual Pilot tests exercise actual widgets and workers with fixture-only services: mandatory-service toggle guards, secret masking and retention, confirmed saves, cancellation, SSH key/CA edits, last-authorization protection, read-only system pages and network trial rollback. Backend tests cover repeatable configuration without key rotation, failed-apply completion invalidation, official/Headscale authorization, provider-switch confirmation and local readiness checks.

Integration checks cover DDNS's real upstream parser, real SSH certificate acceptance/rejection over local pipes, account and sudo policy parsers, interactive first-login/console bootstrap on private pseudo-terminals, kernel firewall/NAT behavior in disposable namespaces, capped RAM overlay behavior, real ext4/partition growth, signed RAUC bundle creation/verification, torn boot-record writes and factory archive extraction. They do not change host accounts/services/network, flash a real card or contact a DDNS provider. CI installs the required tools and requires all integration checks. Vendor-unit fixtures and headless UI tests do not validate the installed ARM64 environment.

The first complete ARM64 factory/OTA build passed in [this artifact-only validation run](https://github.com/aetherrootr/network-exit-pi-image/actions/runs/45062950931). All 25 stages, package checksums, the factory verification entrypoint and OTA signature verification passed. No Release was published by that run. Before deployment, complete signed OTA installation, board boot and first-login checks. Test A-to-B and B-to-A updates, health confirmation, three unconfirmed boots, kernel panic, watchdog reset, loss of power during inactive-slot and boot-record writes, STATE persistence, invalid signature/board rejection and actual rollback. Test offline boot followed by Ethernet connection, network reconnection, chosen optional services, real encrypted WireGuard traffic and the broadband public exit address. These hardware/OTA acceptance tests have not been completed locally.

References: [RAUC custom boot backend](https://rauc.readthedocs.io/en/latest/integration.html#custom), [RAUC bundle formats](https://rauc.readthedocs.io/en/latest/reference.html), [Debian overlayroot](https://sources.debian.org/src/cloud-initramfs-tools/0.18.debian14/overlayroot/scripts/init-bottom/overlayroot/), [sunxi watchdog](https://github.com/torvalds/linux/blob/v6.18/drivers/watchdog/sunxi_wdt.c), [WireGuard](https://www.wireguard.com/quickstart/), [Tailscale packages](https://pkgs.tailscale.com/stable/), [OpenSSH CA authentication](https://manpages.debian.org/trixie/openssh-server/sshd_config.5.en.html#TrustedUserCAKeys), [YubiKey SSH](https://developers.yubico.com/SSH/Securing_SSH_with_FIDO2.html), [Tailscale control-server option](https://tailscale.com/docs/reference/tailscale-cli/up), [Headscale client registration](https://docs.headscale.org/usage/getting-started/), [Cloudflare packages](https://pkg.cloudflare.com/), [sing-box packages](https://sing-box.sagernet.org/installation/package-manager/).
