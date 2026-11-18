"""Entry point shared by every `waffle_py_test`: runs pytest on the given files."""

from __future__ import annotations

import os
import sys

import pytest


def main() -> int:
    args = ["-p", "no:cacheprovider", "--import-mode=importlib", "-q"]
    xml_output = os.environ.get("XML_OUTPUT_FILE")
    if xml_output:
        args.append(f"--junitxml={xml_output}")
    return int(pytest.main([*args, *sys.argv[1:]]))


if __name__ == "__main__":
    sys.exit(main())
