import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type RunStatus } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import { toggleLanguage, currentLanguage } from "../lib/i18n";
import RunSelector from "./RunSelector";

type Tab = "overview" | "knowledge" | "logs";

interface Props {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
}

export default function Header({ activeTab, onTabChange }: Props) {
  const { t, i18n } = useTranslation();
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [, forceRender] = useState(0);

  const refresh = () => {
    api.status().then(setStatus).catch(() => {});
  };

  useEffect(refresh, []);
  useSSE({
    "attempt:new": refresh,
    "attempt:update": refresh,
    "eval:update": refresh,
  });

  // re-render the language toggle label when locale changes
  useEffect(() => {
    const handler = () => forceRender((n) => n + 1);
    i18n.on("languageChanged", handler);
    return () => {
      i18n.off("languageChanged", handler);
    };
  }, [i18n]);

  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: t("tabs.overview") },
    { key: "knowledge", label: t("tabs.knowledge") },
    { key: "logs", label: t("tabs.logs") },
  ];

  const activeAgents = status?.agents.filter((a) => a.status === "active").length ?? 0;

  return (
    <header className="sticky top-0 z-50 bg-background border-b border-border px-6 py-2.5 flex items-center gap-6">
      {/* Branding + task */}
      <div className="flex items-center gap-3 shrink-0">
        <span className="font-display text-base font-bold tracking-tight">CORAL</span>
        <span className="text-border-strong">/</span>
        <RunSelector />
      </div>

      {/* Tab pills */}
      <nav className="flex items-center gap-1 ml-auto">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => onTabChange(tab.key)}
            className={`px-4 py-1.5 text-[13px] font-body rounded-lg transition-colors duration-100 ${
              activeTab === tab.key
                ? "bg-foreground text-background font-medium"
                : "text-muted-fg hover:text-foreground hover:bg-muted"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {/* Live stats */}
      <div className="flex items-center gap-3 shrink-0 font-mono text-[11px] text-muted-fg">
        {status && (
          <>
            <span className="flex items-center gap-1.5">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  status.manager_alive ? "bg-green-500" : "bg-border-strong"
                }`}
              />
              {activeAgents > 0
                ? t("header.active", { count: activeAgents })
                : status.manager_alive
                ? t("header.idle")
                : t("header.stopped")}
            </span>
            <span>{status.total_attempts} {t("header.attempts_short")}</span>
            {status.best_score != null && (
              <span>{t("header.best")} {status.best_score.toFixed(4)}</span>
            )}
            <span>#{status.eval_count}</span>
          </>
        )}
      </div>

      {/* Language toggle */}
      <button
        onClick={toggleLanguage}
        title={t("header.language")}
        className="shrink-0 px-2 py-1 text-[11px] font-mono rounded-md border border-border text-muted-fg hover:text-foreground hover:bg-muted transition-colors duration-100"
      >
        {currentLanguage() === "zh" ? "中" : "EN"}
      </button>
    </header>
  );
}
