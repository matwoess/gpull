"""Test suite for gpull.py using temporary Git repositories with non-trivial commits."""

from pathlib import Path
import subprocess
import tempfile
import pytest

from gpull import find_git_repos, update_repo, generate_html_report


def run_git(cwd: Path, *args: str) -> str:
    """Helper to run git commands in a target directory."""
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


@pytest.fixture
def git_workspace(tmp_path: Path):
    """Fixture creating three non-trivial Git repository scenarios with local filesystem remotes."""
    env_opts = ["-c", "user.name=TestUser", "-c", "user.email=test@example.com", "-c", "init.defaultBranch=main"]

    # 1. Setup Up-To-Date Repo
    remote_a = tmp_path / "remote_a.git"
    run_git(tmp_path, *env_opts, "init", "--bare", str(remote_a))
    setup_a = tmp_path / "setup_a"
    run_git(tmp_path, *env_opts, "clone", str(remote_a), str(setup_a))
    (setup_a / "README.md").write_text("# Project A\nUp to date repo.")
    run_git(setup_a, *env_opts, "add", ".")
    run_git(setup_a, *env_opts, "commit", "-m", "Initial commit")
    run_git(setup_a, *env_opts, "push", "-u", "origin", "main")

    repo_a = tmp_path / "repo_uptodate"
    run_git(tmp_path, *env_opts, "clone", str(remote_a), str(repo_a))

    # 2. Setup Repo with Non-Trivial Incoming Updates
    remote_b = tmp_path / "remote_b.git"
    run_git(tmp_path, *env_opts, "init", "--bare", str(remote_b))

    work_b = tmp_path / "work_b"
    run_git(tmp_path, *env_opts, "clone", str(remote_b), str(work_b))
    (work_b / "app.py").write_text("def main():\n    print('v1')\n")
    run_git(work_b, *env_opts, "add", ".")
    run_git(work_b, *env_opts, "commit", "-m", "Initial version")
    run_git(work_b, *env_opts, "push", "-u", "origin", "main")

    # Clone to target local repo (will track origin/main automatically)
    repo_b = tmp_path / "repo_updated"
    run_git(tmp_path, *env_opts, "clone", str(remote_b), str(repo_b))

    # Push non-trivial commits to remote_b from work_b
    (work_b / "app.py").write_text("def main():\n    print('v2 updated')\n    return 42\n")
    (work_b / "utils.py").write_text("# Utility module\ndef helper():\n    pass\n")
    run_git(work_b, *env_opts, "add", ".")
    run_git(work_b, *env_opts, "commit", "-m", "feat: add utils and update app logic")

    (work_b / "config.json").write_text('{\n  "version": "2.0.0"\n}\n')
    run_git(work_b, *env_opts, "add", ".")
    run_git(work_b, *env_opts, "commit", "-m", "chore: add configuration file")
    run_git(work_b, *env_opts, "push", "origin", "main")

    # 3. Setup Repo with Merge Conflict
    remote_c = tmp_path / "remote_c.git"
    run_git(tmp_path, *env_opts, "init", "--bare", str(remote_c))

    work_c = tmp_path / "work_c"
    run_git(tmp_path, *env_opts, "clone", str(remote_c), str(work_c))
    (work_c / "conflict.txt").write_text("Original line 1\nOriginal line 2\n")
    run_git(work_c, *env_opts, "add", ".")
    run_git(work_c, *env_opts, "commit", "-m", "Initial conflict base")
    run_git(work_c, *env_opts, "push", "-u", "origin", "main")

    repo_c = tmp_path / "repo_conflict"
    run_git(tmp_path, *env_opts, "clone", str(remote_c), str(repo_c))

    # Remote changes
    (work_c / "conflict.txt").write_text("Remote edit line 1\nOriginal line 2\n")
    run_git(work_c, *env_opts, "add", ".")
    run_git(work_c, *env_opts, "commit", "-m", "Remote change")
    run_git(work_c, *env_opts, "push", "origin", "main")

    # Local conflicting changes
    (repo_c / "conflict.txt").write_text("Local conflicting edit line 1\nOriginal line 2\n")
    run_git(repo_c, *env_opts, "add", ".")
    run_git(repo_c, *env_opts, "commit", "-m", "Local change")

    # 4. Setup Local Repo without Remotes
    repo_d = tmp_path / "repo_noremote"
    run_git(tmp_path, *env_opts, "init", str(repo_d))
    (repo_d / "local.txt").write_text("Local only file")
    run_git(repo_d, *env_opts, "add", ".")
    run_git(repo_d, *env_opts, "commit", "-m", "Local commit")

    return {
        "root": tmp_path,
        "repo_uptodate": repo_a,
        "repo_updated": repo_b,
        "repo_conflict": repo_c,
        "repo_noremote": repo_d,
    }


def test_find_git_repos(git_workspace):
    """Test recursive discovery of git repositories."""
    repos = find_git_repos(git_workspace["root"])
    repo_names = {r.name for r in repos}
    assert {"repo_uptodate", "repo_updated", "repo_conflict", "repo_noremote"}.issubset(repo_names)


def test_update_repo_uptodate(git_workspace):
    """Test git pull on an up-to-date repo."""
    res = update_repo(git_workspace["repo_uptodate"])
    assert res["status"] == "up_to_date"
    assert "Already up to date" in res["summary"]


def test_update_repo_updated(git_workspace):
    """Test git pull on a repo with non-trivial incoming commits."""
    res = update_repo(git_workspace["repo_updated"])
    assert res["status"] == "updated"
    assert "files changed" in res["summary"] or "file changed" in res["summary"]
    output = res["output"]
    assert "$ git log --oneline HEAD.." in output
    assert "$ git pull --stat" in output
    assert "$ git diff" not in output
    # Verify order
    assert output.find("$ git log") < output.find("$ git pull")


def test_update_repo_noremote(git_workspace):
    """Test git pull on a repo without remote configured."""
    res = update_repo(git_workspace["repo_noremote"])
    assert res["status"] == "no_remote"
    assert "No remote" in res["summary"]


def test_update_repo_conflict(git_workspace):
    """Test git pull on a repo with merge conflict."""
    res = update_repo(git_workspace["repo_conflict"])
    assert res["status"] == "failed"
    assert "error" in res["output"].lower() or "conflict" in res["output"].lower()


def test_generate_html_report(git_workspace):
    """Test static HTML dashboard generation and keep the output report."""
    results = [
        update_repo(git_workspace["repo_uptodate"]),
        update_repo(git_workspace["repo_updated"]),
        update_repo(git_workspace["repo_noremote"]),
        update_repo(git_workspace["repo_conflict"]),
    ]
    report_file = Path(__file__).parent / "sample_report.html"
    generate_html_report(results, git_workspace["root"], report_file)

    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "Git Update Dashboard" in content
    assert "repo_uptodate" in content
    assert "repo_updated" in content
    assert "repo_noremote" in content
    assert "repo_conflict" in content
    assert "badge-up_to_date" in content
    assert "badge-updated" in content
    assert "badge-no_remote" in content
    assert "badge-failed" in content
    assert "closeDetails" in content
    assert "scrollToTop" in content
    assert "back-to-top" in content
    assert "fold-strip" in content
