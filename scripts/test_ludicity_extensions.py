# -*- coding: utf-8 -*-
"""Unit tests for Ludicity extension parse / display logic (no LLM)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extensions.ludicity_shared.parse import (
    extract_choices_block,
    extract_scene_body,
    is_placeholder_section,
    is_real_ending,
)


def test_life_sim_turn1_stub_ending_not_game_over() -> None:
    raw = """---SCENARIO---
The Great Plague of 1665 strikes London. Streets fill with the dead.

---DELTAS---
morale:0 supplies:0 safety:0

---ENDING---
(only if the crisis is truly over or a stat hits 0/100: short epilogue; otherwise leave this section completely empty)

End the SCENARIO section by asking: What do you want to do?"""
    end = raw.split("---ENDING---", 1)[1].strip() if "---ENDING---" in raw else ""
    assert is_placeholder_section(end), "stub ENDING must not end game"
    assert not is_real_ending(end)
    body = extract_scene_body(raw)
    assert "Great Plague" in body
    assert "what do you want" not in body.lower() or "plague" in body.lower()
    assert "---SCENARIO---" not in body
    print("OK life_sim turn1 stub ending")


def test_life_sim_instruction_line_stripped() -> None:
    raw = """---SCENARIO---
London burns with fear.

ADD "What do you want to do?"

---DELTAS---
morale:-5 supplies:-5 safety:-5

---ENDING---
"""
    body = extract_scene_body(raw)
    assert "ADD" not in body
    assert "London burns" in body
    print("OK life_sim instruction strip")


def test_narration_continue_parses_scene_and_choices() -> None:
    raw = """---SCENE---
You slip through the alley, heart pounding, as footsteps fade behind the rain.

---CHOICES---
1. Knock on the lighthouse keeper's door
2. Hide in the abandoned boat shed
3. Call out to the figure on the cliff"""
    scene = extract_scene_body(raw)
    choices = extract_choices_block(raw)
    assert "alley" in scene
    assert "lighthouse" in choices
    assert "PLAYER CHOICE" not in scene
    print("OK narration continue parse")


def test_narration_rejects_prompt_echo() -> None:
    raw = """The player chose an action. Continue the story from the LAST sentence.
---SCENE---
You run toward the harbor."""
    scene = extract_scene_body(raw)
    assert scene.startswith("You run")
    assert "player chose" not in scene.lower()
    print("OK narration prompt echo rejected")


def test_debate_round_leadership() -> None:
    for rnd, a_leads in [(1, True), (2, False), (3, True), (4, False), (5, True)]:
        assert (rnd % 2 == 1) == a_leads, f"round {rnd}"
    print("OK debate round alternation")


def test_life_sim_apply_turn_state() -> None:
    """Simulate _apply_turn_reply logic without UI."""
    state = {
        "turn": 1,
        "stats": {"morale": 50, "supplies": 50, "safety": 50},
        "game_over": False,
        "ending": "",
        "scenario": "",
    }
    raw = """---SCENARIO---
The plague spreads through London.

---DELTAS---
morale:-10 supplies:-5 safety:-5

---ENDING---
(only if the crisis is truly over: epilogue; else leave empty)"""
    parts = __import__(
        "extensions.ludicity_shared.parse", fromlist=["parse_tagged_sections"]
    ).parse_tagged_sections(raw, "SCENARIO", "DELTAS", "ENDING")
    end = (parts.get("ENDING") or "").strip()
    assert not is_real_ending(end)
    scenario = extract_scene_body(raw)
    state["scenario"] = scenario
    assert state["game_over"] is False
    assert "plague" in scenario.lower()
    print("OK life_sim apply turn state")


def test_narration_choice_parsing() -> None:
    block = """1. <Attempt to weave a thread of silence>
2. Let the Spire consume the needles
3. <Try a different path entirely>"""
    from extensions.ludicity_shared.parse import parse_choice_list

    choices = parse_choice_list(block)
    assert len(choices) == 3
    assert "silence" in choices[0]
    assert choices[0] not in choices[1]
    print("OK narration choice parse")


def main() -> None:
    test_life_sim_turn1_stub_ending_not_game_over()
    test_life_sim_instruction_line_stripped()
    test_life_sim_apply_turn_state()
    test_narration_continue_parses_scene_and_choices()
    test_narration_rejects_prompt_echo()
    test_narration_choice_parsing()
    test_debate_round_leadership()
    print("\nAll ludicity extension tests passed.")


if __name__ == "__main__":
    main()
