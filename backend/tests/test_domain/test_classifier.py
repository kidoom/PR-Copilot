import pytest
from backend.domain.pr_context.classifier import (
    identify_language,
    identify_file_type,
    derive_risk_hints,
    classify_file,
)
from backend.domain.pr_context.fetcher import ChangedFile


def _make_file(filename: str, status: str = "modified") -> ChangedFile:
    return ChangedFile(
        filename=filename, previous_filename=None, status=status,
        additions=10, deletions=5, changes=15,
        blob_url="", raw_url="", contents_url="", patch=None,
    )


class TestIdentifyLanguage:
    def test_python(self):
        lang, family, profile = identify_language("src/main.py")
        assert lang == "python"
        assert family == "backend"

    def test_typescript(self):
        lang, family, _ = identify_language("app/index.tsx")
        assert lang == "typescript"
        assert family == "frontend"

    def test_yaml(self):
        lang, family, _ = identify_language("config.yaml")
        assert lang == "yaml"
        assert family == "config"

    def test_markdown(self):
        lang, family, _ = identify_language("README.md")
        assert lang == "markdown"
        assert family == "docs"

    def test_unknown_extension(self):
        lang, family, _ = identify_language("file.xyz")
        assert lang == "unknown"

    def test_dockerfile(self):
        lang, family, _ = identify_language("Dockerfile")
        assert lang == "dockerfile"


class TestIdentifyFileType:
    def test_test_file(self):
        is_test, is_docs, is_config, is_source, is_gen = identify_file_type("test_main.py")
        assert is_test is True
        assert is_source is False

    def test_docs_file(self):
        is_test, is_docs, is_config, is_source, is_gen = identify_file_type("README.md")
        assert is_docs is True

    def test_config_file(self):
        is_test, is_docs, is_config, is_source, is_gen = identify_file_type("config.yaml")
        assert is_config is True

    def test_source_file(self):
        is_test, is_docs, is_config, is_source, is_gen = identify_file_type("src/main.py")
        assert is_source is True
        assert is_test is False

    def test_spec_file(self):
        is_test, _, _, _, _ = identify_file_type("app.test.ts")
        assert is_test is True


class TestDeriveRiskHints:
    def test_auth_path(self):
        is_high, hints = derive_risk_hints("src/auth/login.py")
        assert is_high is True
        assert "auth_path" in hints

    def test_payment_path(self):
        is_high, hints = derive_risk_hints("billing/stripe.py")
        assert is_high is True
        assert "payment_path" in hints

    def test_normal_path(self):
        is_high, hints = derive_risk_hints("src/utils.py")
        assert is_high is False
        assert hints == []

    def test_db_path(self):
        is_high, hints = derive_risk_hints("db/migration/001.py")
        assert is_high is True
        assert "db_path" in hints


class TestClassifyFile:
    def test_full_classification(self):
        file = _make_file("src/auth/login.py")
        result = classify_file(file, {"src/auth/login.py"})
        assert result.language == "python"
        assert result.is_source is True
        assert result.is_high_risk_path is True
        assert "auth_path" in result.risk_hints

    def test_no_test_pair(self):
        file = _make_file("src/main.py")
        result = classify_file(file, {"src/main.py"})
        assert "no_test_pair" in result.risk_hints

    def test_has_test_pair(self):
        file = _make_file("src/main.py")
        result = classify_file(file, {"src/main.py", "src/test_main.py"})
        assert "no_test_pair" not in result.risk_hints
