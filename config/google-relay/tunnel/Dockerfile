# The house's end of the SSH tunnel to the schlift.com VM: OpenSSH's client and nothing else. It dials out, and
# asks the VM to listen on its own loopback (127.0.0.1:47443) and send what arrives there down the tunnel to the
# relay. It accepts no forwards the other way and trusts one host key, pinned here.
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8
RUN apk add --no-cache openssh-client-default \
 && addgroup -g 1000 tunnel && adduser -D -H -u 1000 -G tunnel -h /tmp -s /sbin/nologin tunnel
COPY known_hosts /etc/google-tunnel/known_hosts
USER tunnel
ENV HOME=/tmp
# -F /dev/null: no config file can add anything. ExitOnForwardFailure: no tunnel, no process (Docker restarts it).
ENTRYPOINT ["ssh", "-F", "/dev/null", "-N", "-T", \
  "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", \
  "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=15", \
  "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=/etc/google-tunnel/known_hosts", \
  "-o", "GlobalKnownHostsFile=/dev/null", "-o", "UpdateHostKeys=no", "-o", "HostKeyAlgorithms=ssh-ed25519", \
  "-o", "IdentitiesOnly=yes", "-o", "IdentityFile=/run/tunnel_key", "-o", "IdentityAgent=none", \
  "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "PermitLocalCommand=no", "-o", "Tunnel=no", \
  "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "LogLevel=ERROR", \
  "-R", "127.0.0.1:47443:google-relay:8443", \
  "harelay@146.235.203.8"]
