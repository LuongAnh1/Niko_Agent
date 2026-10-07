import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niko.config import (
    RUNTIME_CONFIG_ENV,
    env_value_with_source,
    load_env_file,
    reset_runtime_config,
    runtime_subprocess_env,
    update_runtime_config,
)


class RuntimeConfigTests(unittest.TestCase):
    def test_runtime_config_overrides_file_env_but_not_os_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            env_path = Path(temp_dir) / ".env"
            key = "NIKO_TEST_RUNTIME_VALUE"
            env_path.write_text(f"{key}=FromEnv\n", encoding="utf-8")

            with patch.dict(os.environ, {RUNTIME_CONFIG_ENV: str(config_path)}, clear=False):
                os.environ.pop(key, None)
                load_env_file(env_path)
                update_runtime_config({key: "FromRuntime"})

                value, source, effective_key = env_value_with_source(key, "Default")

                self.assertEqual(value, "FromRuntime")
                self.assertEqual(source, "runtime")
                self.assertEqual(effective_key, key)

                os.environ[key] = "FromOS"
                value, source, effective_key = env_value_with_source(key, "Default")

                self.assertEqual(value, "FromOS")
                self.assertEqual(source, "os")
                self.assertEqual(effective_key, key)

    def test_runtime_config_reset_removes_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            key = "NIKO_TEST_MEMORY_TOP_K"

            with patch.dict(os.environ, {RUNTIME_CONFIG_ENV: str(config_path)}, clear=False):
                os.environ.pop(key, None)
                update_runtime_config({key: 7})
                reset_runtime_config([key])

                value, source, _ = env_value_with_source(key, "4")

                self.assertEqual(value, "4")
                self.assertEqual(source, "default")

    def test_runtime_subprocess_env_drops_file_env_but_keeps_config_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "TELEGRAM_BOT_TOKEN=from-file\n"
                f"{RUNTIME_CONFIG_ENV}={Path(temp_dir) / 'config.json'}\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                load_env_file(env_path)
                child_env = runtime_subprocess_env()

            self.assertNotIn("TELEGRAM_BOT_TOKEN", child_env)
            self.assertIn(RUNTIME_CONFIG_ENV, child_env)


if __name__ == "__main__":
    unittest.main()
