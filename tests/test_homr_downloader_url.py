# tests/test_homr_downloader_url.py — regression coverage for P1-5 in the
# fix-plan doc.
#
# _build_url_for is the *only* correct URL builder for the flat
# <app_base_dir>/models/ weight layout this project uses. The upstream
# homr/main.py:_download_from_any_source has its own ModelScope URL logic
# that derives the subdir from the dest path's filesystem layout — that
# assumes the legacy submodule tree and silently builds wrong URLs against
# the flat layout. That distinction was previously enforced only by a
# docstring/comment, not a test.
import pytest

try:
    from core.omr.homr_downloader import _WEIGHT_BASE_URLS, _WEIGHT_FILES, _build_url_for
except ModuleNotFoundError:
    # homr_downloader imports homr.main via the omr_engine/homr submodule's
    # sys.path trick; CI's test job deliberately skips submodule checkout
    # (see ci.yml), so this whole module has nothing real to test there.
    pytest.skip('omr_engine/homr submodule not checked out', allow_module_level=True)

_MODELSCOPE_BASE = _WEIGHT_BASE_URLS[0]
_GITHUB_BASE = _WEIGHT_BASE_URLS[1]

# Real canonical filenames, one of each kind actually shipped (core/config.py
# / homr/main.py's _WEIGHT_FILES), rather than synthetic names — so this test
# breaks if the real naming convention ever drifts, not just some fake string.
_SEGNET_FILE = next(f for f in _WEIGHT_FILES if f.startswith('segnet_'))
_ENCODER_FILE = next(f for f in _WEIGHT_FILES if f.startswith('encoder_'))
_DECODER_FILE = next(f for f in _WEIGHT_FILES if f.startswith('decoder_'))


class TestModelScopeUrls:
    def test_segnet_goes_to_segmentation_subdir(self):
        url = _build_url_for(_MODELSCOPE_BASE, _SEGNET_FILE)
        assert url == f'{_MODELSCOPE_BASE.rstrip("/")}/segmentation/{_SEGNET_FILE}'

    def test_encoder_goes_to_transformer_subdir(self):
        url = _build_url_for(_MODELSCOPE_BASE, _ENCODER_FILE)
        assert url == f'{_MODELSCOPE_BASE.rstrip("/")}/transformer/{_ENCODER_FILE}'

    def test_decoder_goes_to_transformer_subdir(self):
        url = _build_url_for(_MODELSCOPE_BASE, _DECODER_FILE)
        assert url == f'{_MODELSCOPE_BASE.rstrip("/")}/transformer/{_DECODER_FILE}'

    def test_unrecognised_filename_prefix_raises(self):
        # A typo'd or future weight name that doesn't match any known prefix
        # must fail loudly, not silently build a wrong (or root-level) URL.
        import pytest
        with pytest.raises(ValueError, match='Unknown filename pattern'):
            _build_url_for(_MODELSCOPE_BASE, 'mystery_weight.onnx')


class TestGitHubUrls:
    def test_onnx_file_becomes_zip_attachment(self):
        url = _build_url_for(_GITHUB_BASE, _SEGNET_FILE)
        expected_basename = _SEGNET_FILE[: -len('.onnx')]
        assert url == f'{_GITHUB_BASE.rstrip("/")}/{expected_basename}.zip'

    def test_non_onnx_filename_raises(self):
        import pytest
        with pytest.raises(ValueError, match='Unknown filename pattern'):
            _build_url_for(_GITHUB_BASE, 'segnet_weights.bin')


class TestBaseUrlTrailingSlash:
    def test_trailing_slash_on_base_produces_identical_url(self):
        # _WEIGHT_BASE_URLS entries are defined with a trailing slash (see
        # homr/main.py); a caller passing the same value without one must
        # still produce the exact same URL, not a double/missing slash.
        with_slash = _MODELSCOPE_BASE if _MODELSCOPE_BASE.endswith('/') else _MODELSCOPE_BASE + '/'
        without_slash = _MODELSCOPE_BASE.rstrip('/')
        assert _build_url_for(with_slash, _SEGNET_FILE) == _build_url_for(without_slash, _SEGNET_FILE)


class TestAllCanonicalWeightsResolve:
    """Every filename homr/main.py actually ships must resolve on both
    mirrors without raising — a change to _WEIGHT_FILES' naming convention
    that _build_url_for's prefix matching doesn't account for would only
    show up here, not in the three targeted cases above."""

    def test_every_weight_resolves_on_modelscope(self):
        for filename in _WEIGHT_FILES:
            url = _build_url_for(_MODELSCOPE_BASE, filename)
            assert url.startswith(_MODELSCOPE_BASE.rstrip('/') + '/')
            assert url.endswith(filename)

    def test_every_weight_resolves_on_github(self):
        for filename in _WEIGHT_FILES:
            url = _build_url_for(_GITHUB_BASE, filename)
            assert url.startswith(_GITHUB_BASE.rstrip('/') + '/')
            assert url.endswith('.zip')
