#!/bin/bash
# The schlift.com VM's half of the Google relay. Run as root on the VM; `make setup-vm-tunnel` / `make setup-vm-route`
# from config/google-relay pipe it over SSH. Every step is idempotent.
#
#   setup.sh tunnel '<ssh-ed25519 public key>'
#       A user, harelay, whose only possible use is the house's tunnel: its key may ask sshd to listen on
#       127.0.0.1:47443 and nothing else (no shell, no command, no forwards out of the VM, no agent, no pty).
#   setup.sh route
#       Caddy with caddy-l4 (/usr/local/bin/caddy-l4, from /tmp/caddy-l4), and the global options that pass
#       hass-google.brensch.com's TLS through to that port. This is the step that makes the relay reachable.
set -euo pipefail

USER_NAME=harelay
LISTEN=127.0.0.1:47443
SSHD=/etc/ssh/sshd_config
CADDYFILE=/etc/caddy/Caddyfile
GLOBAL=/etc/caddy/google-relay.global

tunnel() {
	local key=$1
	[[ $key =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || { echo "need an ssh-ed25519 public key" >&2; exit 1; }
	if ! id "$USER_NAME" >/dev/null 2>&1; then
		useradd --create-home --shell /sbin/nologin --comment "Google relay tunnel (home repo config/google-relay)" "$USER_NAME"
	fi
	passwd -l "$USER_NAME" >/dev/null
	local home; home=$(getent passwd "$USER_NAME" | cut -d: -f6)
	install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$home/.ssh"
	# restrict turns everything off; port-forwarding turns forwarding back on, permitlisten narrows -R to one
	# loopback port, and sshd's Match block below refuses -L (PermitOpen none) whatever the key says.
	printf 'restrict,port-forwarding,permitlisten="%s",command="/sbin/nologin" %s\n' "$LISTEN" "$key" > "$home/.ssh/authorized_keys"
	chown "$USER_NAME:$USER_NAME" "$home/.ssh/authorized_keys"
	chmod 600 "$home/.ssh/authorized_keys"
	restorecon -R "$home/.ssh" 2>/dev/null || true

	# At the very end of sshd_config, so the Match block can't swallow anything after it.
	if ! grep -q '^# BEGIN google-relay' "$SSHD"; then
		cp -a "$SSHD" "$SSHD.before-google-relay"
		cat >> "$SSHD" <<EOF

# BEGIN google-relay (home repo config/google-relay/vm/setup.sh)
Match User $USER_NAME
	AuthenticationMethods publickey
	AllowTcpForwarding remote
	PermitListen $LISTEN
	PermitOpen none
	GatewayPorts no
	AllowAgentForwarding no
	AllowStreamLocalForwarding no
	X11Forwarding no
	PermitTunnel no
	PermitTTY no
	ForceCommand /sbin/nologin
	MaxSessions 1
	# A dead tunnel must let go of the port quickly, or the house's reconnect is refused.
	ClientAliveInterval 15
	ClientAliveCountMax 3
# END google-relay
EOF
	fi
	if ! sshd -t; then
		echo "sshd config invalid; restoring" >&2
		cp -a "$SSHD.before-google-relay" "$SSHD"
		exit 1
	fi
	systemctl reload sshd
	sshd -T -C "user=$USER_NAME,host=x,addr=192.0.2.1" | grep -Ei '^(allowtcpforwarding|permitlisten|permitopen|forcecommand|permittty|x11forwarding|allowagentforwarding|gatewayports) '
}

route() {
	[[ -x /tmp/caddy-l4 ]] || { echo "copy the ARM64 build to /tmp/caddy-l4 first (make vm-caddy)" >&2; exit 1; }
	/tmp/caddy-l4 list-modules | grep -qx 'layer4' || { echo "/tmp/caddy-l4 has no layer4 module" >&2; exit 1; }
	install -m 755 /tmp/caddy-l4 /usr/local/bin/caddy-l4
	chcon --reference=/usr/bin/caddy /usr/local/bin/caddy-l4
	install -m 644 /tmp/google-relay.global "$GLOBAL"
	restorecon "$GLOBAL" 2>/dev/null || true

	# The package's unit, pointed at this build (dnf can still update /usr/bin/caddy; it just isn't used).
	install -d /etc/systemd/system/caddy.service.d
	cat > /etc/systemd/system/caddy.service.d/google-relay.conf <<'EOF'
# Caddy with caddy-l4 for the Google relay (home repo config/google-relay). Remove this file to go back to the
# package's Caddy, after taking the import line out of /etc/caddy/Caddyfile.
[Service]
ExecStart=
ExecStart=/usr/local/bin/caddy-l4 run --environ --config /etc/caddy/Caddyfile
ExecReload=
ExecReload=/usr/local/bin/caddy-l4 reload --config /etc/caddy/Caddyfile --force
EOF
	systemctl daemon-reload

	# The import goes first thing in the global options block (the file's first line must be its opening brace).
	if ! grep -q "^\s*import $GLOBAL" "$CADDYFILE"; then
		cp -a "$CADDYFILE" "$CADDYFILE.before-google-relay"
		local first; first=$(grep -n -v -E '^\s*(#|$)' "$CADDYFILE" | head -1)
		[[ ${first#*:} == "{" ]] || { echo "$CADDYFILE doesn't start with a global options block" >&2; exit 1; }
		sed -i "${first%%:*}a\\    import $GLOBAL" "$CADDYFILE"
	fi
	if ! /usr/local/bin/caddy-l4 validate --config "$CADDYFILE" >/dev/null; then
		echo "Caddyfile invalid; restoring" >&2
		[[ -f $CADDYFILE.before-google-relay ]] && cp -a "$CADDYFILE.before-google-relay" "$CADDYFILE"
		exit 1
	fi
	# A restart, not a reload: the binary changed.
	systemctl restart caddy
	systemctl is-active caddy
}

case ${1:-} in
tunnel) tunnel "${2:?public key}" ;;
route) route ;;
*) echo "usage: setup.sh tunnel '<public key>' | route" >&2; exit 2 ;;
esac
