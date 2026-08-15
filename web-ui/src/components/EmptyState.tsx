import type { LucideIcon } from "lucide-react";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  detail: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon: Icon, title, detail, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <Icon size={24} strokeWidth={1.6} aria-hidden="true" />
      <h2>{title}</h2>
      <p>{detail}</p>
      {action}
    </div>
  );
}
