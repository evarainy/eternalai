from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_expected_bootstrap_paths_exist() -> None:
    required_paths = [
        REPO_ROOT / "apps" / "web" / "README.md",
        REPO_ROOT / "apps" / "miniapp" / "README.md",
        REPO_ROOT / "services" / "api" / "app" / "main.py",
        REPO_ROOT / "services" / "worker" / "app" / "main.py",
        REPO_ROOT / "services" / "asr" / "app" / "main.py",
        REPO_ROOT / "contracts" / "README.md",
        REPO_ROOT / "docs" / "observability" / "README.md",
        REPO_ROOT / "planning" / "bootstrap_plan.md",
        REPO_ROOT / ".github" / "workflows" / "smoke.yml",
    ]

    missing = [str(path.relative_to(REPO_ROOT)) for path in required_paths if not path.exists()]
    assert missing == []


def test_nul_artifact_was_removed() -> None:
    entries = {path.name.lower() for path in REPO_ROOT.iterdir()}
    assert "nul" not in entries
