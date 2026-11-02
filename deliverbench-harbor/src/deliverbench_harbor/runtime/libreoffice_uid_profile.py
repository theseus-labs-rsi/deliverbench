#!/usr/bin/env python3
"""Give isolated Judge UIDs a usable LibreOffice profile; preserve CLI arguments."""
import os
from pathlib import Path
import sys
import tempfile

args = sys.argv[1:]
if not any(arg.startswith('-env:UserInstallation=') for arg in args):
    # Reserve a private parent and let LibreOffice create the profile itself.
    profile = Path(tempfile.mkdtemp(prefix='libreoffice-', dir=os.environ.get('TMPDIR'))) / 'profile'
    args = ['-env:UserInstallation=' + profile.as_uri(), *args]
os.execv('/usr/bin/libreoffice', ['/usr/bin/libreoffice', *args])
