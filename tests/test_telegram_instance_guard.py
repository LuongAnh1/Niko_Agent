import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.telegram.instance_guard import (
    TelegramBotAlreadyRunning,
    acquire_telegram_bot_instance,
    read_active_instance,
    telegram_bot_lock_path,
)


class TelegramInstanceGuardTests(unittest.TestCase):
    def test_acquire_blocks_second_instance_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"NIKO_STATE_DIR": temp_dir},
            clear=False,
        ):
            lock = acquire_telegram_bot_instance()
            try:
                active = read_active_instance()
                self.assertIsNotNone(active)
                self.assertEqual(active.pid, os.getpid())

                with self.assertRaises(TelegramBotAlreadyRunning):
                    acquire_telegram_bot_instance()
            finally:
                lock.release()

            self.assertIsNone(read_active_instance())

    def test_stale_lock_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"NIKO_STATE_DIR": temp_dir},
            clear=False,
        ):
            path = telegram_bot_lock_path()
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            path.write_text(
                '{"pid": -1, "command": "stale", "lock_path": "%s"}\n' % str(path).replace("\\", "\\\\"),
                encoding="utf-8",
            )

            self.assertIsNone(read_active_instance(cleanup_stale=True))
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

