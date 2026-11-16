"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ExternalLink, FolderOpen, Loader2, Play, Radio, RefreshCw, Search, Square, Tv } from "lucide-react";

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
};
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
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {shown.map((c) => <ClipCard key={`${c.rel}:${c.version}:${c.status}`} clip={c} onAction={act} onPublish={() => setPublishing(c)} />)}
            </div>
          )}
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

function ClipCard({ clip: c, onAction, onPublish }: { clip: LiveClip; onAction: (path: string, body?: unknown) => Promise<void>; onPublish: () => void }) {
  const [hook, setHook] = useState(c.hook);
  const [title, setTitle] = useState(c.yt_title);
  const busy = ["needs_edit", "rendering"].includes(c.status);
  const base = `/api/live/clips/${clipPath(c.rel)}`;
  const pubs = Object.entries(c.published ?? {});
  return (
    <div className="relative overflow-hidden rounded-xl border bg-background">
      <span className={cn("absolute left-2 top-2 z-10 rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase", CLIP_STYLE[c.status] ?? "bg-muted")}>{c.status.replace("_", " ")}</span>
      {c.has_final && !busy ? (
        <video src={`${base}/video?v=${c.version}`} controls preload="metadata" playsInline className="aspect-[9/16] max-h-[520px] w-full bg-black object-contain" />
      ) : (
        <div className="flex aspect-[9/16] max-h-[520px] w-full flex-col items-center justify-center gap-2 bg-muted text-sm text-muted-foreground">
          {c.status === "edit_failed" ? "Render failed" : <><Loader2 className="size-6 animate-spin" />{c.status === "rendering" ? "Rendering…" : "In render queue"}</>}
        </div>
      )}
      <div className="space-y-2 p-3">
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <Badge variant="secondary" className="h-5 px-1.5">{c.game || c.platform}</Badge>{c.channel} · {c.length || "?"}s · x{c.ratio ?? "?"} · {c.created.slice(11, 16)}
        </p>
        <p className="truncate text-xs text-muted-foreground">💬 {c.top_chat.join(" | ")}</p>
        <label className="block text-[11px] uppercase tracking-wide text-muted-foreground">Hook (on screen, first 3 s)
          <Input value={hook} onChange={(e) => setHook(e.target.value)} className="mt-1 h-8" /></label>
        <label className="block text-[11px] uppercase tracking-wide text-muted-foreground">YouTube title
          <Input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} className="mt-1 h-8" /></label>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" variant="outline" onClick={() => onAction(`${base}/update`, { hook, yt_title: title })}>{busy ? "Save" : "Save & re-render"}</Button>
          <Button size="sm" disabled={c.status !== "needs_review"} className="bg-emerald-600 hover:bg-emerald-600/90" onClick={() => onAction(`${base}/update`, { status: "approved", yt_title: title })}>Approve</Button>
          <Button size="sm" variant="outline" disabled={!["needs_review", "approved"].includes(c.status)} onClick={() => onAction(`${base}/update`, { status: "rejected" })}>Reject</Button>
          <Button size="sm" variant="secondary" disabled={c.status !== "approved"} onClick={onPublish}>Publish</Button>
          {c.supoclip && <Button size="sm" variant="ghost" asChild><Link href={`/tasks/${c.supoclip.task_id}`}><FolderOpen className="size-3.5" /> Library</Link></Button>}
        </div>
        {pubs.length > 0 && <p className="text-xs text-muted-foreground">Published: {pubs.map(([p, r]) => r.url ? <a key={p} href={r.url} target="_blank" rel="noreferrer" className="mr-2 text-primary underline">{p}</a> : <span key={p} className="mr-2">{p}{r.ok ? " (queued)" : ""}</span>)}</p>}
        {c.render_error && c.status !== "needs_review" && <p className="max-h-20 overflow-auto whitespace-pre-wrap text-[11px] text-red-600">{c.render_error.slice(-300)}</p>}
      </div>
    </div>
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
          await api("/api/social/posts", { task_id: clip.supoclip.task_id, clip_id: clip.supoclip.clip_id, social_account_id: id,
            title: clip.yt_title, caption: clip.description, privacy_level: privacy });
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
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs text-muted-foreground">{clip.description}</pre>
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
