import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useMatch, useNavigate } from "react-router-dom";
import { errorMessage } from "../api/client";
import { conversations as conversationsApi } from "../api/endpoints";
import type { Conversation } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { COMPANION_NAME, relativeTime } from "../lib/format";
import { useAppData } from "./AppData";
import { Icon, type IconName } from "./Icon";
import { ConfirmDialog, Dialog, useToast } from "./ui";

const NAV: { to: string; label: string; icon: IconName }[] = [
  { to: "/memories", label: "Memories", icon: "brain" },
  { to: "/reminders", label: "Reminders", icon: "bell" },
  { to: "/tasks", label: "Tasks", icon: "checkSquare" },
  { to: "/activity", label: "Activity", icon: "activity" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

export function Layout() {
  const { user, logout } = useAuth();
  const { pending } = useAppData();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);

  // Close the mobile drawer on navigation.
  useEffect(() => setOpen(false), [location.pathname]);

  const name = user?.profile.display_name || user?.email.split("@")[0] || "";

  return (
    <div className={`shell ${open ? "drawer-open" : ""}`}>
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            {COMPANION_NAME.charAt(0)}
          </span>
          <span className="brand-name">{COMPANION_NAME}</span>
          <button className="icon-btn only-mobile" aria-label="Close menu" onClick={() => setOpen(false)}>
            <Icon name="x" />
          </button>
        </div>

        <button className="btn btn-primary new-chat" onClick={() => navigate("/")}>
          <Icon name="plus" /> New conversation
        </button>

        <nav className="nav">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className="nav-link">
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.to === "/activity" && pending.length > 0 && (
                <span className="badge" aria-label={`${pending.length} awaiting approval`}>
                  {pending.length}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <ConversationList />

        <div className="sidebar-foot">
          <div className="avatar" aria-hidden="true">
            {name.charAt(0).toUpperCase()}
          </div>
          <div className="who">
            <div className="who-name">{name}</div>
            <div className="who-email muted">{user?.email}</div>
          </div>
          <button className="icon-btn" title="Sign out" aria-label="Sign out" onClick={() => void logout()}>
            <Icon name="logout" />
          </button>
        </div>
      </aside>
      <div className="scrim" onClick={() => setOpen(false)} />

      <main className="main">
        <button className="icon-btn menu-btn only-mobile" aria-label="Open menu" onClick={() => setOpen(true)}>
          <Icon name="menu" />
        </button>
        <Outlet />
      </main>
    </div>
  );
}

function ConversationList() {
  const {
    conversations,
    conversationsLoaded,
    hasMoreConversations,
    loadMoreConversations,
    showArchived,
    setShowArchived,
  } = useAppData();

  return (
    <section className="convos" aria-label="Conversations">
      <div className="convos-head">
        <span>{showArchived ? "Archived" : "Recent"}</span>
        <button className="link-btn" onClick={() => setShowArchived(!showArchived)}>
          {showArchived ? "Show recent" : "Archived"}
        </button>
      </div>
      <div className="convos-list">
        {!conversationsLoaded && <div className="convo-skeleton" />}
        {conversationsLoaded && conversations.length === 0 && (
          <p className="muted small convos-empty">
            {showArchived ? "Nothing archived." : "No conversations yet. Say hi!"}
          </p>
        )}
        {conversations.map((c) => (
          <ConversationItem key={c.id} conversation={c} />
        ))}
        {hasMoreConversations && (
          <button className="link-btn load-more" onClick={() => void loadMoreConversations()}>
            Load more
          </button>
        )}
      </div>
    </section>
  );
}

function ConversationItem({ conversation: c }: { conversation: Conversation }) {
  const { upsertConversation, removeConversation } = useAppData();
  const conversationId = useMatch("/c/:conversationId")?.params.conversationId;
  const navigate = useNavigate();
  const toast = useToast();
  const [menu, setMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [title, setTitle] = useState(c.title ?? "");
  const active = conversationId === c.id;

  const update = async (body: { title?: string | null; status?: "active" | "archived" }) => {
    try {
      const updated = await conversationsApi.update(c.id, body);
      upsertConversation(updated);
      if (body.status) {
        toast(body.status === "archived" ? "Conversation archived" : "Conversation restored", "success");
        if (active && body.status === "archived") navigate("/");
      }
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  };

  return (
    <div className={`convo ${active ? "active" : ""}`}>
      <NavLink to={`/c/${c.id}`} className="convo-link" title={c.title || "Untitled conversation"}>
        <span className="convo-title">{c.title || "Untitled conversation"}</span>
        <span className="convo-time">{relativeTime(c.updated_at)}</span>
      </NavLink>
      <button
        className="icon-btn convo-menu-btn"
        aria-label="Conversation options"
        aria-expanded={menu}
        onClick={() => setMenu(!menu)}
        onBlur={(e) => {
          if (!e.currentTarget.parentElement?.contains(e.relatedTarget)) setMenu(false);
        }}
      >
        <Icon name="chevronDown" size={16} />
      </button>
      {menu && (
        <div className="menu" role="menu">
          <button role="menuitem" onMouseDown={(e) => e.preventDefault()} onClick={() => { setMenu(false); setRenaming(true); }}>
            <Icon name="edit" size={16} /> Rename
          </button>
          <button
            role="menuitem"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setMenu(false);
              void update({ status: c.status === "archived" ? "active" : "archived" });
            }}
          >
            <Icon name={c.status === "archived" ? "unarchive" : "archive"} size={16} />
            {c.status === "archived" ? "Restore" : "Archive"}
          </button>
          <button role="menuitem" className="danger" onMouseDown={(e) => e.preventDefault()} onClick={() => { setMenu(false); setDeleting(true); }}>
            <Icon name="trash" size={16} /> Delete
          </button>
        </div>
      )}

      {renaming && (
        <Dialog
          title="Rename conversation"
          onClose={() => setRenaming(false)}
          footer={
            <>
              <button className="btn" onClick={() => setRenaming(false)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                form={`rename-${c.id}`}
                type="submit"
              >
                Save
              </button>
            </>
          }
        >
          <form
            id={`rename-${c.id}`}
            onSubmit={(e) => {
              e.preventDefault();
              void update({ title: title.trim() || null });
              setRenaming(false);
            }}
          >
            <input
              className="input"
              autoFocus
              maxLength={200}
              value={title}
              placeholder="Untitled conversation"
              onChange={(e) => setTitle(e.target.value)}
            />
          </form>
        </Dialog>
      )}

      {deleting && (
        <ConfirmDialog
          title="Delete conversation?"
          message="This permanently deletes the conversation and all its messages. Memories learned from it are kept."
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            try {
              await conversationsApi.remove(c.id);
              removeConversation(c.id);
              toast("Conversation deleted", "success");
              if (active) navigate("/");
            } catch (err) {
              toast(errorMessage(err), "error");
            }
          }}
        />
      )}
    </div>
  );
}
