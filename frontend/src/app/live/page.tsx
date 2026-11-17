"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Check, ChevronLeft, ChevronRight, Download, ExternalLink, FolderOpen, Loader2, Play, Radio, RefreshCw, Search, Send, Square, Tv, X } from "lucide-react";

import { ClipCover, ScoreRing } from "@/components/app/clip-cover";
import { CopyShortcuts } from "@/components/app/copy-shortcuts";
import DynamicVideoPlayer from "@/components/dynamic-video-player";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import type { SocialConnection } from "@/lib/social";

type Stream = { platform: string; id: string; url: string; title: string; channel: string; viewers: number; game: string; thumb: string; active?: boolean };
type SessionView = {
  id: string; kind: string; label: string; state: string; note: string; restarts: number; alive: boolean; log: string[];
  info: { url: string; title?: string; game?: string };
  status: { clips?: number; chat_per_min?: number; buffer?: number; last_hype?: string | null; reason?: string };
};
type LiveClip = {
  rel: string; status: string; hook: string; yt_title: string; channel: string; platform: string; game: string; ratio: number | null;
  top_chat: string[]; created: string; length: number; has_final: boolean; version: number; description: string;
  render_error?: string | null; published?: Record<string, { ok?: boolean; url?: string }>;
  supoclip?: { task_id: string; clip_id: string } | null;
  transcript?: string;
  virality?: Virality | null;
  social_copy?: SocialCopy | null;
  social_copy_edited?: string[];
  style?: Partial<EditStyle>;
  style_used?: EditStyle | null;
  layout?: string | null;
  detector?: string | null;
  cam_box_found?: boolean | null;
  broll_used?: { t: number; tag: string; word: string; file: string }[] | null;
};
type EditStyle = {
  intro: boolean; captions: "pop" | "classic" | "off"; label: string; title_bar: boolean; bar_text: string;
  color_fx: "auto" | "red" | "green" | "none"; voiceover: boolean; vo_text: string;
  caption_preset: string; cap_font: string; cap_color: string; cap_key_color: string; cap_outline: string;
  cap_size: "S" | "M" | "L"; broll: boolean; broll_max: number;
};
const STYLE_DEFAULTS: EditStyle = {
  intro: true, captions: "pop", label: "auto", title_bar: true, bar_text: "", color_fx: "auto", voiceover: false, vo_text: "",
  caption_preset: "pop", cap_font: "", cap_color: "", cap_key_color: "", cap_outline: "", cap_size: "M", broll: false, broll_max: 3,
};
const CAPTION_PRESETS: { id: string; label: string; font: string; preview: { color: string; key: string; stroke: string; box?: string } }[] = [
  { id: "pop", label: "Pop — white, keyword in effect colour", font: "Inter", preview: { color: "#fff", key: "#ffe500", stroke: "#000" } },
  { id: "beast", label: "Beast — big Anton, yellow keyword", font: "Anton", preview: { color: "#fff", key: "#ffe500", stroke: "#000" } },
  { id: "hormozi", label: "Hormozi — Poppins, green keyword", font: "Poppins", preview: { color: "#fff", key: "#40ff40", stroke: "#000" } },
  { id: "comic", label: "Comic — yellow Bangers, wobble", font: "Bangers", preview: { color: "#ffe500", key: "#ff3030", stroke: "#000" } },
  { id: "neon", label: "Neon — cyan Bebas, magenta glow", font: "Bebas Neue", preview: { color: "#00ffff", key: "#fff", stroke: "#ff00ff" } },
  { id: "clean", label: "Clean — TikTok Sans on a dark box", font: "TikTok Sans, Inter, sans-serif", preview: { color: "#fff", key: "#ffe500", stroke: "#000", box: "rgba(0,0,0,.6)" } },
  { id: "boxed", label: "Boxed — black text on yellow", font: "Inter", preview: { color: "#000", key: "#ff3030", stroke: "#ffe500", box: "#ffe500" } },
  { id: "serif", label: "Serif — Instrument Serif italic", font: "Instrument Serif", preview: { color: "#fff", key: "#ffe500", stroke: "#000" } },
];
const CAPTION_FONTS = ["Inter Display Black", "Anton", "Bangers", "Bebas Neue", "Archivo Black", "Poppins ExtraBold", "Barlow Condensed", "TikTok Sans", "Instrument Serif", "THE BOLD FONT"];
type Virality = { score: number; grade: string; parts: { label: string; points: number; max: number; note: string }[] };
type SocialCopy = {
  youtube: { title: string; description: string; tags: string[] };
  instagram: { caption: string };
  tiktok: { caption: string };
};
type CopyPlatform = keyof SocialCopy;
const LIMITS: Record<CopyPlatform, { label: string; max: number; ideal?: number }> = {
  youtube: { label: "YouTube", max: 5000 },
  instagram: { label: "Instagram", max: 2200 },
  tiktok: { label: "TikTok", max: 2200, ideal: 150 },
};
const GRADE_STYLE: Record<string, string> = { A: "bg-emerald-500", B: "bg-lime-500", C: "bg-amber-500", D: "bg-red-500" };
type SystemView = { cpu?: number; ram?: number; ram_free_gb?: number; disk_free_gb?: number; sessions: number; queue: number; rendering: number; review: number };
type LiveSettings = { renderers: number; max_height: number; hours: number; k: number; model: string; query: string; auto_search: boolean; privacy: string };

