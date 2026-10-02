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


def test_force_release_reports_status_instead_of_raising_on_contention():
    """Cleanup after a confirmed disconnect must never raise on a live channel.

    ``force_release`` deliberately bypasses token matching, so it cannot hit the
    stale-token branch of ``release``. It answers with a bool instead.
    """

    manager = ChannelOwnershipManager()
    owner = manager.claim(2, WAVEFORM, label="Mixing Slow")

    assert manager.force_release(2) is True
    assert manager.get(2) is None
    # Releasing an unowned channel is a no-op that reports False.
    assert manager.force_release(2) is False
    assert manager.claim(2, MANUAL, label="manual pump control").kind == MANUAL
    assert owner.kind == WAVEFORM


def test_force_release_all_succeeds_while_every_kind_is_owned():
    manager = ChannelOwnershipManager()
    manager.claim(1, MANUAL, label="manual pump control")
    manager.claim(6, WAVEFORM, label="Mixing Slow")
    assert not manager.all_free

    manager.force_release_all()

    assert manager.all_free
    assert manager.is_free(1) and manager.is_free(6)


def test_release_still_raises_for_a_stale_token():
    """Guard the type the service now converts into a result."""

    manager = ChannelOwnershipManager()
    manager.claim(3, WAVEFORM, label="Mixing Slow")

    with pytest.raises(ChannelOwnershipError, match="ownership changed"):
        manager.release(3, "stale-token")

    assert issubclass(ChannelOwnershipError, RuntimeError)
    assert manager.get(3).kind == WAVEFORM
