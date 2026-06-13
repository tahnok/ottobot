import pytest

from helpers import ReplyRecorder
from ottawa_meshbot import MeshBot


@pytest.fixture
def bot() -> MeshBot:
    return MeshBot()


@pytest.fixture
def reply() -> ReplyRecorder:
    return ReplyRecorder()
