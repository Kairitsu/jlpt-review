import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def run_selection_helper(expression):
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    helper = "function librarySelectionState" + source.split(
        "function librarySelectionState", 1
    )[1].split("function selectedSentenceIds", 1)[0]
    result = subprocess.run(
        ["node", "-e", f"{helper}\nconsole.log(JSON.stringify({expression}));"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_select_all_cancel_all_and_partial_selection_states():
    empty = run_selection_helper("librarySelectionState([])")
    assert empty == {
        "total": 0, "selected": 0, "allSelected": False,
        "selectLabel": "全选", "selectDisabled": True,
        "selectionActionsDisabled": True,
    }

    none = run_selection_helper("librarySelectionState([false, false, false])")
    partial = run_selection_helper("librarySelectionState([true, false, true])")
    all_selected = run_selection_helper("librarySelectionState([true, true, true])")
    assert none["selectLabel"] == partial["selectLabel"] == "全选"
    assert none["selectionActionsDisabled"] is True
    assert partial["selectionActionsDisabled"] is False
    assert all_selected["selectLabel"] == "取消全选"
    assert run_selection_helper("nextLibrarySelection([false, true, false])") == [True, True, True]
    assert run_selection_helper("nextLibrarySelection([true, true, true])") == [False, False, False]


def test_select_all_operates_on_current_rendered_search_results_only():
    result = run_selection_helper(
        "({visible: nextLibrarySelection([false, false]), hidden: [true, false]})"
    )
    assert result == {"visible": [True, True], "hidden": [True, False]}

    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    toggle_flow = source.split("function toggleVisibleSentenceSelection", 1)[1].split(
        "function openManageCollectionDialog", 1
    )[0]
    assert "$$('.sentence-check')" in toggle_flow
    assert "renderLibraryRows((await api('/api/sentences?' + query)).sentences)" in source


def test_rechunk_controls_and_dialog_follow_existing_ui_flow():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert 'id="select-all-btn"' in source
    assert 'id="rechunk-selected-btn" disabled' in source
    assert 'id="move-selected-btn" disabled' in source
    assert "updateLibrarySelectionButtons(); return;" in source
    assert "将使用当前 GiNZA 分块规则重新生成所选 ${ids.length} 句的词块" in source
    assert "已有的人工拆分或合并结果会被覆盖" in source
    assert "正在重新分块…" in source
    assert "await reloadLibrary()" in source
    assert "if (errorEl) errorEl.textContent = error.message" in source
    assert ".library-bulk-actions" in styles
    assert ".rechunk-sentences-modal" in styles
