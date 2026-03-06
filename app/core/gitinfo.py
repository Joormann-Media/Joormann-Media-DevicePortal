from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_git(args: list[str], timeout: int = 4) -> tuple[int, str, str]:
    cmd = ["git", "-C", str(REPO_ROOT)] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:
        return 127, "", str(exc)


def get_update_info() -> dict:
    rc_branch, local_branch, err_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], timeout=3)
    if rc_branch != 0 or not local_branch:
        return {
            "available": False,
            "local_branch": "",
            "local_commit": "",
            "remote_commit": "",
            "error": err_branch or "git_branch_failed",
        }

    rc_local, local_commit, err_local = _run_git(["rev-parse", "HEAD"], timeout=3)
    if rc_local != 0 or not local_commit:
        return {
            "available": False,
            "local_branch": local_branch,
            "local_commit": "",
            "remote_commit": "",
            "error": err_local or "git_local_commit_failed",
        }

    # Query remote HEAD for the same branch without mutating local refs.
    rc_remote, remote_out, err_remote = _run_git(["ls-remote", "--heads", "origin", local_branch], timeout=8)
    remote_commit = ""
    if rc_remote == 0 and remote_out:
        remote_commit = remote_out.split()[0].strip()

    if not remote_commit:
        return {
            "available": False,
            "local_branch": local_branch,
            "local_commit": local_commit,
            "remote_commit": "",
            "error": err_remote or "git_remote_unavailable",
        }

    return {
        "available": remote_commit != local_commit,
        "local_branch": local_branch,
        "local_commit": local_commit,
        "remote_commit": remote_commit,
        "error": "",
    }