const PAGE = "mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 md:py-8";
const STATE_STYLE: Record<string, string> = {
  recording: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  starting: "bg-sky-500/15 text-sky-600 dark:text-sky-400", reading_chat: "bg-sky-500/15 text-sky-600", cutting: "bg-sky-500/15 text-sky-600",
  restarting: "bg-amber-500/15 text-amber-600 dark:text-amber-400", error: "bg-red-500/15 text-red-600 dark:text-red-400",
};
const CLIP_STYLE: Record<string, string> = {
  needs_review: "bg-amber-500 text-black", approved: "bg-emerald-500 text-black", posted: "bg-sky-500 text-black",
  rejected: "bg-muted text-muted-foreground", rendering: "bg-violet-500 text-white", needs_edit: "bg-violet-500 text-white",
  edit_failed: "bg-red-500 text-white",
};
const FILTERS: [string, string][] = [["needs_review", "Needs review"], ["work", "Rendering"], ["approved", "Approved"], ["posted", "Posted"], ["rejected", "Rejected"], ["edit_failed", "Failed"], ["all", "All"]];

async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { detail?: string; error?: string }).detail || (data as { error?: string }).error || res.statusText);
  return data as T;
}
const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K` : String(n || 0));
const clipPath = (rel: string) => rel.split("/").map(encodeURIComponent).join("/");

export default function LivePage() {
  const [tab, setTab] = useState("live");
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [settings, setSettings] = useState<LiveSettings | null>(null);
  const [system, setSystem] = useState<SystemView | null>(null);
  const [query, setQuery] = useState("Gaming");
  const [streams, setStreams] = useState<Stream[]>([]);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sessions, setSessions] = useState<SessionView[]>([]);
  const [clips, setClips] = useState<LiveClip[]>([]);
  const [filter, setFilter] = useState("needs_review");
  const [twitch, setTwitch] = useState("");
  const [vod, setVod] = useState("");
  const [openLogs, setOpenLogs] = useState<Set<string>>(new Set());
  const [publishing, setPublishing] = useState<LiveClip | null>(null);
  const [focusRel, setFocusRel] = useState<string | null>(null);
  const searched = useRef(false);

  const search = useCallback(async (q: string) => {
    setSearching(true);
    try {
      const data = await api<{ items: Stream[] }>(`/api/live/search?q=${encodeURIComponent(q || "Gaming")}`);
      setStreams(data.items); setSelected(new Set());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [s, st] = await Promise.all([api<{ sessions: SessionView[] }>("/api/live/sessions"), api<{ system: SystemView }>("/api/live/status")]);
      setSessions(s.sessions); setSystem(st.system);
    } catch { /* transient */ }
  }, []);
  const loadClips = useCallback(async () => {
    try { setClips((await api<{ clips: LiveClip[] }>("/api/live/clips")).clips); } catch { /* transient */ }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const st = await api<{ settings: LiveSettings; system: SystemView }>("/api/live/status");
        setEnabled(true); setSettings(st.settings); setSystem(st.system); setQuery(st.settings.query || "Gaming");
        if (st.settings.auto_search && !searched.current) { searched.current = true; search(st.settings.query || "Gaming"); }
      } catch { setEnabled(false); }
    })();
  }, [search]);
  useEffect(() => {
    if (!enabled) return;
    refresh(); loadClips();
    const a = setInterval(refresh, 3000), b = setInterval(loadClips, 4000);
    return () => { clearInterval(a); clearInterval(b); };
  }, [enabled, refresh, loadClips]);

  const start = async (items: Record<string, unknown>[]) => {
    if (!items.length) return;
    try {
      const r = await api<{ started: string[] }>("/api/live/sessions", { items });
      toast.success(`Started ${r.started.length} clipper${r.started.length === 1 ? "" : "s"}`);
      const urls = new Set(items.map((i) => i.url as string));
      setStreams((all) => all.map((s) => (urls.has(s.url) ? { ...s, active: true } : s))); setSelected(new Set()); refresh();
    } catch (err) { toast.error(err instanceof Error ? err.message : "Could not start"); }
  };
  const act = async (path: string, body: unknown = {}) => {
    try { await api(path, body); refresh(); loadClips(); } catch (err) { toast.error(err instanceof Error ? err.message : "Failed"); }
  };

  const shown = useMemo(() => clips.filter((c) => filter === "all" || (filter === "work" ? ["needs_edit", "rendering"].includes(c.status) : c.status === filter)), [clips, filter]);
  const running = sessions.filter((s) => s.alive || s.state === "restarting").length;

  if (enabled === false) {
    return (
      <div className={PAGE}>
        <h1 className="text-2xl font-semibold">Live clipping</h1>
        <p className="mt-3 max-w-xl text-sm text-muted-foreground">
          Live clipping is off on this server. Set <code>LIVE_CLIPPING=true</code> (plus <code>LIVE_PYTHON</code>, <code>LIVE_KIT_PARENT</code>,
          <code> LIVE_FONT_DIR</code>) in the backend environment and restart the API. See <code>docs/live-clipping.md</code>.
        </p>
      </div>
    );
  }

  return (
    <div className={PAGE}>
      <div className="flex flex-wrap items-center gap-3">
        <Radio className="size-6 text-red-500" />
        <h1 className="text-2xl font-semibold">Live clipping</h1>
        <div className="ml-auto flex flex-wrap gap-2 text-xs">
          {system?.cpu != null && <Chip label="CPU" value={`${Math.round(system.cpu)}%`} hot={system.cpu > 85} />}
          {system?.ram != null && <Chip label="RAM" value={`${Math.round(system.ram)}%`} hot={system.ram > 85} />}
          {system && <Chip label="Disk" value={`${system.disk_free_gb} GB`} hot={(system.disk_free_gb ?? 99) < 10} />}
          {system && <Chip label="Clippers" value={String(system.sessions)} />}
          {system && <Chip label="Render" value={`${system.rendering} · queue ${system.queue}`} />}
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab} className="mt-5">
        <TabsList>
          <TabsTrigger value="live"><Tv className="size-4" /> Live streams</TabsTrigger>
          <TabsTrigger value="clips">Review clips{system?.review ? <Badge className="ml-1.5 h-5 px-1.5">{system.review}</Badge> : null}</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="live" className="mt-4 space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <Input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search(query)}
              placeholder="Context: Gaming, BGMI, GTA 5, Minecraft…" className="w-64" />
            <Button variant="outline" onClick={() => search(query)} disabled={searching}>
              {searching ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />} Search live
            </Button>
            <Button disabled={!selected.size} onClick={() => start(streams.filter((s) => selected.has(s.url)).map((s) => ({ ...s })))}>
              Clip selected ({selected.size})
            </Button>
            <Button variant="secondary" disabled={!streams.some((s) => !s.active)} onClick={() => {
              const items = streams.filter((s) => !s.active);
              if (items.length > 12 && !confirm(`Start ${items.length} clippers? More than 12 at once is heavy for one PC.`)) return;
              start(items.map((s) => ({ ...s })));
            }}>Clip all shown</Button>
            <div className="ml-auto flex gap-2">
              <Input value={twitch} onChange={(e) => setTwitch(e.target.value)} placeholder="Twitch channel" className="w-40" />
              <Button variant="outline" onClick={() => { if (twitch.trim()) { start([{ platform: "twitch", login: twitch.trim() }]); setTwitch(""); } }}>Clip Twitch</Button>
            </div>
          </div>

          <section>
            <div className="mb-3 flex items-center gap-2">
              <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">Active clippers {sessions.length ? `(${running} running)` : ""}</h2>
              <div className="ml-auto flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => act("/api/live/sessions/clear")}>Clear finished</Button>
                <Button size="sm" variant="outline" className="text-red-600" onClick={() => confirm("Stop every clipper?") && act("/api/live/sessions/stop-all")}>
                  <Square className="size-3.5" /> Stop all
                </Button>
              </div>
            </div>
            {!sessions.length ? (
              <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No clippers running. Pick streams below and press Clip.</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {sessions.map((s) => (
                  <div key={s.id} className="rounded-xl border bg-background p-3">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium" title={s.info.title}>{s.kind === "twitch" ? "🟣" : s.kind === "youtube" ? "🔴" : "🎞️"} {s.label}</span>
                      <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase", STATE_STYLE[s.state] ?? "bg-muted text-muted-foreground")}>{s.state.replace("_", " ")}</span>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{[s.info.game, s.info.title].filter(Boolean).join(" · ")}</p>
                    <div className="mt-2 grid grid-cols-4 gap-1.5 text-center">
                      <Stat v={s.status.clips ?? 0} l="clips" /><Stat v={s.status.chat_per_min ?? "–"} l="chat/min" />
                      <Stat v={s.status.buffer != null ? `${Math.round(s.status.buffer / 60)}m` : "–"} l="buffer" /><Stat v={s.restarts} l="restarts" />
                    </div>
                    {s.status.last_hype && <p className="mt-2 truncate text-xs text-amber-600">🔥 {s.status.last_hype}</p>}
                    {s.note && <p className="mt-1 text-xs text-amber-600">{s.note}</p>}
                    <div className="mt-2 flex gap-1.5">
                      <Button size="sm" variant="ghost" onClick={() => setOpenLogs((o) => { const n = new Set(o); if (n.has(s.id)) n.delete(s.id); else n.add(s.id); return n; })}>Log</Button>
                      {s.alive || s.state === "restarting"
                        ? <Button size="sm" variant="outline" onClick={() => act(`/api/live/sessions/${s.id}/stop`)}>Stop</Button>
                        : <Button size="sm" variant="outline" onClick={() => act(`/api/live/sessions/${s.id}/restart`)}><RefreshCw className="size-3.5" /> Restart</Button>}
                      <Button size="sm" variant="ghost" asChild><a href={s.info.url} target="_blank" rel="noreferrer"><ExternalLink className="size-3.5" /> Stream</a></Button>
                    </div>
                    {openLogs.has(s.id) && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-[11px]">{s.log.join("\n")}</pre>}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground">Live now {streams.length ? `(${streams.length})` : ""}</h2>
            {searching && !streams.length ? (
              <p className="flex items-center justify-center gap-2 rounded-xl border border-dashed p-10 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Searching YouTube live…</p>
            ) : !streams.length ? (
              <p className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">Press Search live to find streams for this context.</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {streams.map((s) => (
                  <div key={s.url} className={cn("overflow-hidden rounded-xl border bg-background", selected.has(s.url) && "ring-2 ring-primary")}>
                    <div className="relative aspect-video bg-black bg-cover bg-center" style={{ backgroundImage: `url(${s.thumb})` }}>
                      <span className="absolute left-2 top-2 rounded bg-red-600 px-1.5 py-0.5 text-[11px] font-bold text-white">LIVE</span>
                      <span className="absolute bottom-2 right-2 rounded bg-black/75 px-1.5 py-0.5 text-xs text-white">{fmt(s.viewers)} watching</span>
                      {!s.active && (
                        <Checkbox className="absolute right-2 top-2 size-5 bg-white/90" checked={selected.has(s.url)}
                          onCheckedChange={(v) => setSelected((o) => { const n = new Set(o); if (v) n.add(s.url); else n.delete(s.url); return n; })} />
                      )}
                    </div>
                    <div className="p-3">
                      <p className="line-clamp-2 min-h-10 text-sm font-medium" title={s.title}>{s.title}</p>
                      <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground"><Badge variant="secondary" className="h-5 px-1.5">{s.game}</Badge><span className="truncate">{s.channel}</span></p>
                      <div className="mt-2 flex gap-1.5">
                        {s.active ? <Badge className="bg-emerald-500/15 text-emerald-600">clipping</Badge>
                          : <Button size="sm" onClick={() => start([{ ...s }])}><Play className="size-3.5" /> Clip this</Button>}
                        <Button size="sm" variant="ghost" asChild><a href={s.url} target="_blank" rel="noreferrer">Watch</a></Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="rounded-xl border p-4">
            <p className="text-sm font-medium">Past live stream → every hype clip</p>
            <p className="mt-1 text-xs text-muted-foreground">A finished YouTube stream with chat replay: the same detector runs over the replay and cuts every hype moment.</p>
            <div className="mt-3 flex gap-2">
              <Input value={vod} onChange={(e) => setVod(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" />
              <Button variant="outline" onClick={() => { if (vod.trim()) { start([{ platform: "vod", url: vod.trim() }]); setVod(""); } }}>Clip all hype moments</Button>
            </div>
          </section>
        </TabsContent>

        <TabsContent value="clips" className="mt-4">
          <div className="mb-4 flex flex-wrap gap-1.5">
            {FILTERS.map(([key, label]) => (
              <Button key={key} size="sm" variant={filter === key ? "default" : "outline"} onClick={() => setFilter(key)}>
                {label} {key !== "all" && key !== "work" ? `(${clips.filter((c) => c.status === key).length})` : ""}
              </Button>
            ))}
          </div>
          {!shown.length ? (
            <p className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">No clips here yet. They appear as soon as chat explodes on a stream you are clipping.</p>
          ) : (
            <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {shown.map((c) => <LiveTile key={`${c.rel}:${c.version}:${c.status}`} clip={c} onOpen={() => setFocusRel(c.rel)} onAction={act} />)}
            </div>
          )}
          <LiveFocus
            clips={shown}
            rel={focusRel}
            onNavigate={(rel) => setFocusRel(rel)}
            onClose={() => setFocusRel(null)}
            onAction={act}
            onPublish={(c) => setPublishing(c)}
          />
        </TabsContent>

        <TabsContent value="settings" className="mt-4">
          {settings && <SettingsForm value={settings} onSaved={(s) => { setSettings(s); toast.success("Settings saved"); }} />}
        </TabsContent>
      </Tabs>

      <PublishDialog clip={publishing} onClose={() => setPublishing(null)} onDone={() => { setPublishing(null); loadClips(); }} />
    </div>
  );
}

function Chip({ label, value, hot }: { label: string; value: string; hot?: boolean }) {
  return <span className="rounded-full border bg-background px-2.5 py-1 text-muted-foreground">{label} <b className={cn("font-semibold text-foreground", hot && "text-amber-600")}>{value}</b></span>;
}
function Stat({ v, l }: { v: string | number; l: string }) {
  return <div className="rounded-md bg-muted px-1 py-1.5"><b className="block text-base">{v}</b><span className="text-[11px] text-muted-foreground">{l}</span></div>;
}

function videoUrl(c: LiveClip) {
  return `/api/live/clips/${clipPath(c.rel)}/video?v=${c.version}`;
}

/** Library-style tile: 9:16 cover, status + score ring, duration; click opens the popup. */
function LiveTile({ clip: c, onOpen, onAction }: { clip: LiveClip; onOpen: () => void; onAction: (path: string, body?: unknown) => Promise<void> }) {
  const busy = ["needs_edit", "rendering"].includes(c.status);
  const base = `/api/live/clips/${clipPath(c.rel)}`;
  return (
    <article className="group">
      <button type="button" onClick={onOpen} aria-label={`Open ${c.hook}`}
        className="block w-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
        <ClipCover src={c.has_final && !busy ? videoUrl(c) : null}
          className="aspect-[9/16] rounded-xl ring-1 ring-black/5 transition-shadow duration-200 group-hover:shadow-xl">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-black/25" />
          <span className={cn("absolute left-2 top-2 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase", CLIP_STYLE[c.status] ?? "bg-muted")}>{c.status.replace("_", " ")}</span>
          {c.virality && <span className="absolute right-2 top-2 rounded-full bg-black/55 text-white backdrop-blur"><ScoreRing score={c.virality.score} size={36} /></span>}
          {busy && <span className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-xs text-white"><Loader2 className="size-6 animate-spin" />{c.status === "rendering" ? "Rendering…" : "In render queue"}</span>}
          {!busy && <span className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
            <span className="flex size-12 items-center justify-center rounded-full bg-white/90 text-black shadow-lg"><Play className="ml-0.5 size-5 fill-current" /></span>
          </span>}
          <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">0:{String(Math.round(c.length || 0)).padStart(2, "0")}</span>
        </ClipCover>
      </button>
      <div className="mt-2.5 px-0.5">
        <h3 className="line-clamp-2 text-sm font-semibold leading-snug">{c.hook || c.yt_title}</h3>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">{c.channel}{c.game ? ` · ${c.game}` : ""} · {c.created.slice(11, 16)}</p>
        <div className="mt-2 flex items-center gap-1">
          <Button size="sm" className="h-7 flex-1 bg-emerald-600 px-2 text-xs hover:bg-emerald-600/90" disabled={c.status !== "needs_review"}
            onClick={() => onAction(`${base}/update`, { status: "approved" })}><Check className="size-3.5" />Approve</Button>
          <Button size="icon-sm" variant="ghost" className="size-7 text-muted-foreground hover:bg-destructive/10 hover:text-destructive" aria-label="Reject"
            disabled={!["needs_review", "approved"].includes(c.status)} onClick={() => onAction(`${base}/update`, { status: "rejected" })}><X className="size-3.5" /></Button>
        </div>
      </div>
    </article>
  );
}

/** Library-style popup: video on the left, scrollable details on the right; ←/→ (or j/k) move between clips. */
function LiveFocus({ clips, rel, onNavigate, onClose, onAction, onPublish }: {
  clips: LiveClip[]; rel: string | null; onNavigate: (rel: string) => void; onClose: () => void;
  onAction: (path: string, body?: unknown) => Promise<void>; onPublish: (c: LiveClip) => void;
}) {
  const index = clips.findIndex((c) => c.rel === rel);
  const c = index >= 0 ? clips[index] : null;
  const go = useCallback((delta: number) => {
    if (!clips.length || index < 0) return;
    onNavigate(clips[(index + delta + clips.length) % clips.length].rel);
  }, [clips, index, onNavigate]);
  useEffect(() => {
    if (!c) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest("input, textarea, select")) return;
      if (e.key === "ArrowRight" || e.key === "j") { e.preventDefault(); go(1); }
      if (e.key === "ArrowLeft" || e.key === "k") { e.preventDefault(); go(-1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [c, go]);
  return (
    <Dialog open={c !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[92dvh] max-w-5xl gap-0 overflow-y-auto p-0 md:grid-cols-[auto_1fr] md:overflow-hidden">
        {c && <LiveClipDetails key={`${c.rel}:${c.version}:${c.status}`} clip={c} index={index} total={clips.length} onNavigate={go} onAction={onAction} onPublish={() => onPublish(c)} />}
      </DialogContent>
    </Dialog>
  );
}

function LiveClipDetails({ clip: c, index, total, onNavigate, onAction, onPublish }: {
  clip: LiveClip; index: number; total: number; onNavigate: (delta: number) => void;
  onAction: (path: string, body?: unknown) => Promise<void>; onPublish: () => void;
}) {
  const [hook, setHook] = useState(c.hook);
  const [title, setTitle] = useState(c.yt_title);
  const busy = ["needs_edit", "rendering"].includes(c.status);
  const base = `/api/live/clips/${clipPath(c.rel)}`;
  const pubs = Object.entries(c.published ?? {});
  return (
    <>
      <div className="relative flex items-center justify-center bg-stone-950 p-3 md:p-4">
        {c.has_final && !busy ? (
          <DynamicVideoPlayer key={c.rel + c.version} src={videoUrl(c)} autoPlay className="h-[min(58dvh,560px)]! md:h-[min(80dvh,640px)]!" />
        ) : (
          <div className="flex h-[min(58dvh,560px)] w-[min(32dvh,315px)] flex-col items-center justify-center gap-2 text-sm text-stone-400 md:h-[min(80dvh,640px)] md:w-[min(45dvh,360px)]">
            {c.status === "edit_failed" ? "Render failed" : <><Loader2 className="size-6 animate-spin" />{c.status === "rendering" ? "Rendering…" : "In render queue"}</>}
          </div>
        )}
      </div>
      <div className="flex min-h-0 flex-col md:max-h-[92dvh] md:overflow-y-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b bg-background px-5 py-3 pr-12 text-xs text-muted-foreground">
          <span className="tabular-nums">Clip {index + 1} of {total}</span>
          <div className="flex items-center gap-1">
            <Button size="icon-sm" variant="ghost" onClick={() => onNavigate(-1)} aria-label="Previous clip"><ChevronLeft className="size-4" /></Button>
            <Button size="icon-sm" variant="ghost" onClick={() => onNavigate(1)} aria-label="Next clip"><ChevronRight className="size-4" /></Button>
          </div>
        </div>
        <div className="space-y-5 p-5">
          <div>
            <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase", CLIP_STYLE[c.status] ?? "bg-muted")}>{c.status.replace("_", " ")}</span>
            <DialogTitle className="mt-2 font-display text-xl font-bold leading-tight">{c.hook || c.yt_title}</DialogTitle>
            <DialogDescription className="mt-1 text-xs">
              {c.channel}{c.game ? ` · ${c.game}` : ""} · {c.length || "?"}s · chat x{c.ratio ?? "?"} · {c.created.slice(11, 16)}
            </DialogDescription>
            <p className="mt-1 truncate text-xs text-muted-foreground">💬 {c.top_chat.join(" | ")}</p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" disabled={c.status !== "needs_review"} className="bg-emerald-600 hover:bg-emerald-600/90" onClick={() => onAction(`${base}/update`, { status: "approved", yt_title: title })}><Check className="size-4" />Approve</Button>
            <Button size="sm" variant="outline" disabled={!["needs_review", "approved"].includes(c.status)} onClick={() => onAction(`${base}/update`, { status: "rejected" })}><X className="size-4" />Reject</Button>
            <Button size="sm" variant="secondary" disabled={c.status !== "approved"} onClick={onPublish}><Send className="size-4" />Publish</Button>
            {c.has_final && <Button size="sm" variant="outline" asChild><a href={videoUrl(c)} download={`${c.rel.split("/").pop()}.mp4`}><Download className="size-4" />Download</a></Button>}
            {c.supoclip && <Button size="sm" variant="ghost" asChild><Link href={`/tasks/${c.supoclip.task_id}`}><FolderOpen className="size-4" />Library</Link></Button>}
            {c.supoclip && <Button size="sm" variant="ghost" asChild><Link href={`/tasks/${c.supoclip.task_id}/edit?clip=${c.supoclip.clip_id}`}>Open in editor</Link></Button>}
          </div>
          <section className="space-y-2 rounded-xl border p-4">
            <label className="block text-[11px] uppercase tracking-wide text-muted-foreground">Hook (on screen, first 3 s)
              <Input value={hook} onChange={(e) => setHook(e.target.value)} className="mt-1 h-8" /></label>
            <label className="block text-[11px] uppercase tracking-wide text-muted-foreground">YouTube title
              <Input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} className="mt-1 h-8" /></label>
            <Button size="sm" variant="outline" onClick={() => onAction(`${base}/update`, { hook, yt_title: title })}>{busy ? "Save" : "Save & re-render"}</Button>
          </section>
          {c.virality && (
            <section className="rounded-xl border p-4">
              <div className="flex items-center gap-4">
                <ScoreRing score={c.virality.score} size={56} className="text-foreground [&>span]:text-base" />
                <div>
                  <p className="text-sm font-semibold">Virality score · grade {c.virality.grade}</p>
                  <p className="text-xs text-muted-foreground">From the live-chat signal, not a view prediction</p>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3">
                {c.virality.parts.map((p) => (
                  <div key={p.label} className="space-y-1.5" title={p.note}>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">{p.label}</span>
                      <span className="font-medium tabular-nums">{p.points}/{p.max}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <div className="h-full rounded-full bg-foreground" style={{ width: `${(p.points / p.max) * 100}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}
          <section>
            <h3 className="text-sm font-semibold">Transcript</h3>
            <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-xl bg-muted/60 p-3 text-sm text-muted-foreground">{c.transcript || "No speech detected in this clip (chat-only reaction)."}</p>
          </section>
          {c.social_copy && <CaptionEditor key={JSON.stringify(c.social_copy)} clip={c} base={base} onAction={onAction} />}
          {c.has_final && <StyleEditor key={`${c.rel}:${c.version}`} clip={c} base={base} onAction={onAction} />}
          {pubs.length > 0 && <p className="text-xs text-muted-foreground">Published: {pubs.map(([p, r]) => r.url ? <a key={p} href={r.url} target="_blank" rel="noreferrer" className="mr-2 text-primary underline">{p}</a> : <span key={p} className="mr-2">{p}{r.ok ? " (queued)" : ""}</span>)}</p>}
          {c.render_error && c.status !== "needs_review" && <p className="max-h-20 overflow-auto whitespace-pre-wrap text-[11px] text-red-600">{c.render_error.slice(-300)}</p>}
        </div>
      </div>
    </>
  );
}

