# Caddy for the schlift.com VM (ARM64) with caddy-l4, whose listener wrapper passes the Google relay's TLS through
# unopened. Same Caddy as the house's (config/caddy), cross-compiled; `make vm-caddy` copies the binary out.
FROM caddy:2.11.4-builder-alpine@sha256:0aa610043dab5da82ad0a0268e46bb852785e6f5160f12f1c6fe3f42903d7e1b AS builder
RUN CGO_ENABLED=0 GOOS=linux GOARCH=arm64 xcaddy build v2.11.4 --with github.com/mholt/caddy-l4@v0.1.2 --output /caddy

FROM scratch
COPY --from=builder /caddy /caddy
