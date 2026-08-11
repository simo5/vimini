import os
import subprocess

def get_git_repo_root(start_dir=None):
    """
    Finds the root directory of the git repository starting from start_dir or current working directory.
    Does not require vim.
    """
    if start_dir is None:
        start_dir = os.getcwd()
    try:
        res = subprocess.run(
            ['git', '-C', start_dir, 'rev-parse', '--show-toplevel'],
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode == 0 and res.stdout.strip():
            return os.path.abspath(res.stdout.strip())
    except Exception:
        pass
    return None

def get_project_root(start_dir=None):
    """
    Returns the repository root path if inside a git repo, otherwise the absolute path of start_dir or cwd.
    """
    if start_dir is None:
        start_dir = os.getcwd()
    repo_root = get_git_repo_root(start_dir)
    if repo_root:
        return repo_root
    return os.path.abspath(start_dir)
