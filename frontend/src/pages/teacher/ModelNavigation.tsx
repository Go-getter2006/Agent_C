export const MODEL_SECTIONS = [
  { key: "models-overview", label: "模型概览" },
  { key: "models-validation", label: "预测检验" },
  { key: "models-parameters", label: "参数与融合" },
  { key: "models-prerequisites", label: "先修诊断" },
] as const;

export type ModelSection = typeof MODEL_SECTIONS[number]["key"];

export default function ModelNavigation({ value, onChange }: {
  value: ModelSection; onChange: (section: ModelSection) => void;
}) {
  return <div className="tabs" role="tablist" aria-label="模型管理内容">
    {MODEL_SECTIONS.map((section, index) => <button key={section.key} type="button" role="tab"
      id={`tab-${section.key}`} aria-controls={`panel-${section.key}`} aria-selected={value === section.key}
      tabIndex={value === section.key ? 0 : -1} onClick={() => onChange(section.key)}
      onKeyDown={event => {
        const next = event.key === "ArrowRight" ? (index + 1) % MODEL_SECTIONS.length
          : event.key === "ArrowLeft" ? (index + MODEL_SECTIONS.length - 1) % MODEL_SECTIONS.length
          : event.key === "Home" ? 0 : event.key === "End" ? MODEL_SECTIONS.length - 1 : null;
        if (next === null) return;
        event.preventDefault();
        onChange(MODEL_SECTIONS[next].key);
        document.getElementById(`tab-${MODEL_SECTIONS[next].key}`)?.focus();
      }}>{section.label}</button>)}
  </div>;
}
