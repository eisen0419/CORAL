import { useEffect, useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { api, type Note, type Skill } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import NoteCard from "../components/NoteCard";
import SkillCard from "../components/SkillCard";

const CATEGORY_ORDER = ["research", "experiments", "other", "raw"];
const CATEGORY_KEYS: Record<string, string> = {
  research: "knowledge.cat_research",
  experiments: "knowledge.cat_experiments",
  raw: "knowledge.cat_raw",
  other: "knowledge.cat_other",
};

export default function Knowledge() {
  const { t } = useTranslation();
  const [notes, setNotes] = useState<Note[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [expandedNote, setExpandedNote] = useState<number | null>(null);

  const refreshNotes = () => api.notes().then(setNotes).catch(() => {});
  const refreshSkills = () => api.skills().then(setSkills).catch(() => {});

  useEffect(() => {
    refreshNotes();
    refreshSkills();
  }, []);

  useSSE({ "note:update": refreshNotes });

  const groupedNotes = useMemo(() => {
    const groups: Record<string, Note[]> = {};
    for (const note of notes) {
      const cat = note.category || "other";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(note);
    }
    // Sort categories by defined order, unknown categories at the end
    const sorted = Object.entries(groups).sort(([a], [b]) => {
      const ia = CATEGORY_ORDER.indexOf(a);
      const ib = CATEGORY_ORDER.indexOf(b);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });
    return sorted;
  }, [notes]);

  return (
    <>
      {/* LEFT COLUMN — Notes */}
      <div className="overflow-y-auto border-r border-border p-5">
        <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
          {t("knowledge.notes", { count: notes.length })}
        </p>

        {notes.length === 0 ? (
          <div className="border border-border rounded-xl p-5">
            <p className="font-display text-[14px] font-semibold mb-1.5">
              {t("knowledge.no_notes_title")}
            </p>
            <p className="font-body text-[12px] text-muted-fg leading-relaxed">
              {t("knowledge.no_notes_hint")}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {groupedNotes.map(([category, catNotes]) => (
              <div key={category}>
                <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-2">
                  {t("knowledge.category_count", {
                    category: CATEGORY_KEYS[category] ? t(CATEGORY_KEYS[category]) : category,
                    count: catNotes.length,
                  })}
                </p>
                <div className="border border-border rounded-xl overflow-hidden">
                  {[...catNotes].reverse().map((note) => (
                    <NoteCard
                      key={note.index}
                      note={note}
                      expanded={expandedNote === note.index}
                      onToggle={() =>
                        setExpandedNote(expandedNote === note.index ? null : note.index)
                      }
                      variant="full"
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* RIGHT COLUMN — Skills */}
      <div className="overflow-y-auto p-5">
        <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
          {t("knowledge.skills", { count: skills.length })}
        </p>

        {skills.length === 0 ? (
          <div className="border border-border rounded-xl p-5">
            <p className="font-display text-[14px] font-semibold mb-1.5">
              {t("knowledge.no_skills_title")}
            </p>
            <p className="font-body text-[12px] text-muted-fg leading-relaxed">
              {t("knowledge.no_skills_hint")}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {skills.map((skill) => (
              <SkillCard key={skill.name} skill={skill} variant="full" />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
