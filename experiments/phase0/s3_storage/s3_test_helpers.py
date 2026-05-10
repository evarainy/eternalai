"""
P0-SPIKE-006: S3-compatible test helper utilities.

Spike-only code for Phase 0. Not a production module.
Before production use, an independent task must review, harden, and relocate
these utilities (e.g. to tests/utils/ with proper dependency management).

Status: needs_test_environment
Status: needs_internal_mirror_confirmation (boto3 on internal PyPI)
"""

try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


def create_s3_client(
    endpoint_url: str = "http://localhost:9000",
    access_key: str = "<MINIO_ACCESS_KEY_PLACEHOLDER>",
    secret_key: str = "<MINIO_SECRET_KEY_PLACEHOLDER>",
    region: str = "us-east-1",
):
    """Factory: create a boto3 S3 client for MinIO or other S3-compatible store."""
    if not BOTO3_AVAILABLE:
        raise RuntimeError("boto3 is not installed. Mark as needs_internal_mirror_confirmation.")
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )


def ensure_bucket_exists(s3_client, bucket: str):
    """Create bucket if it does not exist; no-op if already present."""
    try:
        s3_client.head_bucket(Bucket=bucket)
    except ClientError:
        s3_client.create_bucket(Bucket=bucket)


def cleanup_bucket(s3_client, bucket: str):
    """Delete all objects in a bucket, then delete the bucket itself."""
    response = s3_client.list_objects_v2(Bucket=bucket)
    for obj in response.get("Contents", []):
        s3_client.delete_object(Bucket=bucket, Key=obj["Key"])
    s3_client.delete_bucket(Bucket=bucket)
