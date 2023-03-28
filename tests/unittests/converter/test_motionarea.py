import unittest
from pathlib import Path
from unittest.mock import patch

from converter.motionarea import MotionArea
from pmac_motorhome._version_git import __version__


class TestMotionArea(unittest.TestCase):
    def test_shebang_looks_as_expected(self):
        # Arrange
        expected_shebang = "#!/bin/env /dls_sw/prod/python3/RHEL7-x86_64/pmac_motorhome/"+__version__+"/lightweight-venv/bin/python3"
        motionarea = MotionArea(Path("/tmp"))
        # Act
        content = motionarea.get_shebang()
        # Assert
        self.assertEqual(content, expected_shebang)
