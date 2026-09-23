import pytest

from medaccess import melodi


@pytest.fixture(autouse=True)
def _bpe_neuve():
    """Le fichier BPE national est mis en cache par exécution : on le vide entre tests."""
    melodi._bpe_nationale.cache_clear()
    yield
    melodi._bpe_nationale.cache_clear()
