/**
 * Renders a collapsible note row. Title and body are translated
 * independently so the body — usually much longer — doesn't block
 * the user from seeing the title.
 */

import type { Note } from "../lib/api";
import { useTranslated } from "../hooks/useTranslated";

interface Props {
  note: Note;
  expanded: boolean;
  onToggle: () => void;
  variant?: "compact" | "full";
}

export default function NoteCard({ note, expanded, onToggle, variant = "compact" }: Props) {
  const titleTr = useTranslated(note.title, "note_title");
  const bodyTr = useTranslated(expanded ? note.body : "", "note_body");

  const isTitleStub = titleTr.status === "translating" || titleTr.status === "disabled";
  const isBodyStub = bodyTr.status === "translating" || bodyTr.status === "disabled";
  const isTitleErr = titleTr.status === "error";
  const isBodyErr = bodyTr.status === "error";

  const stopAndRetry = (retry: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    retry();
  };

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        onClick={onToggle}
        className={`w-full text-left ${
          variant === "full" ? "py-3.5 px-4" : "py-2.5 px-3"
        } hover:bg-muted/50 transition-colors duration-100 flex items-start gap-${
          variant === "full" ? "3" : "2"
        }`}
      >
        <div className="mt-1 shrink-0">
          <div
            className={`${
              variant === "full" ? "w-2.5 h-2.5" : "w-2 h-2"
            } border-2 border-foreground bg-background rounded-full`}
          />
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-mono text-[10px] text-muted-fg">
            {note.date}
            {note.relative_path && variant === "full" && (
              <span className="ml-2 opacity-60">{note.relative_path}</span>
            )}
          </p>
          <p
            className={`font-display ${
              variant === "full" ? "text-[14px]" : "text-[13px]"
            } font-semibold leading-snug ${variant === "full" ? "" : "truncate"} ${
              isTitleStub ? "text-muted-fg italic" : ""
            } ${isTitleErr ? "text-red-600 dark:text-red-400 cursor-pointer hover:underline" : ""}`}
            onClick={isTitleErr ? stopAndRetry(titleTr.retry) : undefined}
            title={isTitleErr ? "点击重试翻译" : undefined}
          >
            {titleTr.text}
          </p>
        </div>
        <span className="font-mono text-xs text-muted-fg shrink-0">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded && (
        <div className={`${variant === "full" ? "pb-4 pl-10 pr-4" : "pb-3 pl-7 pr-3"}`}>
          <div className="border-l-2 border-border pl-3">
            <div
              className={`font-body ${
                variant === "full" ? "text-[13px]" : "text-[12px]"
              } leading-relaxed whitespace-pre-wrap ${
                isBodyStub ? "text-muted-fg italic" : "text-muted-fg"
              } ${isBodyErr ? "text-red-600 dark:text-red-400 cursor-pointer hover:underline" : ""}`}
              onClick={isBodyErr ? stopAndRetry(bodyTr.retry) : undefined}
              title={isBodyErr ? "点击重试翻译" : undefined}
            >
              {bodyTr.text}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
