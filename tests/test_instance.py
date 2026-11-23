from openwispr.instance import SessionInstanceGuard


def test_non_windows_instance_guard_is_a_noop():
    guard = SessionInstanceGuard(platform="linux")

    assert guard.acquire() is True
    assert guard.focus_existing() is False

    guard.release()
