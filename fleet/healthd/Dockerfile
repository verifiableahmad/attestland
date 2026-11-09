# healthd: single static binary on distroless.
FROM golang:1.24 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /healthd ./cmd/healthd

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /healthd /healthd
COPY rules.yaml /etc/healthd/rules.yaml
USER nonroot
ENTRYPOINT ["/healthd"]
