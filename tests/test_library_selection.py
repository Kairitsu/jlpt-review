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


def run_selection_dom(checked_states, action="updateLibrarySelectionButtons()"):
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    helper = "function librarySelectionState" + source.split(
        "function librarySelectionState", 1
    )[1].split("function openManageCollectionDialog", 1)[0]
    script = f"""
const rows = {json.dumps(checked_states)}.map(() => {{
  const classes = new Set();
  return {{
    classList: {{
      toggle(name, force) {{ force ? classes.add(name) : classes.delete(name); }},
      contains(name) {{ return classes.has(name); }},
    }},
    attrs: {{}},
    setAttribute(name, value) {{ this.attrs[name] = value; }},
  }};
}});
const checks = {json.dumps(checked_states)}.map((checked, index) => ({{
  checked,
  closest(selector) {{ return selector === '.library-row' ? rows[index] : null; }},
}}));
const selectedCountClasses = new Set(['hidden']);
const selectedCount = {{
  textContent: '',
  classList: {{
    toggle(name, force) {{ force ? selectedCountClasses.add(name) : selectedCountClasses.delete(name); }},
    contains(name) {{ return selectedCountClasses.has(name); }},
  }},
}};
const buttons = Object.fromEntries([
  '#select-all-btn', '#rechunk-selected-btn', '#move-selected-btn',
  '#batch-note-selected-btn', '#practice-selected-btn',
].map(id => [id, {{disabled: null, textContent: ''}}]));
function $$(selector) {{
  if (selector === '.sentence-check') return checks;
  if (selector === '.sentence-check:checked') return checks.filter(item => item.checked);
  throw new Error(`unexpected selector ${{selector}}`);
}}
function $(selector) {{
  if (selector === '#library-selected-count') return selectedCount;
  return buttons[selector] || null;
}}
{helper}
{action};
console.log(JSON.stringify({{
  checks: checks.map(item => item.checked),
  rows: rows.map(row => ({{
    selected: row.classList.contains('is-selected'),
    ariaSelected: row.attrs['aria-selected'],
  }})),
  selectedText: selectedCount.textContent,
  selectedHidden: selectedCount.classList.contains('hidden'),
  selectLabel: buttons['#select-all-btn'].textContent,
  disabled: Object.fromEntries(Object.entries(buttons).slice(1).map(([id, button]) => [id, button.disabled])),
}}));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
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


def test_selection_buttons_count_and_row_feedback_stay_in_sync():
    none = run_selection_dom([False, False, False])
    assert none["selectedHidden"] is True
    assert none["selectLabel"] == "全选"
    assert all(none["disabled"].values())
    assert none["rows"] == [
        {"selected": False, "ariaSelected": "false"},
        {"selected": False, "ariaSelected": "false"},
        {"selected": False, "ariaSelected": "false"},
    ]

    partial = run_selection_dom([True, False, True])
    assert partial["selectedText"] == " · 已选 2 条"
    assert partial["selectedHidden"] is False
    assert not any(partial["disabled"].values())
    assert partial["rows"] == [
        {"selected": True, "ariaSelected": "true"},
        {"selected": False, "ariaSelected": "false"},
        {"selected": True, "ariaSelected": "true"},
    ]

    selected_all = run_selection_dom(
        [False, False, False], "toggleVisibleSentenceSelection()"
    )
    assert selected_all["checks"] == [True, True, True]
    assert all(row["selected"] for row in selected_all["rows"])
    assert selected_all["selectedText"] == " · 已选 3 条"
    assert selected_all["selectLabel"] == "取消全选"
    assert not any(selected_all["disabled"].values())

    cleared_all = run_selection_dom(
        [True, True, True], "toggleVisibleSentenceSelection()"
    )
    assert cleared_all["checks"] == [False, False, False]
    assert not any(row["selected"] for row in cleared_all["rows"])
    assert cleared_all["selectedHidden"] is True
    assert cleared_all["selectLabel"] == "全选"
    assert all(cleared_all["disabled"].values())


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


def test_library_bulk_controls_dialog_sticky_and_mobile_contracts():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert 'id="select-all-btn"' in source
    assert 'id="rechunk-selected-btn" disabled' in source
    assert 'id="move-selected-btn" disabled' in source
    assert 'id="batch-note-selected-btn" disabled' in source
    assert 'id="practice-selected-btn" disabled' in source
    toolbar = source.split('class="library-bulk-actions"', 1)[1].split("</div></div>", 1)[0]
    assert toolbar.index("管理句集") < toolbar.index("全选") < toolbar.index("重新分块")
    assert toolbar.index("重新分块") < toolbar.index("转移选中句子")
    assert toolbar.index("转移选中句子") < toolbar.index("批量添加备注") < toolbar.index("专项练习")
    assert "updateLibrarySelectionButtons(); return;" in source
    assert "checks.forEach(syncLibraryRowSelection)" in source
    assert "row.classList.toggle('is-selected', selected)" in source
    assert "row.setAttribute('aria-selected', String(selected))" in source
    assert 'class="library-row" aria-selected="false"' in source
    assert "list.innerHTML = items.length ?" in source
    assert "updateLibrarySelectionButtons(); }" in source
    assert 'id="library-selected-count"' in source
    assert "将使用当前 GiNZA 分块规则重新生成所选 ${ids.length} 句的词块" in source
    assert "已有的人工拆分或合并结果会被覆盖" in source
    assert "正在重新分块…" in source
    assert "await reloadLibrary()" in source
    assert "if (errorEl) errorEl.textContent = error.message" in source
    assert "将为已选的 ${ids.length} 句添加同一条备注" in source
    assert 'maxlength="1000"' in source
    assert "正在添加…" in source
    assert "await api('/api/sentences/batch-note'" in source
    assert "toast(`已为 ${ids.length} 句添加备注`)" in source
    assert "window.scrollTo({top:scrollPosition" in source
    assert ".library-bulk-actions" in styles
    assert ".library-sticky-toolbar{position:sticky" in styles
    sticky_rule = styles.split(".library-sticky-toolbar{", 1)[1].split("}", 1)[0]
    assert "position:fixed" not in sticky_rule
    assert "z-index:30" in sticky_rule
    assert "backdrop-filter:blur(14px)" in sticky_rule
    assert ".library-row.is-selected{" in styles
    assert "box-shadow:inset 4px 0 0 var(--primary)" in styles
    mobile = styles.split("@media(max-width:900px){", 1)[1].split("@media(max-width:480px){", 1)[0]
    assert "overflow-x:auto" in mobile
    assert "flex:0 0 auto" in mobile
    assert "min-height:48px" in mobile
    assert "white-space:nowrap" in mobile
    assert ".rechunk-sentences-modal" in styles
    assert ".batch-note-modal" in styles
