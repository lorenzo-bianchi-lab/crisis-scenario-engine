"""Shared fixtures."""

import pytest

from app import store, ui


@pytest.fixture(autouse=True)
def exercises_in_a_temporary_folder(tmp_path, monkeypatch):
    """Live exercises started by a test are saved under tmp_path, never in the project."""
    monkeypatch.setattr(store, "FOLDER", tmp_path / "exercises")


@pytest.fixture(autouse=True)
def app_opens_on_the_design_page(monkeypatch):
    """The app opens on its home page; the tests written before it existed open the app and
    expect the Design page. tests/test_demo_ready.py sets the landing page back."""
    monkeypatch.setattr(ui, "LANDING", "design")
