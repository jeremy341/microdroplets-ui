from pathlib import Path
import ast


APP_SOURCE = Path(__file__).resolve().parents[1] / "app.py"


def _assignment(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    raise AssertionError(f"{name} assignment not found")


def test_shift_number_shortcuts_map_to_requested_pages():
    source = APP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    mapping = _assignment(tree, "navigation_shortcut_targets")

    assert isinstance(mapping, ast.Dict)

    actual = {}
    for key, value in zip(mapping.keys, mapping.values):
        assert isinstance(key, ast.Constant)
        assert isinstance(value, ast.Constant)
        actual[key.value] = value.value

    assert actual == {
        "Shift+1": 0,
        "Shift+2": 1,
        "Shift+3": 3,
        "Shift+4": 5,
        "Shift+5": 4,
        "Shift+6": 6,
        "Shift+7": 2,
    }


def test_ctrl_tab_uses_visible_sidebar_order():
    source = APP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    order = _assignment(tree, "navigation_page_order")

    assert isinstance(order, ast.Tuple)
    actual = tuple(item.value for item in order.elts)
    assert actual == (0, 1, 3, 5, 4, 6, 2, 7)


def test_next_and_previous_shortcuts_are_registered():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert 'QKeySequence("Ctrl+Tab")' in source
    assert 'lambda: navigate_relative_page(1)' in source
    assert 'QKeySequence("Ctrl+Shift+Tab")' in source
    assert 'lambda: navigate_relative_page(-1)' in source


def test_existing_sidebar_navigation_is_unchanged():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "lambda: pages.setCurrentIndex(0)" in source
    assert "lambda: pages.setCurrentIndex(1)" in source
    assert "lambda: pages.setCurrentIndex(2)" in source
    assert "lambda: pages.setCurrentIndex(3)" in source
    assert "lambda: pages.setCurrentIndex(4)" in source
    assert "lambda: pages.setCurrentIndex(5)" in source
    assert "lambda: pages.setCurrentIndex(6)" in source
