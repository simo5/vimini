import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch

# Ensure python3 root is in sys.path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), '..', 'python3')))

from vimini.common.util import (
    upgrade_project_data,
    load_project_data,
    save_project_data,
    create_default_project_data
)
from vimini.config import (
    prompt_reset_config_dialog,
    load_project_config_or_prompt
)


def test_upgrade_project_data_errors():
    with pytest.raises(ValueError, match="Invalid project configuration format"):
        upgrade_project_data("not-a-dict-or-list")

    with pytest.raises(ValueError, match="Invalid project configuration format"):
        upgrade_project_data(123)

    with pytest.raises(ValueError, match="higher than supported version"):
        upgrade_project_data({"version": "99.0", "configuration": {}})


def test_load_project_data_errors(tmp_path):
    with patch("vimini.common.util.PROJECTS_DIR", str(tmp_path / "projects")):
        # Case 1: Non-existent file returns default project data
        data = load_project_data(project_name="my_project")
        assert data["version"] == "0.1"
        assert "configuration" in data

        # Case 2: Malformed JSON raises JSONDecodeError / Exception
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(exist_ok=True)
        config_file = projects_dir / "my_project"
        config_file.write_text("{invalid json: broken", encoding="utf-8")

        with pytest.raises(Exception):
            load_project_data(project_name="my_project")

        # Case 3: Invalid data format (e.g. JSON number) raises ValueError
        config_file.write_text("42", encoding="utf-8")
        with pytest.raises(ValueError):
            load_project_data(project_name="my_project")

        # Case 4: Higher version raises ValueError
        config_file.write_text(json.dumps({"version": "999.0"}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_project_data(project_name="my_project")


def test_load_project_config_or_prompt(tmp_path):
    with patch("vimini.common.util.PROJECTS_DIR", str(tmp_path / "projects")):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(exist_ok=True)
        config_file = projects_dir / "test_proj"
        config_file.write_text("corrupted json {", encoding="utf-8")

        # User answers 'y' in dialog
        with patch("vimini.config.prompt_reset_config_dialog", return_value=True):
            data = load_project_config_or_prompt(project_name="test_proj", project_root=str(tmp_path))
            assert data["version"] == "0.1"
            # File on disk should have been reset to valid default data
            with open(config_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            assert saved["version"] == "0.1"
            assert "configuration" in saved

        # User answers 'n' in dialog
        config_file.write_text("corrupted json {", encoding="utf-8")
        with patch("vimini.config.prompt_reset_config_dialog", return_value=False):
            with pytest.raises(Exception):
                load_project_config_or_prompt(project_name="test_proj", project_root=str(tmp_path))