function StyleEditor({ clip: c, base, onAction }: { clip: LiveClip; base: string; onAction: (path: string, body?: unknown) => Promise<void> }) {
  const [st, setSt] = useState<EditStyle>({ ...STYLE_DEFAULTS, ...(c.style ?? {}) });
  const set = <K extends keyof EditStyle>(k: K, v: EditStyle[K]) => setSt((o) => ({ ...o, [k]: v }));
  const used = c.style_used;
  const sel = "h-7 rounded-md border bg-background px-1.5 text-xs";
  const row = "flex items-center justify-between gap-2";
  return (
    <details className="rounded-md border px-2 py-1 text-xs">
      <summary className="cursor-pointer font-medium">Edit style (effects, captions, title, voice)</summary>
      <div className="mt-2 space-y-1.5">
        <p className="text-[11px] text-muted-foreground">
          Layout: <b>{c.layout ?? "?"}</b>{c.detector ? ` · ${c.detector}` : ""}{c.cam_box_found ? " · webcam box found" : ""}
          {used ? ` · now: ${used.color_fx} FX, ${used.captions} captions` : ""}
        </p>
        <div className={row}><span>Colour effect</span>
          <select className={sel} value={st.color_fx} onChange={(e) => set("color_fx", e.target.value as EditStyle["color_fx"])}>
            <option value="auto">Auto (shock red, hype green)</option><option value="red">🔴 Red</option>
            <option value="green">🟢 Green</option><option value="none">None</option>
          </select></div>
        <div className={row}><span>Captions</span>
          <select className={sel} value={st.captions} onChange={(e) => set("captions", e.target.value as EditStyle["captions"])}>
            <option value="pop">Styled (preset below)</option><option value="classic">Classic: 3-word karaoke</option><option value="off">Off</option>
          </select></div>
        {st.captions === "pop" && (
          <div className="space-y-1.5 rounded-md bg-muted/40 p-1.5">
            {/* preview fonts for the preset chips (the render itself uses the bundled .ttf files) */}
            <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Anton&family=Bangers&family=Bebas+Neue&family=Inter:wght@900&family=Instrument+Serif:ital@1&family=Poppins:wght@800&display=swap" />
            <div className="grid grid-cols-2 gap-1">
              {CAPTION_PRESETS.map((p) => (
                <button key={p.id} type="button" title={p.label} onClick={() => set("caption_preset", p.id)}
                  className={cn("rounded-md border bg-neutral-900 px-1 py-1.5 text-center", st.caption_preset === p.id && "ring-2 ring-primary")}>
                  <span style={{ fontFamily: p.font, fontWeight: 900, color: p.preview.color, background: p.preview.box, padding: p.preview.box ? "0 4px" : 0,
                    WebkitTextStroke: p.preview.box ? undefined : `1px ${p.preview.stroke}`, textShadow: p.id === "neon" ? "0 0 6px #ff00ff" : undefined }}>
                    NO <span style={{ color: p.preview.key }}>WAY</span>
                  </span>
                  <span className="block text-[10px] text-neutral-400">{p.id}</span>
                </button>
              ))}
            </div>
            <div className={row}><span>Font</span>
              <select className={sel} value={st.cap_font} onChange={(e) => set("cap_font", e.target.value)}>
                <option value="">Preset font</option>{CAPTION_FONTS.map((f) => <option key={f}>{f}</option>)}
              </select></div>
            {st.cap_font === "THE BOLD FONT" && <p className="text-[11px] text-amber-600">THE BOLD FONT is free for personal use only.</p>}
            <div className={row}><span>Size</span>
              <select className={sel} value={st.cap_size} onChange={(e) => set("cap_size", e.target.value as EditStyle["cap_size"])}>
                <option value="S">Small</option><option value="M">Medium</option><option value="L">Large</option>
              </select></div>
            {([["cap_color", "Text colour"], ["cap_key_color", "Keyword colour"], ["cap_outline", "Outline / box"]] as const).map(([k, label]) => (
              <div key={k} className={row}><span>{label}</span>
                <span className="flex items-center gap-1">
                  <input type="color" value={st[k] || "#ffffff"} onChange={(e) => set(k, e.target.value)} className="h-6 w-8 cursor-pointer rounded border bg-transparent" />
                  <button type="button" className="text-[11px] text-muted-foreground underline" onClick={() => set(k, "")}>{st[k] ? "preset" : "(preset)"}</button>
                </span></div>
            ))}
          </div>
        )}
        <div className={row}><span>Reaction label</span>
          <Input className="h-7 w-40 text-xs" value={st.label} placeholder="off" onChange={(e) => set("label", e.target.value)} /></div>
        <p className="text-[11px] text-muted-foreground">“auto” picks *LOCKED IN* / *DEAD* / *NO WAY* from chat; type your own or clear it to turn it off.</p>
        <label className={row}><span>Zoom-blur intro + whoosh</span><Checkbox checked={st.intro} onCheckedChange={(v) => set("intro", Boolean(v))} /></label>
        <label className={row}><span>Pinned title bar</span><Checkbox checked={st.title_bar} onCheckedChange={(v) => set("title_bar", Boolean(v))} /></label>
        {st.title_bar && <Input className="h-7 text-xs" maxLength={60} value={st.bar_text} placeholder="Title bar text (empty = the hook)" onChange={(e) => set("bar_text", e.target.value)} />}
        <label className={row}><span>B-roll cutaways (keyword clips)</span><Checkbox checked={st.broll} onCheckedChange={(v) => set("broll", Boolean(v))} /></label>
        {st.broll && (
          <div className={row}><span className="text-muted-foreground">Max cutaways</span>
            <select className={sel} value={st.broll_max} onChange={(e) => set("broll_max", +e.target.value)}>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></div>
        )}
        {c.broll_used && c.broll_used.length > 0 && (
          <p className="text-[11px] text-muted-foreground">B-roll used: {c.broll_used.map((b) => `“${b.word}” → ${b.tag} at ${b.t}s`).join(" · ")}</p>
        )}
        {st.broll && c.broll_used && c.broll_used.length === 0 && (
          <p className="text-[11px] text-muted-foreground">No keyword from the library was spoken outside the hook/hype moments.</p>
        )}
        <label className={row}><span>AI voiceover (context line)</span><Checkbox checked={st.voiceover} onCheckedChange={(v) => set("voiceover", Boolean(v))} /></label>
        {st.voiceover && <Input className="h-7 text-xs" maxLength={200} value={st.vo_text} placeholder="e.g. Speed was playing GTA 6 when this happened" onChange={(e) => set("vo_text", e.target.value)} />}
        <div className="flex gap-1.5 pb-1">
          <Button size="sm" className="h-7" onClick={() => onAction(`${base}/update`, { style: st })}>Apply & re-render</Button>
          <Button size="sm" variant="ghost" className="h-7" onClick={() => setSt(STYLE_DEFAULTS)}>Reset</Button>
        </div>
      </div>
    </details>
  );
}

