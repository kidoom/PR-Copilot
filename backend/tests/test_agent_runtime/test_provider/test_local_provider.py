from __future__ import annotations

import json
import os
import tempfile

import pytest

from backend.agent.tools.repo_context.provider.local import LocalRepoProvider


@pytest.fixture
def temp_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "src"))
        os.makedirs(os.path.join(tmpdir, "tests"))
        os.makedirs(os.path.join(tmpdir, ".git"))

        with open(os.path.join(tmpdir, "src", "main.py"), "w") as f:
            f.write("def main():\n    print('hello')\n    return True\n")

        with open(os.path.join(tmpdir, "src", "utils.py"), "w") as f:
            f.write("def helper():\n    return 42\n")

        with open(os.path.join(tmpdir, "README.md"), "w") as f:
            f.write("# Test Project\n")

        with open(os.path.join(tmpdir, "package.json"), "w") as f:
            f.write('{"name": "test"}')

        with open(os.path.join(tmpdir, ".env"), "w") as f:
            f.write("SECRET=abc123")

        with open(os.path.join(tmpdir, "private_key.pem"), "w") as f:
            f.write("key-content")

        with open(os.path.join(tmpdir, "tests", "test_main.py"), "w") as f:
            f.write("def test_main():\n    assert True\n")

        yield tmpdir


class TestLocalRepoProviderReadFile:
    @pytest.mark.asyncio
    async def test_read_file(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.read_file("src/main.py")
        assert result.error is None
        assert len(result.lines) == 3
        assert result.lines[0]["content"] == "def main():"

    @pytest.mark.asyncio
    async def test_read_file_with_line_range(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.read_file("src/main.py", start_line=2, end_line=2)
        assert result.error is None
        assert len(result.lines) == 1
        assert result.lines[0]["line"] == 2

    @pytest.mark.asyncio
    async def test_read_file_truncated_by_lines(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.read_file("src/main.py", start_line=1, end_line=2)
        assert result.error is None
        assert len(result.lines) == 2
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_read_file_truncated_by_bytes(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.read_file("src/main.py", max_bytes=10)
        assert result.error is None
        assert len(result.lines) < 3
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.read_file("nonexistent.py")
        assert result.error == "File not found"

    @pytest.mark.asyncio
    async def test_read_file_path_traversal(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.read_file("../../../etc/passwd")
        assert result.error == "Path traversal rejected"

    @pytest.mark.asyncio
    async def test_read_file_ignored_directory(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.read_file(".git/config")
        assert result.error == "Path in ignored directory"

    @pytest.mark.asyncio
    async def test_read_file_sensitive(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.read_file(".env")
        assert result.error == "Sensitive file blocked"

        result = await provider.read_file("private_key.pem")
        assert result.error == "Sensitive file blocked"


class TestLocalRepoProviderSearchCode:
    @pytest.mark.asyncio
    async def test_search_found(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.search_code("def main")
        assert len(result.matches) > 0
        assert any("main.py" in m.file for m in result.matches)

    @pytest.mark.asyncio
    async def test_search_not_found(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.search_code("nonexistent_xyz")
        assert len(result.matches) == 0
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_search_with_globs(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.search_code("def", globs=["*.py"])
        assert all(m.file.endswith(".py") for m in result.matches)

    @pytest.mark.asyncio
    async def test_search_respects_max_results(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.search_code("def", max_results=1)
        assert len(result.matches) <= 1

    @pytest.mark.asyncio
    async def test_search_skips_sensitive_files(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.search_code("SECRET")
        env_matches = [m for m in result.matches if ".env" in m.file]
        assert len(env_matches) == 0


class TestLocalRepoProviderListFiles:
    @pytest.mark.asyncio
    async def test_list_all_files(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.list_files()
        paths = [e.path.replace(os.sep, "/") for e in result.entries]
        assert "src/main.py" in paths
        assert "README.md" in paths

    @pytest.mark.asyncio
    async def test_list_with_globs(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.list_files(globs=["*.py"])
        assert all(e.path.endswith(".py") for e in result.entries)

    @pytest.mark.asyncio
    async def test_list_respects_max_results(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.list_files(max_results=2)
        assert len(result.entries) <= 2

    @pytest.mark.asyncio
    async def test_list_skips_git_and_sensitive(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.list_files()
        paths = [e.path.replace(os.sep, "/") for e in result.entries]
        assert not any(".git" in p for p in paths)
        assert ".env" not in paths


class TestLocalRepoProviderGetManifest:
    @pytest.mark.asyncio
    async def test_get_manifest(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.get_manifest()
        assert "readme" in result.manifests
        assert "dependencies" in result.manifests

    @pytest.mark.asyncio
    async def test_manifest_reads_content(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = await provider.get_manifest()
        readme = result.manifests["readme"][0]
        assert readme.content is not None
        assert "Test Project" in readme.content


class TestLocalRepoProviderVerify:
    def test_verify_valid_repo(self, temp_repo):
        provider = LocalRepoProvider(temp_repo)
        result = provider.verify()
        assert result["verified"] is True

    def test_verify_invalid_path(self):
        provider = LocalRepoProvider("/nonexistent/path")
        result = provider.verify()
        assert result["verified"] is False

    def test_verify_no_git_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalRepoProvider(tmpdir)
            result = provider.verify()
            assert result["verified"] is False
            assert "Not a git" in result["reason"]
