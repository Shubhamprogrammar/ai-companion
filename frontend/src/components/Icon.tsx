// A small inline icon set (24×24, stroke-based) so there is no icon dependency.

const PATHS = {
  chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z",
  brain:
    "M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a3 3 0 0 0-3-1Zm6 0a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2Zm4 4a2 2 0 0 0 4 0",
  check: "M5 12.5 10 17l9-10",
  checkSquare: "M4 4h16v16H4zM8 12l3 3 5-6",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  settings:
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2.2-1.3L14.4 3h-4l-.4 2.4a7 7 0 0 0-2.2 1.3l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2.2 1.3l.4 2.4h4l.4-2.4a7 7 0 0 0 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.1-.9.1-1.3Z",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Zm-7 9a7 7 0 0 0 14 0M12 19v3",
  micOff: "M3 3l18 18M9 9v3a3 3 0 0 0 5 2.2M15 9.3V6a3 3 0 0 0-5.7-1.3M5 12a7 7 0 0 0 11.5 5.4M19 12a7 7 0 0 1-.6 2.8M12 19v3",
  send: "M4 12 20 4l-6 16-3-7-7-1Z",
  plus: "M12 5v14M5 12h14",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  edit: "M4 20h4L19 9l-4-4L4 16v4ZM13 7l4 4",
  x: "M6 6l12 12M18 6 6 18",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  menu: "M4 6h16M4 12h16M4 18h16",
  stop: "M7 7h10v10H7z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 4 4",
  archive: "M4 5h16v4H4zM5 9v10h14V9M10 13h4",
  unarchive: "M4 5h16v4H4zM5 9v10h14V9M12 17v-5M9.5 14.5 12 12l2.5 2.5",
  phoneOff: "M3 3l18 18M8.5 8.5A16 16 0 0 0 15.5 15.5l2-2 4 2v4a1 1 0 0 1-1 1A18 18 0 0 1 3.5 4.5a1 1 0 0 1 1-1h4l2 4-2 1",
  wave: "M3 12h2M7 8v8M11 5v14M15 8v8M19 10v4M21 12h0",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z",
  chevronDown: "M6 9l6 6 6-6",
  refresh: "M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5",
  volume: "M4 9h4l5-4v14l-5-4H4zM16 9a4 4 0 0 1 0 6",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
