// Ícones SVG inline (traço 1.8, herdam currentColor).
type P = { className?: string };
const base = (d: React.ReactNode) =>
  function Icon({ className = "size-4" }: P) {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
        {d}
      </svg>
    );
  };

export const Copy = base(<><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></>);
export const Check = base(<path d="m5 12 5 5 9-10" />);
export const Brain = base(<><path d="M9 4a3 3 0 0 0-3 3v.5A3 3 0 0 0 4 10.5a3 3 0 0 0 1 2.2A3 3 0 0 0 6 18a3 3 0 0 0 3 2 3 3 0 0 0 3-3V7a3 3 0 0 0-3-3z" /><path d="M15 4a3 3 0 0 1 3 3v.5a3 3 0 0 1 2 3 3 3 0 0 1-1 2.2 3 3 0 0 1-1 5.3 3 3 0 0 1-3 2 3 3 0 0 1-3-3" /></>);
export const Chevron = base(<path d="m8 10 4-4 4 4M8 14l4 4 4-4" />);
export const ChevronDown = base(<path d="m6 9 6 6 6-6" />);
export const Cube = base(<><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" /><path d="m4 7.5 8 4.5 8-4.5M12 12v9" /></>);
export const Tokens = base(<><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" /></>);
export const Clock = base(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>);
export const Balanca = base(<><path d="M12 4v16M8 20h8M4 8h16" /><path d="m4 8-2.5 5a3 3 0 0 0 5 0z" /><path d="m20 8-2.5 5a3 3 0 0 0 5 0z" /></>);
export const Gauge = base(<><path d="M4 16a8 8 0 1 1 16 0" /><path d="m12 16 4-5" /></>);
export const ArrowUp = base(<path d="M12 19V5m-6 6 6-6 6 6" />);
export const Eye = base(<><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z" /><circle cx="12" cy="12" r="2.8" /></>);
export const EyeOff = base(<><path d="M3 3l18 18" /><path d="M10.6 6.2A9.9 9.9 0 0 1 12 6c6.4 0 10 6 10 6a17 17 0 0 1-3.3 3.8" /><path d="M6.3 8.2A17 17 0 0 0 2 12s3.6 6 10 6a9.8 9.8 0 0 0 3.5-.6" /></>);
export const Square = ({ className = "size-3.5" }: P) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
    <rect x="5" y="5" width="14" height="14" rx="2.5" />
  </svg>
);
export const Edit = base(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></>);
export const Search = base(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>);
export const Trash = base(<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />);
export const Gear = base(<><circle cx="12" cy="12" r="3" /><path d="M19.4 13.5a7.7 7.7 0 0 0 0-3l1.7-1.3-2-3.4-2 .8a7.7 7.7 0 0 0-2.6-1.5L14.2 3H9.8l-.3 2.1a7.7 7.7 0 0 0-2.6 1.5l-2-.8-2 3.4 1.7 1.3a7.7 7.7 0 0 0 0 3L2.9 15l2 3.4 2-.8a7.7 7.7 0 0 0 2.6 1.5l.3 2.1h4.4l.3-2.1a7.7 7.7 0 0 0 2.6-1.5l2 .8 2-3.4z" /></>);
export const Paperclip = base(<path d="M21 11.5 12.5 20a5 5 0 0 1-7-7l8-8a3.5 3.5 0 0 1 5 5l-8 8a2 2 0 0 1-3-3l7.5-7.5" />);
export const Refresh = base(<><path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 4v5h-5" /></>);
export const Shield = base(<><path d="M12 3l7 3v6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z" /><path d="m9 12 2 2 4-4" /></>);
export const X = base(<path d="M6 6l12 12M18 6L6 18" />);
export const Plus = base(<path d="M12 5v14M5 12h14" />);
export const GitBranch = base(<><circle cx="6" cy="5" r="2.5" /><circle cx="6" cy="19" r="2.5" /><circle cx="18" cy="8" r="2.5" /><path d="M6 7.5v9M18 10.5c0 3-3 4-6 4s-6 1-6 3" /></>);
export const Terminal = base(<><path d="m5 7 5 5-5 5" /><path d="M12 17h7" /></>);
export const Pin = base(<><path d="M9 4h6l-1 6 3 3v1H7v-1l3-3z" /><path d="M12 14v6" /></>);
export const Archive = base(<><rect x="3" y="4" width="18" height="4" rx="1" /><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4" /></>);
export const More = base(<><circle cx="5" cy="12" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /><circle cx="19" cy="12" r="1.2" fill="currentColor" /></>);
export const ExternalLink = base(<><path d="M14 4h6v6" /><path d="M20 4 10 14" /><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6" /></>);
export const FolderOpen = base(<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H6.5a2 2 0 0 0-1.9 1.4L3 17z M3 17l1.6-5.6A2 2 0 0 1 6.5 10H21l-2 7a2 2 0 0 1-1.9 1.4H5a2 2 0 0 1-2-1.4z" />);
export const Download = base(<><path d="M12 4v11m-5-5 5 5 5-5" /><path d="M4 19h16" /></>);
export const Activity = base(<path d="M3 12h4l3-8 4 16 3-8h4" />);
export const Globe = base(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a13.5 13.5 0 0 1 0 18M12 3a13.5 13.5 0 0 0 0 18" /></>);
export const Info = base(<><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>);
export const PanelRight = base(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></>);
export const ArrowLeft = base(<path d="M19 12H5m6-6-6 6 6 6" />);
export const ArrowRight = base(<path d="M5 12h14m-6-6 6 6-6 6" />);
export const Folder = base(<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />);
export const Laptop = base(<><rect x="4" y="5" width="16" height="11" rx="1.5" /><path d="M2 19h20" /></>);
export const Undo = base(<><path d="M9 14 4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" /></>);
export const Split = base(<><circle cx="6" cy="6" r="2" /><circle cx="18" cy="6" r="2" /><circle cx="12" cy="19" r="2" /><path d="M6 8v2a4 4 0 0 0 4 4h0a2 2 0 0 1 2 2v1M18 8v2a4 4 0 0 1-4 4" /></>);
export const Bubble = base(<><rect x="3" y="4" width="18" height="13" rx="3.5" /><path d="M8.5 17v3.5L13 17" /></>);
export const Code = base(<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 5l-4 14" />);
export const PanelLeft = base(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>);
export const Sliders = base(<><path d="M4 8h10M18 8h2M4 16h4M12 16h8" /><circle cx="16" cy="8" r="2" /><circle cx="10" cy="16" r="2" /></>);
export const Clipboard = base(<><rect x="6" y="4" width="12" height="16" rx="2" /><path d="M9 4h6v3H9z" /><path d="M9 11h6M9 15h4" /></>);
export const Wrench = base(<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z" />);
export const Cpu = base(<><rect x="7" y="7" width="10" height="10" rx="1.5" /><rect x="4" y="4" width="16" height="16" rx="2.5" /><path d="M9 1.5v2.5M15 1.5v2.5M9 20v2.5M15 20v2.5M1.5 9H4M1.5 15H4M20 9h2.5M20 15h2.5" /></>);
export const Image = base(<><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="8.5" cy="10" r="1.5" /><path d="m4 17 5-5 4 4 3-3 4 4" /></>);
export const Recolher = base(<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />);
export const Expandir = base(<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />);
