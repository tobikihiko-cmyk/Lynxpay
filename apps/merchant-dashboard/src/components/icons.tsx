import type { SVGProps } from "react";

export type IconName = "payments" | "reconcile" | "onboarding" | "key" | "webhook" | "audit" | "approval" | "search" | "bell" | "menu" | "close" | "arrow" | "shield" | "check" | "clock" | "review" | "refresh";

const paths: Record<IconName, React.ReactNode> = {
  payments: <><path d="M3 7.5h18M6 4h12a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3Z"/><path d="M7 15h3"/></>,
  reconcile: <><path d="M20 7h-7a4 4 0 0 0-4 4v1"/><path d="m17 4 3 3-3 3M4 17h7a4 4 0 0 0 4-4v-1"/><path d="m7 20-3-3 3-3"/></>,
  onboarding: <><path d="M12 3v18M3 12h18"/><circle cx="12" cy="12" r="9"/></>,
  key: <><circle cx="8" cy="15" r="4"/><path d="m11 12 8-8M15 8l3 3M17 6l2 2"/></>,
  webhook: <><circle cx="6" cy="7" r="3"/><circle cx="18" cy="7" r="3"/><circle cx="12" cy="18" r="3"/><path d="m8.5 8.5 2 6M15.5 8.5l-2 6"/></>,
  audit: <><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h5"/></>,
  approval: <><path d="M12 3 4 7v5c0 5 3.5 8 8 9 4.5-1 8-4 8-9V7Z"/><path d="m9 12 2 2 4-4"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></>,
  menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
  close: <><path d="m6 6 12 12M18 6 6 18"/></>,
  arrow: <><path d="M5 12h14M14 7l5 5-5 5"/></>,
  shield: <><path d="M12 3 4 7v5c0 5 3.5 8 8 9 4.5-1 8-4 8-9V7Z"/><path d="m9 12 2 2 4-4"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  review: <><path d="M12 9v4M12 17h.01"/><path d="M10.3 4.6 2.8 18a2 2 0 0 0 1.7 3h15a2 2 0 0 0 1.7-3L13.7 4.6a2 2 0 0 0-3.4 0Z"/></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M18.4 9A7 7 0 0 0 6 6.7L4 9M5.6 15A7 7 0 0 0 18 17.3l2-2.3"/></>
};

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...props}>{paths[name]}</svg>;
}
