[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SmokeArgs
)

& uv run python -m scripts.smoke @SmokeArgs
exit $LASTEXITCODE
