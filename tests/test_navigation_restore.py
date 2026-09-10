from pathlib import Path
import ast


APP_SOURCE = Path(__file__).resolve().parents[1] / "app.py"


def _assignment_dict(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    raise AssertionError(f"{name} assignment not found")


def test_sidebar_page_mapping_matches_internal_stack_order():
    source = APP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    mapping = _assignment_dict(tree, "page_navigation_buttons")
    assert isinstance(mapping, ast.Dict)

    actual = {}
    for key, value in zip(mapping.keys, mapping.values):
        assert isinstance(key, ast.Constant)
        assert isinstance(value, ast.Name)
        actual[key.value] = value.id

    assert actual == {
        0: "Home_button",
        1: "pumps_button",
        2: "valves_button",
        3: "sensors_button",
        4: "camera_button",
        5: "Waveform_button",
        6: "analytics_button",
        7: "workspace_button",
    }


def test_programmatic_page_changes_drive_sidebar_selection():
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert "pages.currentChanged.connect(update_navigation_selection)" in source
    assert "navigation_buttons[page_index].setChecked(True)" not in source


def test_visible_sidebar_order_keeps_v61_order_with_analytics_inserted_after_camera():
    source = APP_SOURCE.read_text(encoding="utf-8")
    expected = [
        "sidebar_layout.addWidget(Home_button)",
        "sidebar_layout.addWidget(pumps_button)",
        "sidebar_layout.addWidget(sensors_button)",
        "sidebar_layout.addWidget(Waveform_button)",
        "sidebar_layout.addWidget(camera_button)",
        "sidebar_layout.addWidget(analytics_button)",
        "sidebar_layout.addWidget(valves_button)",
        "sidebar_layout.addWidget(workspace_button)",
    ]
    positions = [source.index(item) for item in expected]
    assert positions == sorted(positions)
