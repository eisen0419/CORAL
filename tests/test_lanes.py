"""Tests for coral.hub.lanes — focus-note discovery + pivot prompt augmentation."""

from __future__ import annotations

from pathlib import Path

from coral.hub.lanes import (
    ActiveLane,
    augment_pivot_prompt,
    format_lanes_for_prompt,
    list_active_lanes,
)


def _write_focus(notes_dir: Path, name: str, frontmatter: dict[str, str], body: str = "x") -> None:
    notes_dir.mkdir(parents=True, exist_ok=True)
    front = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    (notes_dir / name).write_text(f"---\n{front}\n---\n\n{body}\n", encoding="utf-8")


# --- list_active_lanes --------------------------------------------------------


def test_no_lanes_when_notes_dir_absent(tmp_path: Path) -> None:
    assert list_active_lanes(tmp_path) == []


def test_no_lanes_when_no_focus_notes(tmp_path: Path) -> None:
    notes_dir = tmp_path / "public" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "regular-note.md").write_text("just a note\n")
    (notes_dir / "index.md").write_text("# Index\n")
    assert list_active_lanes(tmp_path) == []


def test_picks_up_focus_notes_with_lane(tmp_path: Path) -> None:
    notes_dir = tmp_path / "public" / "notes"
    _write_focus(
        notes_dir,
        "focus-grad-clip.md",
        {"creator": "agent-1", "posture": "engineer", "lane": "gradient-clipping"},
    )
    _write_focus(
        notes_dir,
        "focus-mpc.md",
        {"creator": "agent-2", "posture": "performance engineer", "lane": "mpc-residual"},
    )
    lanes = list_active_lanes(tmp_path)
    assert len(lanes) == 2
    # Sorted by lane name.
    assert lanes[0].lane == "gradient-clipping"
    assert lanes[0].creator == "agent-1"
    assert lanes[1].lane == "mpc-residual"
    assert lanes[1].posture == "performance engineer"


def test_skips_focus_files_without_lane_key(tmp_path: Path) -> None:
    notes_dir = tmp_path / "public" / "notes"
    _write_focus(
        notes_dir,
        "focus-no-lane.md",
        {"creator": "agent-1", "posture": "engineer"},  # missing `lane`
    )
    _write_focus(
        notes_dir,
        "focus-real.md",
        {"creator": "agent-2", "lane": "real-thing"},
    )
    lanes = list_active_lanes(tmp_path)
    assert len(lanes) == 1
    assert lanes[0].lane == "real-thing"


def test_skips_files_not_matching_focus_glob(tmp_path: Path) -> None:
    notes_dir = tmp_path / "public" / "notes"
    # File has `lane:` but doesn't start with "focus-".
    notes_dir.mkdir(parents=True)
    (notes_dir / "random.md").write_text(
        "---\ncreator: x\nlane: hidden\n---\n\nbody\n", encoding="utf-8"
    )
    assert list_active_lanes(tmp_path) == []


# --- format_lanes_for_prompt --------------------------------------------------


def test_format_empty_returns_empty() -> None:
    assert format_lanes_for_prompt([]) == ""


def test_format_includes_lane_posture_creator_filename() -> None:
    lanes = [
        ActiveLane(
            lane="grad-clip",
            posture="engineer",
            creator="agent-1",
            filename="focus-grad-clip.md",
        )
    ]
    out = format_lanes_for_prompt(lanes)
    assert "grad-clip" in out
    assert "posture: engineer" in out
    assert "agent-1" in out
    assert "focus-grad-clip.md" in out
    assert "Pick a lane" not in out or "different lane" in out


def test_format_handles_missing_posture_and_creator() -> None:
    lanes = [
        ActiveLane(lane="bare", posture="", creator="", filename="focus-bare.md"),
    ]
    out = format_lanes_for_prompt(lanes)
    assert "bare" in out
    # Empty posture/creator must not produce stray "posture: " or " — " noise.
    assert "(posture: )" not in out


# --- augment_pivot_prompt -----------------------------------------------------


def test_augment_noop_without_focus_notes(tmp_path: Path) -> None:
    out = augment_pivot_prompt("Original pivot prompt.", tmp_path)
    assert out == "Original pivot prompt."


def test_augment_appends_lane_block_when_notes_present(tmp_path: Path) -> None:
    notes_dir = tmp_path / "public" / "notes"
    _write_focus(
        notes_dir,
        "focus-grad.md",
        {"creator": "agent-1", "lane": "grad-clip", "posture": "engineer"},
    )
    out = augment_pivot_prompt("Original pivot prompt.", tmp_path)
    # Original kept intact.
    assert out.startswith("Original pivot prompt.")
    # Block separator + lane data appended.
    assert "---" in out
    assert "grad-clip" in out
    assert "agent-1" in out


def test_augment_deterministic_ordering(tmp_path: Path) -> None:
    notes_dir = tmp_path / "public" / "notes"
    _write_focus(notes_dir, "focus-c.md", {"creator": "agent-1", "lane": "c-lane"})
    _write_focus(notes_dir, "focus-a.md", {"creator": "agent-2", "lane": "a-lane"})
    _write_focus(notes_dir, "focus-b.md", {"creator": "agent-3", "lane": "b-lane"})
    out = augment_pivot_prompt("p.", tmp_path)
    # Lanes appear in alphabetical order regardless of filename order.
    a_pos = out.find("a-lane")
    b_pos = out.find("b-lane")
    c_pos = out.find("c-lane")
    assert 0 < a_pos < b_pos < c_pos
