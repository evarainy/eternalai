# experiments/phase0/s3_storage/

P0-SPIKE-006 spike code for S3-compatible object storage candidate evaluation.

## Status

- **scope**: spike-only
- **needs_test_environment**: no running MinIO / S3-compatible instance available for real execution
- **needs_internal_mirror_confirmation**: boto3 availability on internal PyPI mirror unconfirmed

## Files

| File | Purpose |
|------|---------|
| `minio_put_get_delete_test.py` | Minimum put/get/delete test against MinIO via boto3 |
| `s3_test_helpers.py` | Reusable S3 client factory and bucket lifecycle helpers |

## Spike Code Disposition

This code is spike-only. Before any production use:

1. An independent task must review and harden the code.
2. Relocate reusable parts to `tests/utils/` or appropriate production path.
3. Ensure boto3 is confirmed available in internal PyPI mirror.
4. Ensure a MinIO or S3-compatible instance is available in test/dev environment.

## How to Run (requires MinIO Docker instance)

```bash
# Start MinIO (example)
docker run -d -p 9000:9000 -p 9001:9001 minio/minio server /data --console-address ":9001"

# Run spike test
python experiments/phase0/s3_storage/minio_put_get_delete_test.py
```

## License Note

MinIO Community Server is evaluated under GNU AGPLv3 (not Apache 2.0).
Phase 1 adoption requires legal/compliance review confirming AGPLv3 acceptability
or evaluation of commercial AIStor license. See ADR-P0-SPIKE-006 for details.
