# testing GitHub Actions
from gns3_proxy import HttpParser


def test_answer():
    assert HttpParser.build_header(b'key', b'value') == b'key: value'
