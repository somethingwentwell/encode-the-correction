#!/bin/sh
# Generic CI step for any runner that has a POSIX shell, Python 3.8+ and git.
# Run it from the root of a project that has adopted encode-the-correction:
#
#   sh ci.sh
#
# It installs the CLI into a throwaway virtual environment outside the checkout
# (so the check does not scan it, and a system-managed Python is left alone),
# then runs the check. The script exits with the check's status, so the job
# fails on 1 (a correction is violated) and on 2 (configuration error).
#
# ETC_SOURCE overrides what pip installs, e.g. to pin a commit:
#   ETC_SOURCE=git+https://github.com/somethingwentwell/encode-the-correction.git@<sha>
set -eu

source=${ETC_SOURCE:-git+https://github.com/somethingwentwell/encode-the-correction.git}
venv=$(mktemp -d)/venv

python3 -m venv "$venv"
"$venv/bin/python" -m pip install --quiet --disable-pip-version-check "$source"
"$venv/bin/encode-the-correction" check "$@"
