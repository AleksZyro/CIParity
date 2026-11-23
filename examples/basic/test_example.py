from pathlib import Path


def test_project_contains_requirements():
    assert Path(__file__).with_name("requirements.txt").is_file()
