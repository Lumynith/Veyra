import json

from core.settings_manager import SettingsManager
from tests.conftest import DEFAULTS


def test_defaults_and_override_saved_alone(tmp_path):
    user = tmp_path / "settings.json"
    s = SettingsManager(DEFAULTS, user)
    assert s.get("viewport.width") == 841
    assert s.get("missing.key", 5) == 5
    s.set("viewport.width", 900)
    s.save()
    assert json.loads(user.read_text()) == {"viewport": {"width": 900}}
    again = SettingsManager(DEFAULTS, user)
    assert again.get("viewport.width") == 900 and again.get("viewport.height") == 661


def test_corrupt_user_file_is_backed_up(tmp_path):
    user = tmp_path / "settings.json"
    user.write_text("{not json")
    s = SettingsManager(DEFAULTS, user)
    assert s.get("viewport.width") == 841
    assert not user.exists()
    assert (tmp_path / "settings.corrupt.json").read_text() == "{not json"
