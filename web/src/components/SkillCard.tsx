/**
 * Renders a single Skill entry. Centralizes language handling so the
 * rest of the dashboard can simply map over the skill list.
 *
 * Built-in CORAL skills (deep-research / organize-files / skill-creator)
 * have hand-translated Chinese descriptions in builtin-skills-zh.json —
 * those are used directly when the UI is in zh mode, sparing an LLM
 * call. Anything else falls through to useTranslated.
 */

import { useTranslation } from "react-i18next";
import type { Skill } from "../lib/api";
import { useTranslated } from "../hooks/useTranslated";
import builtinZh from "../locales/builtin-skills-zh.json";

type BuiltinKey = keyof typeof builtinZh;

interface Props {
  skill: Skill;
  variant?: "compact" | "full";
}

export default function SkillCard({ skill, variant = "compact" }: Props) {
  const { i18n } = useTranslation();
  const isZh = i18n.language.startsWith("zh");

  // Built-in CORAL skills have hand-translated copy
  const builtin = isZh && (skill.name in builtinZh)
    ? builtinZh[skill.name as BuiltinKey]
    : null;

  const desc = skill.description ?? "";
  const descTr = useTranslated(builtin ? "" : desc, "skill_description");

  const displayName = builtin ? builtin.name : skill.name;
  const displayDesc = builtin
    ? builtin.description
    : desc
      ? descTr.text
      : "";
  const isStub = !builtin && (descTr.status === "translating" || descTr.status === "disabled");
  const isErr = !builtin && descTr.status === "error";

  if (variant === "full") {
    return (
      <div className="p-4 border border-border rounded-lg hover:bg-muted/50 transition-colors duration-100">
        <p className="font-display text-[14px] font-semibold mb-1">{displayName}</p>
        {displayDesc && (
          <p
            className={`font-body text-[13px] mb-2 ${
              isStub ? "text-muted-fg italic" : "text-muted-fg"
            } ${isErr ? "text-red-600 dark:text-red-400 cursor-pointer hover:underline" : ""}`}
            onClick={isErr ? () => descTr.retry() : undefined}
            title={isErr ? "点击重试翻译" : undefined}
          >
            {displayDesc}
          </p>
        )}
        <div className="font-mono text-[10px] text-muted-fg flex gap-3">
          {skill.creator && <span>{isZh ? "作者：" : "By:"} {skill.creator}</span>}
          {skill.created && <span>{String(skill.created).slice(0, 10)}</span>}
        </div>
      </div>
    );
  }

  return (
    <div className="p-3 border border-border rounded-lg hover:bg-muted/50 transition-colors duration-100">
      <p className="font-display text-[13px] font-semibold mb-0.5">{displayName}</p>
      {displayDesc && (
        <p
          className={`font-body text-[12px] truncate ${
            isStub ? "text-muted-fg italic" : "text-muted-fg"
          } ${isErr ? "text-red-600 dark:text-red-400 cursor-pointer hover:underline" : ""}`}
          onClick={isErr ? () => descTr.retry() : undefined}
          title={isErr ? "点击重试翻译" : undefined}
        >
          {displayDesc}
        </p>
      )}
    </div>
  );
}
