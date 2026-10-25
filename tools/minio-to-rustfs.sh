#!/usr/bin/env bash
# Flytter filerne i observationer fra den gamle MinIO til RustFS. Køres én gang på serveren efter opdateringen:
#
#   tools/minio-to-rustfs.sh
#
# Den gamle MinIO startes med sine data (volumen minio), hvert objekt kopieres til RustFS via S3 (filer, der allerede
# findes med samme størrelse, springes over, så scriptet kan køres igen), og MinIO stoppes bagefter. Volumen minio
# slettes ikke – gør det selv, når billeder og lyd er tjekket i portalen:  docker volume rm doftool_minio
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose --profile minio-migrate up -d --wait minio
trap 'docker compose --profile minio-migrate stop minio >/dev/null' EXIT

docker compose exec -T api python - <<'PY'
import io, os
from minio import Minio
from minio.error import S3Error
from portal.config import get_settings

s = get_settings()
src = Minio("minio:9000", access_key=os.environ.get("MINIO_ROOT_USER") or "doftool",
            secret_key=os.environ["MINIO_ROOT_PASSWORD"], secure=False)
dst = Minio(s.s3_endpoint, access_key=s.s3_access_key, secret_key=s.s3_secret_key, secure=s.s3_secure,
            region=s.s3_region or None)
bucket = s.s3_bucket
if not src.bucket_exists(bucket):
    raise SystemExit(f"MinIO har ingen bucket {bucket} – intet at flytte")
if not dst.bucket_exists(bucket):
    dst.make_bucket(bucket)

copied = skipped = 0
for o in src.list_objects(bucket, recursive=True):
    try:
        if dst.stat_object(bucket, o.object_name).size == o.size:
            skipped += 1
            continue
    except S3Error:
        pass
    r = src.get_object(bucket, o.object_name)
    try:
        st = src.stat_object(bucket, o.object_name)
        data = r.read()
    finally:
        r.close(); r.release_conn()
    dst.put_object(bucket, o.object_name, io.BytesIO(data), len(data), content_type=st.content_type)
    copied += 1
print(f"Flyttet: {copied} filer, allerede i RustFS: {skipped}")
PY
