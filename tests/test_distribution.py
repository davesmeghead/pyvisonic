"""Build the sdist, build its wheel, and exercise the installed release offline."""

from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def run(*args, cwd):
    result = subprocess.run(
        [sys.executable, *args], cwd=cwd, text=True,
        capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope='module')
def installed_release(tmp_path_factory):
    work = tmp_path_factory.mktemp('release')
    dist = work / 'dist'
    run('-m', 'build', '--sdist', '--no-isolation', '--outdir', str(dist), str(ROOT), cwd=work)
    sdist, = dist.glob('*.tar.gz')
    with tarfile.open(sdist) as archive:
        assert any(name.endswith('/tests/test_checksums.py') for name in archive.getnames())
        archive.extractall(work / 'source', filter='data')
    source, = (work / 'source').iterdir()
    run('-m', 'build', '--wheel', '--no-isolation', '--outdir', str(dist), str(source), cwd=work)
    wheel, = dist.glob('*.whl')
    run('-m', 'twine', 'check', '--strict', str(sdist), str(wheel), cwd=work)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert 'pyvisonic/py_visonic.py' in names
        assert 'pyvisonic/examples/example_common.py' in names
        assert 'pyvisonic/examples/requirements.txt' in names
        assert not any(name.startswith(('tests/', 'examples/')) for name in names)
        assert not any(name.endswith('.pyc') for name in names)
    target = work / 'installed'
    run('-m', 'pip', 'install', '--no-index', '--no-deps', '--target', str(target), str(wheel), cwd=work)
    return work, target


def test_installed_library_imports(installed_release):
    work, target = installed_release
    run('-I', '-c', f'''
import importlib, importlib.metadata, pathlib, pkgutil, sys
sys.path.insert(0, {str(target)!r})
import pyvisonic
assert pathlib.Path(pyvisonic.__file__).is_relative_to({str(target)!r})
assert importlib.metadata.version('pyvisonic') == {VERSION!r}
for module in pkgutil.iter_modules(pyvisonic.__path__):
    if not module.ispkg:
        importlib.import_module('pyvisonic.' + module.name)
from pyvisonic.py_visonic import VisonicProtocol
from pyvisonic.py_abstract_classes import AlPanelInterface
from pyvisonic.py_enum import AlPanelCommand
''', cwd=work)


@pytest.mark.parametrize('example', ['simple_example', 'complete_example', 'bridge'])
def test_installed_example_help(installed_release, example):
    work, target = installed_release
    output = run('-I', '-c', f'''
import runpy, sys
sys.path.insert(0, {str(target)!r})
sys.argv = [{example!r}, '--help']
runpy.run_module('pyvisonic.examples.' + {example!r}, run_name='__main__')
''', cwd=work)
    assert 'usage:' in output.lower()
