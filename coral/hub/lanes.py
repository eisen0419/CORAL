"""Lane discovery: extract per-agent `focus-*.md` declarations from shared notes.

A "lane" is the specific technique/area an agent has publicly committed
to via a focus note (CORAL.md "Claim the lane publicly via a focus note"
section). Reading the active lane set lets the manager surface what the
team is *already* exploring — useful when the pivot heartbeat fires
because plateau detection wants the agent to pick a *different* lane,
not the same one.

A focus note looks like:

    ---
    creator: agent-1
    created: 2026-05-19T...
    posture: engineer
    lane: gradient-clipping
    budget: 5 evals
    abandon_if: score < 0.7
    ---
    # Focus: gradient clipping
    ...

We treat any `notes/focus-*.md` with a `lane:` frontmatter key as active.
There's no expiration logic — the note is the contract; deleting it
means the agent abandoned the direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coral.hub.notes import _parse_frontmatter


@dataclass
class ActiveLane:
    """A single focus-note declaration."""

    lane: str
    posture: str
    creator: str
    filename: str

    def to_dict(self) -> dict[str, str]:
        return {
            "lane": self.lane,
            "posture": self.posture,
            "creator": self.creator,
            "filename": self.filename,
        }


def _notes_dir(coral_dir: str | Path) -> Path:
    return Path(coral_dir) / "public" / "notes"


def list_active_lanes(coral_dir: str | Path) -> list[ActiveLane]:
    """Scan `.coral/public/notes/focus-*.md` and return parsed lane entries.

    Skips files that don't have a `lane:` frontmatter key. Sorted by lane
    name so prompt output is deterministic.
    """
    notes_dir = _notes_dir(coral_dir)
    if not notes_dir.is_dir():
        return []
    lanes: list[ActiveLane] = []
    for path in notes_dir.glob("focus-*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _body = _parse_frontmatter(text)
        lane = meta.get("lane", "").strip()
        if not lane:
            continue
        lanes.append(
            ActiveLane(
                lane=lane,
                posture=meta.get("posture", "").strip(),
                creator=meta.get("creator", "").strip(),
                filename=path.name,
            )
        )
    lanes.sort(key=lambda x: (x.lane, x.creator))
    return lanes


def format_lanes_for_prompt(lanes: list[ActiveLane]) -> str:
    """Render an `ActiveLane` list as a Markdown bullet block.

    Returns an empty string if `lanes` is empty so the caller can simply
    append the result without checking.
    """
    if not lanes:
        return ""
    lines = ["### Active lanes already being explored by the team", ""]
    lines.append(
        "Each entry is a teammate's *current public commitment*. "
        "Picking a lane in this set duplicates their effort; "
        "pick a different lane, OR pick the same lane with a *different posture* "
        "(e.g. reviewer trying to falsify, performance engineer profiling instead "
        "of building). Same-lane-same-posture is the failure mode."
    )
    lines.append("")
    for lane in lanes:
        posture = f" *(posture: {lane.posture})*" if lane.posture else ""
        creator = f" — {lane.creator}" if lane.creator else ""
        lines.append(f"- **{lane.lane}**{posture}{creator} — `{lane.filename}`")
    return "\n".join(lines)


def augment_pivot_prompt(prompt: str, coral_dir: str | Path) -> str:
    """Append the active-lane block to a `pivot` heartbeat prompt.

    No-op when no focus notes exist (early in a run, or after they've
    been cleared). When called, the block goes after a clean separator
    so the rendered prompt reads coherently to the agent.
    """
    lanes = list_active_lanes(coral_dir)
    block = format_lanes_for_prompt(lanes)
    if not block:
        return prompt
    return f"{prompt.rstrip()}\n\n---\n\n{block}"
