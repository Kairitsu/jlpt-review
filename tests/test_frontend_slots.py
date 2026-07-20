from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_practice_frontend_renders_one_slot_per_structure_slot_and_never_a_fixed_candidate():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "const structure = (s.practiceStructure || []).map" in source
    assert "const index = slotIndex++" in source
    assert 'class="answer-slot empty"' in source
    assert 'class="fixed-element"' in source
    assert "shuffle(sentence.chunks.map(chunk => chunk.id))" in source
    assert "slotAssignments: Array(sentence.chunks.length).fill(null)" in source
    assert "const id = item.slotAssignments[index]" in source
    assert "item.selected" not in source
    assert "practiceStructure:state.draft.practiceStructure" in source


def test_practice_slot_assignments_keep_holes_and_project_null_free_answer_order():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    move_flow = source.split("function moveSelectedTo", 1)[1].split(
        "function removeSelectedId", 1
    )[0]
    remove_flow = source.split("function removeSelectedId", 1)[1].split(
        "function updatePracticeSelection", 1
    )[0]
    answer_order_flow = source.split("function practiceAnswerOrder", 1)[1].split(
        "function resetPracticeSlotAssignments", 1
    )[0]

    assert "assignments[targetIndex] = id" in move_flow
    assert "assignments[emptyIndex] = displaced" in move_flow
    assert "[assignments[from], assignments[targetIndex]]" in move_flow
    assert "item.slotAssignments[from] = null" in remove_flow
    assert ".splice(" not in move_flow + remove_flow
    assert ".filter(id => id != null)" in answer_order_flow
    assert "answerOrder: practiceAnswerOrder(practice.items[index])" in source
    assert "const answerOrder = practiceAnswerOrder(item)" in source


def test_practice_drag_uses_in_slot_ruby_preview_without_flex_drop_marker():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "drop-slot" not in source
    assert ".drop-slot" not in styles
    assert "practice-drop-preview" in source
    assert "preview.innerHTML = session.previewHtml" in source
    assert "target.appendChild(preview)" in source
    assert "preview.setAttribute('aria-hidden', 'true')" in source
    assert "clearPracticeDropPreview()" in source
    assert ".practice-drop-preview{position:absolute" in styles
    preview_rule = styles.split(".practice-drop-preview{", 1)[1].split("}", 1)[0]
    assert "pointer-events:none" in preview_rule
    assert "opacity:" in preview_rule
    assert "border:2px dashed" in preview_rule


def test_slot_css_wraps_without_horizontal_overflow_and_fixed_text_is_not_interactive():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".preview-structure,.answer-sequence{display:flex;flex-wrap:wrap" in styles
    assert ".fixed-element{" in styles
    fixed_rule = styles.split(".fixed-element{", 1)[1].split("}", 1)[0]
    assert "user-select:none" in fixed_rule
    assert "pointer-events:none" in fixed_rule
    assert ".answer-slot.empty" in styles
    assert ".candidate-area{padding:" in styles


def test_practice_chunks_slice_existing_furigana_by_exact_ranges_with_safe_fallbacks():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    helper = source.split("function chunkRubyHtml(sentence, chunk) {", 1)[1].split(
        "function formatDate", 1
    )[0]

    assert "Number.isInteger(start)" in helper
    assert "Number.isInteger(end)" in helper
    assert "Array.from(japanese)" in helper
    assert "sentenceChars.slice(start, end).join('') !== text" in helper
    assert "segments.map(seg => seg.text).join('') !== japanese" in helper
    assert "wholeSegment && segment.ruby" in helper
    assert "rubyHtml(sliced)" in helper
    assert "indexOf(" not in helper
    assert "${chunkRubyHtml(s, map[id])}" in source
    assert "const correctJp = rubyHtml(s.furigana) || esc(s.japanese);" in source


def test_practice_chunk_ruby_css_reserves_annotation_space_on_desktop_and_mobile():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert ".practice-page .candidate{max-width:100%;min-height:62px" in styles
    assert ".answer-slot{position:relative;display:inline-flex" in styles
    assert "min-height:64px;padding:14px 10px 8px" in styles
    assert ".practice-page rt{font-size:.55em;line-height:1;white-space:nowrap}" in styles
    assert ".practice-page .candidate{min-height:58px" in styles
