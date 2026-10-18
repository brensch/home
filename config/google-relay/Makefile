# The Google relay (README.md). The house's half is in ../../docker-compose.yaml (google-relay, google-tunnel).
VM       := opc@146.235.203.8
KEY      := $(HOME)/.config/google-relay/tunnel_key
RANGES   := ../../data/google-relay/google-ranges.txt

.PHONY: key setup-vm-tunnel vm-caddy setup-vm-route host ranges

# The tunnel's SSH key. Stays on this machine; the VM gets the public half.
key: $(KEY)
$(KEY):
	install -d -m 700 $(dir $(KEY))
	ssh-keygen -q -t ed25519 -N '' -C google-tunnel@snek3 -f $(KEY)

setup-vm-tunnel: $(KEY)
	ssh $(VM) 'sudo bash -s -- tunnel "$(shell cat $(KEY).pub)"' < vm/setup.sh

vm-caddy:
	docker build --output type=local,dest=vm/out vm

# Makes hass-google.brensch.com reachable through the VM (once its DNS record points there).
setup-vm-route: vm-caddy
	scp vm/out/caddy $(VM):/tmp/caddy-l4
	scp vm/google-relay.global $(VM):/tmp/google-relay.global
	ssh $(VM) 'sudo bash -s -- route' < vm/setup.sh
	ssh $(VM) 'rm -f /tmp/caddy-l4 /tmp/google-relay.global'

# This machine: the tunnel container's network may reach only the VM's SSH port, and none of the relay's networks
# may reach the host.
host:
	sudo install -m 755 host/google-tunnel-egress /usr/local/sbin/google-tunnel-egress
	sudo install -m 644 host/google-tunnel-egress.service /etc/systemd/system/google-tunnel-egress.service
	sudo systemctl daemon-reload
	sudo systemctl enable --now google-tunnel-egress.service
	sudo systemctl restart google-tunnel-egress.service
	for br in br-google-egr br-google-tun br-google-ha; do \
		sudo ufw status | grep -q "Anywhere on $$br.*DENY" || sudo ufw insert 1 deny in on $$br; \
	done

ranges:
	install -d $(dir $(RANGES))
	python3 google-ranges.py > $(RANGES).new && mv $(RANGES).new $(RANGES)
