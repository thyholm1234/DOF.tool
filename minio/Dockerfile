# MinIO bygget fra kildekoden: projektet udgiver ikke længere færdige images, og repoet blev arkiveret i 2026.
# RELEASE.2025-10-15T17-29-55Z er den sidste udgave (AGPL-3.0). Den får ingen sikkerhedsopdateringer – appen taler
# almindelig S3, så en anden S3-tjeneste kan tage over ved at ændre S3_* i .env.
FROM golang:1.24.8-alpine AS build
ARG MINIO_RELEASE=RELEASE.2025-10-15T17-29-55Z
RUN apk add --no-cache git
RUN git clone --depth 1 --branch "$MINIO_RELEASE" https://github.com/minio/minio /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X github.com/minio/minio/cmd.ReleaseTag=$MINIO_RELEASE -X github.com/minio/minio/cmd.Version=2025-10-15T17:29:55Z" -o /minio .

FROM alpine:3.21.3
RUN apk add --no-cache ca-certificates && adduser -D -H -u 1000 minio && mkdir /data && chown minio /data
COPY --from=build /minio /usr/bin/minio
USER minio
VOLUME /data
EXPOSE 9000
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--address", ":9000", "--console-address", ":9001"]
