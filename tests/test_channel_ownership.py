import pytest

from backend.channel_ownership import (
    ChannelOwnershipError,
    ChannelOwnershipManager,
    MANUAL,
    WAVEFORM,
)


def test_claim_blocks_second_owner_but_other_channels_remain_free():
    manager = ChannelOwnershipManager()
    owner = manager.claim(2, WAVEFORM, label="Mixing Slow")
    assert manager.get(2) == owner
    assert manager.is_free(1)
    with pytest.raises(ChannelOwnershipError, match="Mixing Slow"):
        manager.claim(2, MANUAL, label="manual pump")


def test_only_matching_token_can_release_channel():
    manager = ChannelOwnershipManager()
    owner = manager.claim(3, MANUAL, label="manual pump")
    with pytest.raises(ChannelOwnershipError):
        manager.release(3, "stale-token")
    assert manager.get(3) == owner
    assert manager.release(3, owner.token) is True
    assert manager.is_free(3)


def test_force_release_all_is_reserved_for_confirmed_disconnect_cleanup():
    manager = ChannelOwnershipManager()
    manager.claim(1, MANUAL)
    manager.claim(4, WAVEFORM)
    manager.force_release_all()
    assert manager.all_free
