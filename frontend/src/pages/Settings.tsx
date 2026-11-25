import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { errorMessage } from "../api/client";
import { TTS_VOICES, users, voice as voiceApi } from "../api/endpoints";
import type { Language, Preferences } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Icon } from "../components/Icon";
import { Field, PageHeader, Spinner, useToast } from "../components/ui";
import { COMPANION_NAME, humanize } from "../lib/format";

const LANGUAGES: { value: Language; label: string }[] = [
  { value: "auto", label: "Match how I speak" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "hinglish", label: "Hinglish" },
];

const RETENTION = [
  { value: "", label: "Keep forever" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
];

function timezones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf(k: string): string[] }).supportedValuesOf("timeZone");
  } catch {
    return ["UTC"];
  }
}

export function SettingsPage() {
  const { user, setUser, logout } = useAuth();
  const toast = useToast();

  // profile
  const [name, setName] = useState(user?.profile.display_name ?? "");
  const [language, setLanguage] = useState<Language>(user?.profile.preferred_language ?? "auto");
  const [timezone, setTimezone] = useState(user?.profile.timezone ?? "UTC");
  const [savingProfile, setSavingProfile] = useState(false);
  const zones = useMemo(() => {
    const list = timezones();
    return list.includes(timezone) ? list : [timezone, ...list];
  }, [timezone]);
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  // preferences
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    users.preferences().then(setPrefs).catch((err) => toast(errorMessage(err), "error"));
    return () => audioRef.current?.pause();
  }, [toast]);

  const saveProfile = async (e: FormEvent) => {
    e.preventDefault();
    setSavingProfile(true);
    try {
      setUser(await users.updateMe({ display_name: name.trim() || null, preferred_language: language, timezone }));
      toast("Profile saved", "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setSavingProfile(false);
    }
  };

  const updatePref = async (body: Parameters<typeof users.updatePreferences>[0]) => {
    const before = prefs;
    if (prefs) setPrefs({ ...prefs, ...body } as Preferences);
    try {
      setPrefs(await users.updatePreferences(body));
    } catch (err) {
      setPrefs(before);
      toast(errorMessage(err), "error");
    }
  };

  const preview = async () => {
    if (!prefs) return;
    setPreviewing(true);
    try {
      const first = user?.profile.display_name?.split(" ")[0];
      const blob = await voiceApi.synthesize(
        `Hi${first ? ` ${first}` : ""}! I'm ${COMPANION_NAME}. This is how I'll sound when we talk.`,
        { voice: prefs.voice, speed: prefs.speech_speed },
      );
      audioRef.current?.pause();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <div className="page">
      <PageHeader title="Settings" subtitle="Profile, voice and privacy preferences." />

      <section className="panel">
        <h2>Profile</h2>
        <form className="form" onSubmit={saveProfile}>
          <div className="form-row">
            <Field label="Name">
              <input className="input" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="What should I call you?" />
            </Field>
            <Field label="Language">
              <select className="input select" value={language} onChange={(e) => setLanguage(e.target.value as Language)}>
                {LANGUAGES.map((l) => (
                  <option key={l.value} value={l.value}>{l.label}</option>
                ))}
              </select>
            </Field>
          </div>
          <Field
            label="Timezone"
            hint={timezone !== browserZone ? `Your browser is in ${browserZone}.` : "Used for reminders and dates."}
          >
            <div className="input-with-btn">
              <select className="input select" value={timezone} onChange={(e) => setTimezone(e.target.value)}>
                {zones.map((z) => (
                  <option key={z} value={z}>{z.replace(/_/g, " ")}</option>
                ))}
              </select>
              {timezone !== browserZone && (
                <button type="button" className="btn" onClick={() => setTimezone(browserZone)}>Use mine</button>
              )}
            </div>
          </Field>
          <div>
            <button className="btn btn-primary" disabled={savingProfile}>{savingProfile ? "Saving…" : "Save profile"}</button>
          </div>
        </form>
      </section>

      <section className="panel">
        <h2>Voice</h2>
        {!prefs ? (
          <Spinner />
        ) : (
          <div className="form">
            <div className="form-row">
              <Field label="Voice" hint="Applies from your next voice session.">
                <select className="input select" value={prefs.voice ?? ""} onChange={(e) => void updatePref({ voice: e.target.value || null })}>
                  <option value="">Default</option>
                  {TTS_VOICES.map((v) => (
                    <option key={v} value={v}>{humanize(v)}</option>
                  ))}
                </select>
              </Field>
              <Field label={`Speaking speed: ${prefs.speech_speed.toFixed(2)}×`}>
                <input
                  type="range"
                  min={0.5}
                  max={2}
                  step={0.05}
                  value={prefs.speech_speed}
                  onChange={(e) => setPrefs({ ...prefs, speech_speed: Number(e.target.value) })}
                  onPointerUp={(e) => void updatePref({ speech_speed: Number((e.target as HTMLInputElement).value) })}
                  onKeyUp={(e) => void updatePref({ speech_speed: Number((e.target as HTMLInputElement).value) })}
                />
              </Field>
            </div>
            <div>
              <button className="btn" onClick={() => void preview()} disabled={previewing}>
                <Icon name="volume" /> {previewing ? "Generating…" : "Preview voice"}
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Memory &amp; privacy</h2>
        {!prefs ? (
          <Spinner />
        ) : (
          <div className="form">
            <label className="switch-row">
              <div>
                <div className="switch-title">Remember things about me</div>
                <div className="muted small">{COMPANION_NAME} learns facts and preferences from your conversations.</div>
              </div>
              <span className="switch">
                <input type="checkbox" checked={prefs.memory_enabled} onChange={(e) => void updatePref({ memory_enabled: e.target.checked })} />
                <span className="switch-track" />
              </span>
            </label>
            <Field label="Delete old conversations" hint="Conversations older than this are removed automatically. Memories are kept.">
              <select
                className="input select"
                value={prefs.conversation_retention_days?.toString() ?? ""}
                onChange={(e) => void updatePref({ conversation_retention_days: e.target.value ? Number(e.target.value) : null })}
              >
                {RETENTION.some((r) => r.value === (prefs.conversation_retention_days?.toString() ?? "")) ? null : (
                  <option value={prefs.conversation_retention_days ?? ""}>{prefs.conversation_retention_days} days</option>
                )}
                {RETENTION.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </Field>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Account</h2>
        <p className="muted">
          Signed in as <strong>{user?.email}</strong>
          {user && ` · member since ${new Date(user.created_at).toLocaleDateString()}`}
        </p>
        <button className="btn" onClick={() => void logout()}>
          <Icon name="logout" /> Sign out
        </button>
      </section>
    </div>
  );
}
