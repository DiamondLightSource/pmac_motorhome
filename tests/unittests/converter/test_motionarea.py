import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from converter.motionarea import MotionArea


class TestMotionArea(unittest.TestCase):
    @patch("subprocess.check_output")
    def test_shebang_looks_as_expected(self, mock_subprocess):
        # Arrange
        python_path = "/bin/python"
        expected_shebang = "#!/bin/env /bin/python"
        mock_subprocess.return_value = python_path.encode("UTF-8")
        motionarea = MotionArea(Path("/tmp"))
        # Act
        content = motionarea.get_shebang()
        # Assert
        self.assertEqual(content, expected_shebang)
