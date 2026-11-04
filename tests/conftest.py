import os
import sys
import tempfile

# keep tests away from the real ~/.cbbf data folder
os.environ["HOME"] = tempfile.mkdtemp(prefix="cbbf-test-home-")
os.environ["USERPROFILE"] = os.environ["HOME"]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
