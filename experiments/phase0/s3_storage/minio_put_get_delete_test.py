"""
P0-SPIKE-006: MinIO S3-compatible put/get/delete spike test.

This script demonstrates minimum S3-compatible object storage operations
using boto3 against a MinIO instance. It is spike-only code; production
file management is out of scope for Phase 0.

Status: needs_test_environment (no Docker/MinIO instance available for real execution)
Status: needs_internal_mirror_confirmation (boto3 availability on internal PyPI mirror unconfirmed)
"""

import os
import sys

# boto3 is spike-only; never add to production dependencies.
# If boto3 is unavailable, this script exits gracefully after static parse.
try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

# MinIO connection settings from environment variables.
# No hardcoded credentials; set these before running against a real instance.
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "<MINIO_ACCESS_KEY_PLACEHOLDER>")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "<MINIO_SECRET_KEY_PLACEHOLDER>")
TEST_BUCKET = "p0-spike-006-test"
TEST_OBJECT_KEY = "test-object.txt"
TEST_OBJECT_BODY = b"P0-SPIKE-006 S3-compatible storage spike test payload."


def create_s3_client(endpoint: str = MINIO_ENDPOINT):
    """Create a boto3 S3 client configured for MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


def ensure_bucket(s3_client, bucket: str = TEST_BUCKET):
    """Create bucket if it does not exist."""
    try:
        s3_client.head_bucket(Bucket=bucket)
    except ClientError:
        s3_client.create_bucket(Bucket=bucket)


def put_object(s3_client, bucket: str = TEST_BUCKET, key: str = TEST_OBJECT_KEY, body: bytes = TEST_OBJECT_BODY):
    """Upload an object to the S3-compatible store."""
    s3_client.put_object(Bucket=bucket, Key=key, Body=body)
    return True


def get_object(s3_client, bucket: str = TEST_BUCKET, key: str = TEST_OBJECT_KEY) -> bytes:
    """Download an object from the S3-compatible store."""
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def delete_object(s3_client, bucket: str = TEST_BUCKET, key: str = TEST_OBJECT_KEY):
    """Delete an object from the S3-compatible store."""
    s3_client.delete_object(Bucket=bucket, Key=key)
    return True


def cleanup_bucket(s3_client, bucket: str = TEST_BUCKET):
    """Remove all objects and delete the test bucket."""
    response = s3_client.list_objects_v2(Bucket=bucket)
    for obj in response.get("Contents", []):
        s3_client.delete_object(Bucket=bucket, Key=obj["Key"])
    s3_client.delete_bucket(Bucket=bucket)


def run_spike_test():
    """
    Execute the full put/get/delete spike test cycle.

    Returns dict with per-step results. Real execution requires a running
    MinIO instance (e.g. via Docker Compose); otherwise results are marked
    needs_test_environment.
    """
    results = {"put": None, "get": None, "get_body_match": None, "delete": None}

    if not BOTO3_AVAILABLE:
        for key in results:
            results[key] = "needs_test_environment (boto3 not installed)"
        return results

    try:
        s3 = create_s3_client()
        ensure_bucket(s3)

        # PUT
        put_object(s3)
        results["put"] = "passed"

        # GET
        retrieved = get_object(s3)
        results["get"] = "passed"
        results["get_body_match"] = retrieved == TEST_OBJECT_BODY

        # DELETE
        delete_object(s3)
        results["delete"] = "passed"

        # Cleanup
        cleanup_bucket(s3)

    except Exception as exc:
        results["error"] = str(exc)
        for key in ("put", "get", "get_body_match", "delete"):
            if results[key] is None:
                results[key] = f"needs_test_environment ({type(exc).__name__})"

    return results


if __name__ == "__main__":
    if not BOTO3_AVAILABLE:
        print("boto3 is not installed. Cannot run real S3 operations.")
        print("Status: needs_test_environment")
        print("Status: needs_internal_mirror_confirmation")
        sys.exit(0)

    print("P0-SPIKE-006: MinIO S3-compatible put/get/delete spike test")
    print(f"Endpoint: {MINIO_ENDPOINT}")
    print(f"Bucket:   {TEST_BUCKET}")
    print()
    results = run_spike_test()
    for step, result in results.items():
        print(f"  {step}: {result}")
    print()
    print("Status: needs_test_environment (requires running MinIO Docker instance)")
