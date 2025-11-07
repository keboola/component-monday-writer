#!/bin/sh
set -e

pip install ruff pytest
ruff check .
pytest