function CaptionEditor({ clip: c, base, onAction }: { clip: LiveClip; base: string; onAction: (path: string, body?: unknown) => Promise<void> }) {
  const [platform, setPlatform] = useState<CopyPlatform>("youtube");
  const [copy, setCopy] = useState<SocialCopy>(c.social_copy as SocialCopy);
  const limit = LIMITS[platform];
  const text = platform === "youtube" ? copy.youtube.description : copy[platform].caption;
  const setText = (v: string) => setCopy((o) => (platform === "youtube"
    ? { ...o, youtube: { ...o.youtube, description: v } }
    : { ...o, [platform]: { caption: v } }));
  const edited = (c.social_copy_edited ?? []).includes(platform);
  return (
    <details className="rounded-md border px-2 py-1 text-xs" open>
      <summary className="cursor-pointer font-medium">Captions & descriptions</summary>
      <div className="mt-1.5 flex gap-1">
        {(Object.keys(LIMITS) as CopyPlatform[]).map((p) => (
          <button key={p} type="button" onClick={() => setPlatform(p)}
            className={cn("rounded-md border px-2 py-0.5", platform === p ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground")}>
            {LIMITS[p].label}
          </button>
        ))}
      </div>
      {platform === "youtube" && (
        <label className="mt-1.5 block text-[11px] text-muted-foreground">Title ({copy.youtube.title.length}/100)
          <Input value={copy.youtube.title} maxLength={100} className="mt-0.5 h-7 text-xs"
            onChange={(e) => setCopy((o) => ({ ...o, youtube: { ...o.youtube, title: e.target.value } }))} /></label>
      )}
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={platform === "tiktok" ? 3 : 7}
        className="mt-1.5 w-full rounded-md border bg-background p-1.5 text-xs leading-snug" />
      <CopyShortcuts className="mt-1.5" platform={platform} title={copy.youtube.title} text={text} tags={copy.youtube.tags} />
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span className={cn(text.length > limit.max && "text-red-600", limit.ideal && text.length > limit.ideal && "text-amber-600")}>
          {text.length}/{limit.max}{limit.ideal ? ` · best under ~${limit.ideal}` : ""}{edited ? " · edited" : " · auto"}
        </span>
        {platform === "youtube" && <span className="truncate pl-2" title={copy.youtube.tags.join(", ")}>{copy.youtube.tags.length} tags</span>}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5 pb-1">
        <Button size="sm" variant="outline" className="h-7" onClick={() => onAction(`${base}/update`, { social_copy: { [platform]: copy[platform] } })}>Save</Button>
        <Button size="sm" variant="ghost" className="h-7" onClick={() => onAction(`${base}/update`, { regenerate_copy: platform })}>Regenerate</Button>
      </div>
    </details>
  );
}

function PublishDialog({ clip, onClose, onDone }: { clip: LiveClip | null; onClose: () => void; onDone: () => void }) {
  const [accounts, setAccounts] = useState<SocialConnection[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [via, setVia] = useState<"supoclip" | "postiz" | "youtube-api">("supoclip");
  const [platforms, setPlatforms] = useState<Set<string>>(new Set(["youtube", "instagram"]));
  const [privacy, setPrivacy] = useState("public");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!clip) return;
    fetch("/api/social/connections", { cache: "no-store" }).then((r) => (r.ok ? r.json() : { connections: [] }))
      .then((d: { connections?: SocialConnection[] }) => {
        const active = (d.connections ?? []).filter((a) => a.status === "active");
        setAccounts(active); setPicked(new Set(active.filter((a) => a.provider === "youtube" || a.provider === "instagram").map((a) => a.id)));
        setVia(active.length && clip.supoclip ? "supoclip" : "postiz");
      }).catch(() => { setAccounts([]); setVia("postiz"); });
  }, [clip]);
  if (!clip) return null;
  const submit = async () => {
    setBusy(true);
    try {
      if (via === "supoclip") {
        if (!clip.supoclip) throw new Error("This clip is not in the Library yet (it imports a few seconds after rendering).");
        for (const id of picked) {
          const provider = accounts.find((a) => a.id === id)?.provider;
          const copy = clip.social_copy;
          const caption = provider === "youtube" ? copy?.youtube.description
            : provider === "instagram" ? copy?.instagram.caption : provider === "tiktok" ? copy?.tiktok.caption : undefined;
          await api("/api/social/posts", { task_id: clip.supoclip.task_id, clip_id: clip.supoclip.clip_id, social_account_id: id,
            title: copy?.youtube.title || clip.yt_title, caption: caption || clip.description, privacy_level: privacy });
        }
        await api(`/api/live/clips/${clipPath(clip.rel)}/update`, { status: "posted" });
      } else {
        await api(`/api/live/clips/${clipPath(clip.rel)}/publish`, { platforms: [...platforms], privacy, via });
      }
      toast.success("Published"); onDone();
    } catch (err) { toast.error(err instanceof Error ? err.message : "Not published"); } finally { setBusy(false); }
  };
  const toggle = (set: Set<string>, v: string, fn: (s: Set<string>) => void) => { const n = new Set(set); if (n.has(v)) n.delete(v); else n.add(v); fn(n); };
  return (
    <Dialog open={Boolean(clip)} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>Publish clip</DialogTitle><DialogDescription>{clip.yt_title}</DialogDescription></DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-3">
            {(["supoclip", "postiz", "youtube-api"] as const).map((v) => (
              <label key={v} className="flex items-center gap-1.5"><input type="radio" checked={via === v} onChange={() => setVia(v)} />
                {v === "supoclip" ? "SupoClip accounts" : v === "postiz" ? "Postiz" : "YouTube API (free)"}</label>
            ))}
          </div>
          {via === "supoclip" ? (
            accounts.length ? accounts.map((a) => (
              <label key={a.id} className="flex items-center gap-2"><Checkbox checked={picked.has(a.id)} onCheckedChange={() => toggle(picked, a.id, setPicked)} />
                {a.provider} · {a.display_name || a.username}</label>
            )) : <p className="text-muted-foreground">No connected accounts. Connect YouTube / Instagram in <Link className="underline" href="/settings/social">Social</Link>, or use Postiz.</p>
          ) : (
            <div className="flex gap-4">
              {["youtube", "instagram"].map((p) => (
                <label key={p} className="flex items-center gap-2"><Checkbox disabled={via === "youtube-api" && p === "instagram"}
                  checked={platforms.has(p) && !(via === "youtube-api" && p === "instagram")} onCheckedChange={() => toggle(platforms, p, setPlatforms)} />
                  {p === "youtube" ? "YouTube Shorts" : "Instagram Reels"}</label>
              ))}
            </div>
          )}
          <label className="flex items-center gap-2">Privacy
            <select value={privacy} onChange={(e) => setPrivacy(e.target.value)} className="rounded-md border bg-background px-2 py-1">
              <option>public</option><option>unlisted</option><option>private</option>
            </select></label>
          <p className="text-xs text-muted-foreground">Each platform gets its own caption from the clip card&apos;s Captions panel.</p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={busy || (via === "supoclip" && !picked.size)}>{busy && <Loader2 className="size-4 animate-spin" />} Publish now</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SettingsForm({ value, onSaved }: { value: LiveSettings; onSaved: (s: LiveSettings) => void }) {
  const [s, setS] = useState<LiveSettings>(value);
  const set = <K extends keyof LiveSettings>(k: K, v: LiveSettings[K]) => setS((o) => ({ ...o, [k]: v }));
  const row = "grid grid-cols-1 items-center gap-1 sm:grid-cols-[220px_1fr] sm:gap-4";
  const sel = "h-9 rounded-md border bg-background px-2 text-sm";
  return (
    <div className="max-w-2xl space-y-3 rounded-xl border p-4 text-sm">
      <div className={row}><span>Parallel renders</span><select className={sel} value={s.renderers} onChange={(e) => set("renderers", +e.target.value)}>{[1, 2, 3, 4].map((n) => <option key={n}>{n}</option>)}</select></div>
      <div className={row}><span>Recording quality</span><select className={sel} value={s.max_height} onChange={(e) => set("max_height", +e.target.value)}><option value={720}>720p (6+ streams)</option><option value={1080}>1080p</option></select></div>
      <div className={row}><span>Max hours per stream</span><Input type="number" step={0.5} value={s.hours} onChange={(e) => set("hours", parseFloat(e.target.value))} /></div>
      <div className={row}><span>Twitch sensitivity k</span><Input type="number" step={0.1} value={s.k} onChange={(e) => set("k", parseFloat(e.target.value))} /></div>
      <div className={row}><span>Caption model</span><select className={sel} value={s.model} onChange={(e) => set("model", e.target.value)}>{["tiny", "base", "small"].map((m) => <option key={m}>{m}</option>)}</select></div>
      <div className={row}><span>Default search context</span><Input value={s.query} onChange={(e) => set("query", e.target.value)} /></div>
      <div className={row}><span>Search on open</span><label className="flex items-center gap-2"><Checkbox checked={s.auto_search} onCheckedChange={(v) => set("auto_search", Boolean(v))} /> yes</label></div>
      <Button onClick={async () => { try { onSaved(await api<LiveSettings>("/api/live/settings", s)); } catch (err) { toast.error(err instanceof Error ? err.message : "Failed"); } }}>Save settings</Button>
    </div>
  );
}
