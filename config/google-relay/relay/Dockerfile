# The Google relay (main.go): static, no shell, nothing else in the image.
FROM golang:1.26-alpine@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade AS build
WORKDIR /src
COPY go.mod *.go ./
RUN CGO_ENABLED=0 go vet ./... && CGO_ENABLED=0 go test -count=1 ./... \
 && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /relay .

FROM scratch
COPY --from=build /relay /relay
ENTRYPOINT ["/relay"]
