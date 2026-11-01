export function SectionHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <header className="section-header"><p>{eyebrow}</p><h1>{title}</h1><span>{description}</span></header>;
}

export function EmptyState({ number, title, copy }: { number: string; title: string; copy: string }) {
  return <div className="empty-panel"><div className="empty-number">{number}</div><div><h2>{title}</h2><p>{copy}</p></div></div>;
}
