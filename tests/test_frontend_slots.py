from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_practice_frontend_renders_one_slot_per_structure_slot_and_never_a_fixed_candidate():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "const structure = (s.practiceStructure || []).map" in source
    assert "const index = slotIndex++" in source
    assert 'class="answer-slot empty"' in source
    assert 'class="fixed-element"' in source
    assert "shuffle(sentence.chunks.map(chunk => chunk.id))" in source
    assert "item.selected = []" in source
    assert "practiceStructure:state.draft.practiceStructure" in source


def test_slot_css_wraps_without_horizontal_overflow_and_fixed_text_is_not_interactive():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".preview-structure,.answer-sequence{display:flex;flex-wrap:wrap" in styles
    assert ".fixed-element{" in styles
    fixed_rule = styles.split(".fixed-element{", 1)[1].split("}", 1)[0]
    assert "user-select:none" in fixed_rule
    assert "pointer-events:none" in fixed_rule
    assert ".answer-slot.empty" in styles
    assert ".candidate-area{padding:" in styles
