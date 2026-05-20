import hashlib
import tempfile
from pathlib import Path

# tests/test_vlm_downloader.py


def test_build_url_modelscope():
    from core.vlm.gguf_downloader import _build_url
    base = 'https://modelscope.cn/models/Qwen/Qwen2-VL-7B-Instruct-GGUF/resolve/master/'
    assert _build_url(base, 'model.gguf') == base + 'model.gguf'


def test_build_url_trailing_slash_stripped():
    from core.vlm.gguf_downloader import _build_url
    base = 'https://example.com/path/'
    assert _build_url(base, 'file.gguf') == 'https://example.com/path/file.gguf'


def test_verify_sha256_empty_skips():
    from core.vlm.gguf_downloader import verify_sha256
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
        f.write(b'test')
        tmp = Path(f.name)
    assert verify_sha256(str(tmp), '') is True
    tmp.unlink()


def test_verify_sha256_correct():
    from core.vlm.gguf_downloader import verify_sha256
    data = b'hello world'
    expected = hashlib.sha256(data).hexdigest()
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
        f.write(data)
        tmp = Path(f.name)
    assert verify_sha256(str(tmp), expected) is True
    tmp.unlink()


def test_verify_sha256_wrong():
    from core.vlm.gguf_downloader import verify_sha256
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
        f.write(b'hello world')
        tmp = Path(f.name)
    assert verify_sha256(str(tmp), 'deadbeef' * 8) is False
    tmp.unlink()
