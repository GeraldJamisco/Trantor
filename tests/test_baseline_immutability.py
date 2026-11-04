"""The Phase E and Phase F artifacts are immutable research baselines."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PHASE_E = ROOT / "research_results" / "xauusd_p" / "phase_e_authoritative_20261102"
PHASE_F = ROOT / "research_results" / "xauusd_p" / "phase_f_diagnostics"

BASELINE_SHA256 = {
    PHASE_E / "decisions.jsonl": "0604640d471eb51520ccf0a3e0cc8c3d43e7a23d2c1150b6e25b0eece885b0e5",
    PHASE_E / "entry_attempts.json": "8add875162ce5a19940c60c5401555319363d56e2553a263c047038841f6c814",
    PHASE_E / "manifest.json": "7e6e40e30cf8b1005ae5cb56dfffea774a243c379bc9937fb8fccc1dbfb0eea4",
    PHASE_E / "summary.json": "8d8e1fc48fee84d8ad1cd605ea911f4fd156fbcb82ee04ff6087a58e376c9e74",
    PHASE_E / "trades.json": "6885715d81eac80e7cc2d9a9a092a5069cd917c2dc4e672b42ac6e19b5dc4acd",
    PHASE_F / "breakdowns.json": "221174d6ab5e19e4ba99ca1e2b1a219131ef18ef0be9fb645d6ff98b9b99069d",
    PHASE_F / "summary.json": "fc1a10e85ea69cba51d1cf22f15c7a7aefb94663fe2d5216d3d78878bafc7bbd",
    PHASE_F / "trade_diagnostics.csv": "60cddd88669658ad6901f5852ff751929fdaf42c13d8b892e762b1d7e173462d",
    PHASE_F / "trade_diagnostics.json": "b139cef8ca5784e5b0935a5d4f1d9b81f291b6f25c23731e6f8ec1e38f7c418c",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.parametrize("path", sorted(BASELINE_SHA256), ids=lambda p: p.name)
def test_baseline_artifact_is_unchanged(path: Path) -> None:
    if path.name == "decisions.jsonl" and path.stat().st_size < 1024:
        pytest.skip("decisions.jsonl is a Git LFS pointer; run `git lfs checkout`")
    assert _sha256(path) == BASELINE_SHA256[path]


def test_no_unexpected_files_in_baseline_directories() -> None:
    expected = {path.name for path in BASELINE_SHA256 if path.parent == PHASE_E}
    assert {path.name for path in PHASE_E.iterdir()} == expected
    expected_f = {path.name for path in BASELINE_SHA256 if path.parent == PHASE_F}
    assert {path.name for path in PHASE_F.iterdir()} == expected_f


PHASE_G = ROOT / "research_results" / "xauusd_p" / "phase_g_approved_utc_v1"
PHASE_G_SHA256 = {
    PHASE_G / "decisions.jsonl": "0b2f7109059219fa852ce4158532085b8a45abb23bded5ba53135b96e6adb8d1",
    PHASE_G / "entry_attempts.json": "fe484328d93d2dce3e84038d3002d217a329b4512ab8592a4c5438468bd4c236",
    PHASE_G / "experiment.json": "becae3c027f914a0ef91d4343a6a0de004e4e22b0f5a79e5a4c09bda9588ed05",
    PHASE_G / "manifest.json": "2ea3d920cd5114313ab01af44db96235e6e5071a5b93ad79ac67c19fc81c3b16",
    PHASE_G / "summary.json": "b72644719306391c1e18d43d1de13110842cce81f341a5a8402d0bc8c2505ad4",
    PHASE_G / "trades.json": "973d23d5a47d7c14621f4a4d238f47d7116f133403bf26d6a76fb4ea6afd64c3",
}


@pytest.mark.parametrize("path", sorted(PHASE_G_SHA256), ids=lambda p: p.name)
def test_phase_g_artifact_is_unchanged(path: Path) -> None:
    if not path.exists():
        pytest.skip("Phase G results not present")
    if path.name == "decisions.jsonl" and path.stat().st_size < 1024:
        pytest.skip("decisions.jsonl is a Git LFS pointer; run `git lfs checkout`")
    assert _sha256(path) == PHASE_G_SHA256[path]
