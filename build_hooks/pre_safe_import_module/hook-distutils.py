# Workaround: Python 3.14 removed distutils from stdlib entirely.
# PyInstaller's built-in hook-distutils.py calls add_alias_module() which
# raises ValueError because modulegraph already marked distutils as
# ExcludedModule before the alias is registered.  Guard with try/except.
from PyInstaller import compat
from PyInstaller.utils.hooks.setuptools import setuptools_info


def pre_safe_import_module(api):
    if compat.is_py312 and setuptools_info.distutils_vendored:
        for aliased_name, real_vendored_name in setuptools_info.get_distutils_aliases():
            try:
                api.add_alias_module(real_vendored_name, aliased_name)
            except ValueError:
                # Python 3.14+: distutils already marked ExcludedModule;
                # skip gracefully.
                pass
