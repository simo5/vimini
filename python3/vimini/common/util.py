# Vimini Agent Package
# Common utility module for vimini without vim dependency.
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
            return os.path.realpath(res.stdout.strip())
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
    return os.path.realpath(start_dir)

def get_relative_path(file_path, repo_name=None, git_root=None):
    """
    Computes a path for a file relative to its git repository root,
    or to the user's home directory as a fallback.
    Prepends the capitalized git repo name or 'HOME' to the path.
    """
    if not file_path:
        return ""

    abs_path = os.path.realpath(file_path)

    if git_root and abs_path.startswith(git_root):
        relative_path = os.path.relpath(abs_path, git_root)
        if not repo_name:
            repo_name = os.path.basename(git_root)
        if repo_name:
            return f"{repo_name.upper()}:{relative_path}"
        return relative_path

    home_dir = os.path.expanduser('~')
    # Check if the path is inside the home directory.
    if abs_path.startswith(home_dir):
        try:
            relative_path = os.path.relpath(abs_path, home_dir)
            return f"HOME:{relative_path}"
        except ValueError:
            # This can happen on Windows if home_dir and abs_path are on different drives,
            # even with startswith check if symlinks are involved. Fallback is safe.
            pass

    # Fallback for files not in git repo or home, or on different drives on Windows.
    return os.path.basename(abs_path)
