from backend import camera_service


def test_validated_exposure_range_is_one_point_two_five_percent():
    assert camera_service.EFFECTIVE_EXPOSURE_FRACTION == 0.0125
    assert camera_service.EFFECTIVE_EXPOSURE_MAX_RAW == 522
    assert camera_service.practical_exposure_max(1, 41771) == 522


def test_exposure_slider_uses_quadratic_low_end_control():
    # Preserve the validated raw ceiling but spread the sensitive low end over
    # much more of the UI slider. These values correspond to the user's real
    # 1280x1024 screenshots where the old linear 1..4% range was already huge.
    assert camera_service.EXPOSURE_SLIDER_GAMMA == 2.0
    assert camera_service.exposure_raw_from_percent(0, 1, 41771) == 1
    assert camera_service.exposure_raw_from_percent(1, 1, 41771) == 1
    assert camera_service.exposure_raw_from_percent(2, 1, 41771) == 1
    assert camera_service.exposure_raw_from_percent(3, 1, 41771) == 1
    assert camera_service.exposure_raw_from_percent(4, 1, 41771) == 2
    assert camera_service.exposure_raw_from_percent(10, 1, 41771) == 6
    assert camera_service.exposure_raw_from_percent(20, 1, 41771) == 22
    assert camera_service.exposure_raw_from_percent(50, 1, 41771) == 131
    assert camera_service.exposure_raw_from_percent(100, 1, 41771) == 522


def test_exposure_readback_inverse_matches_slider_curve():
    for percent in (0, 2, 4, 10, 20, 50, 75, 100):
        raw = camera_service.exposure_raw_from_percent(percent, 1, 41771)
        recovered = camera_service.exposure_percent_from_raw(raw, 1, 41771)
        # Integer DNX64 raw quantization is strongest at the very bottom.
        tolerance = 2.1 if percent <= 2 else (1.5 if percent <= 4 else 0.5)
        assert abs(recovered - percent) <= tolerance
