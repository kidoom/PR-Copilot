import pytest
from backend.domain.pr_context.scorer import (
    compute_priority_score,
    compute_path_risk_score,
    compute_change_size_score,
    compute_file_status_score,
    compute_language_risk_score,
)
from backend.domain.pr_context.classifier import Classification


def _make_classification(**overrides) -> Classification:
    defaults = dict(
        language="python", language_family="backend", rule_profile="python",
        is_test=False, is_docs=False, is_config=False, is_source=True,
        is_generated=False, is_high_risk_path=False, risk_hints=[],
    )
    defaults.update(overrides)
    return Classification(**defaults)


class TestComputePathRiskScore:
    def test_auth_path(self):
        score = compute_path_risk_score("src/auth/login.py")
        assert score == 90

    def test_payment_path(self):
        score = compute_path_risk_score("billing/stripe.py")
        assert score == 90

    def test_normal_source(self):
        score = compute_path_risk_score("src/utils.py")
        assert score == 40

    def test_docs_path(self):
        score = compute_path_risk_score("docs/README.md")
        assert score == 10

    def test_test_path(self):
        score = compute_path_risk_score("tests/test_main.py")
        assert score == 15


class TestComputeChangeSizeScore:
    def test_small_change(self):
        assert compute_change_size_score(5, 3) == 1

    def test_medium_change(self):
        assert compute_change_size_score(100, 50) == 30

    def test_large_change(self):
        assert compute_change_size_score(500, 500) == 100

    def test_capped_at_100(self):
        assert compute_change_size_score(1000, 1000) == 100


class TestComputeFileStatusScore:
    def test_added(self):
        assert compute_file_status_score("added") == 80

    def test_modified(self):
        assert compute_file_status_score("modified") == 40

    def test_unknown(self):
        assert compute_file_status_score("unknown") == 40


class TestComputeLanguageRiskScore:
    def test_backend(self):
        assert compute_language_risk_score("backend") == 70

    def test_frontend(self):
        assert compute_language_risk_score("frontend") == 50

    def test_docs(self):
        assert compute_language_risk_score("docs") == 10


class TestComputePriorityScore:
    def test_high_risk_source(self):
        c = _make_classification(is_high_risk_path=True, risk_hints=["auth_path"])
        score = compute_priority_score(c, 100, 50, "modified", filename="src/auth/login.py")
        assert score >= 60

    def test_docs_low_score(self):
        c = _make_classification(is_docs=True, language_family="docs")
        score = compute_priority_score(c, 10, 5, "modified", filename="docs/README.md")
        assert score <= 20

    def test_test_file_low_score(self):
        c = _make_classification(is_test=True)
        score = compute_priority_score(c, 50, 30, "modified", filename="tests/test_main.py")
        assert score <= 40

    def test_score_bounds(self):
        c = _make_classification()
        score = compute_priority_score(c, 0, 0, "modified")
        assert 0 <= score <= 100
