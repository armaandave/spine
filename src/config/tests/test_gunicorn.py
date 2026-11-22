import io
import os
import runpy
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock, skipIf

from django.test import SimpleTestCase

# gunicorn is not available on Windows
if sys.platform != "win32":
    from gunicorn.app.wsgiapp import run


@skipIf(sys.platform == "win32", "gunicorn is not fully supported on Windows")
class GunicornConfigTests(SimpleTestCase):
    """Test Gunicorn configuration."""

    config_path = Path(__file__).parents[1] / "gunicorn.py"

    def test_concurrency_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = runpy.run_path(self.config_path)

        self.assertEqual(config["worker_class"], "gthread")
        self.assertEqual(config["workers"], 2)
        self.assertEqual(config["threads"], 4)

    def test_concurrency_environment_overrides(self):
        with mock.patch.dict(
            os.environ,
            {"GUNICORN_WORKERS": "3", "GUNICORN_THREADS": "6"},
            clear=True,
        ):
            config = runpy.run_path(self.config_path)

        self.assertEqual(config["workers"], 3)
        self.assertEqual(config["threads"], 6)

    def test_legacy_web_concurrency_remains_a_fallback(self):
        with mock.patch.dict(os.environ, {"WEB_CONCURRENCY": "5"}, clear=True):
            config = runpy.run_path(self.config_path)

        self.assertEqual(config["workers"], 5)

    def test_invalid_concurrency_environment_fails(self):
        with (
            mock.patch.dict(os.environ, {"GUNICORN_WORKERS": "many"}, clear=True),
            self.assertRaises(ValueError),
        ):
            runpy.run_path(self.config_path)

    def test_preloaded_objects_are_frozen_before_each_fork(self):
        config = runpy.run_path(self.config_path)

        with mock.patch("gc.freeze") as freeze:
            config["pre_fork"](mock.Mock(), mock.Mock())

        freeze.assert_called_once_with()

    def test_print_config_reports_effective_concurrency(self):
        argv = [
            "gunicorn",
            "--print-config",
            "--config",
            "python:config.gunicorn",
            "config.wsgi",
        ]
        output = io.StringIO()

        with (
            mock.patch.object(sys, "argv", argv),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as cm,
        ):
            run()

        self.assertEqual(cm.exception.args[0], 0)
        self.assertIn("worker_class                       = gthread", output.getvalue())
        self.assertIn("workers                            = 2", output.getvalue())
        self.assertIn("threads                            = 4", output.getvalue())

    def test_config(self):
        """Test that the Gunicorn configuration file is valid."""
        argv = [
            "gunicorn",
            "--check-config",
            "--config",
            "python:config.gunicorn",
            "config.wsgi",
        ]
        mock_argv = mock.patch.object(sys, "argv", argv)

        with self.assertRaises(SystemExit) as cm, mock_argv:
            run()

        exit_code = cm.exception.args[0]
        self.assertEqual(exit_code, 0)
