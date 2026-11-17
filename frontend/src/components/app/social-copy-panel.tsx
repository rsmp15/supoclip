"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Save } from "lucide-react";
import { toast } from "sonner";

import { CopyShortcuts } from "@/components/app/copy-shortcuts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type SocialCopy = {
  youtube: { title: string; description: string; tags: string[] };
  instagram: { caption: string };
  tiktok: { caption: string };
};
type Platform = keyof SocialCopy;
type CopyResponse = { source: "live" | "generated"; rel: string | null; social_copy: SocialCopy; edited: string[] };

const PLATFORMS: { id: Platform; label: string; max: number; ideal?: number }[] = [
  { id: "youtube", label: "YouTube", max: 5000 },
  { id: "instagram", label: "Instagram", max: 2200 },
  { id: "tiktok", label: "TikTok", max: 2200, ideal: 150 },
];

/**
 * Per-platform captions & descriptions for a clip (YouTube title + description + tags, Instagram and TikTok
 * captions). Live clips are editable (saved to the live clip); other clips get generated copy to copy out.
 * Renders nothing when the server has no live clipping / copy generator.
 */
export function SocialCopyPanel({ taskId, clipId }: { taskId: string; clipId: string }) {
  const [data, setData] = useState<CopyResponse | null>(null);
  const [copy, setCopy] = useState<SocialCopy | null>(null);
  const [platform, setPlatform] = useState<Platform>("youtube");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/live/library-copy?task_id=${encodeURIComponent(taskId)}&clip_id=${encodeURIComponent(clipId)}`, { cache: "no-store" });
      if (!res.ok) { setData(null); return; }
      const json = (await res.json()) as CopyResponse;
      setData(json); setCopy(json.social_copy);
    } catch { setData(null); }
  }, [taskId, clipId]);
  useEffect(() => { load(); }, [load]);

  if (!data || !copy) return null;
  const editable = data.source === "live" && Boolean(data.rel);
  const meta = PLATFORMS.find((p) => p.id === platform)!;
  const text = platform === "youtube" ? copy.youtube.description : copy[platform].caption;
  const setText = (v: string) => setCopy((o) => (o ? (platform === "youtube"
    ? { ...o, youtube: { ...o.youtube, description: v } } : { ...o, [platform]: { caption: v } }) : o));

  const post = async (body: unknown, ok: string) => {
    if (!data.rel) return;
    setBusy(true);
    try {
      const path = data.rel.split("/").map(encodeURIComponent).join("/");
      const res = await fetch(`/api/live/clips/${path}/update`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      toast.success(ok); await load();
    } catch (err) { toast.error(err instanceof Error ? err.message : "Failed"); } finally { setBusy(false); }
  };

  return (
    <section className="rounded-xl border p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Captions & descriptions</h3>
        <span className="text-[11px] text-muted-foreground">
          {editable ? ((data.edited ?? []).includes(platform) ? "edited" : "auto-generated") : "generated · copy to use"}
        </span>
      </div>
      <div className="mb-2 flex gap-1">
        {PLATFORMS.map((p) => (
          <button key={p.id} type="button" onClick={() => setPlatform(p.id)}
            className={cn("rounded-md border px-2.5 py-1 text-xs", platform === p.id ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground")}>
            {p.label}
          </button>
        ))}
      </div>
      {platform === "youtube" && (
        <label className="mb-2 block text-xs text-muted-foreground">Title ({copy.youtube.title.length}/100)
          <Input value={copy.youtube.title} maxLength={100} readOnly={!editable} className="mt-1 h-8 text-sm"
            onChange={(e) => setCopy((o) => (o ? { ...o, youtube: { ...o.youtube, title: e.target.value } } : o))} />
        </label>
      )}
      <textarea value={text} readOnly={!editable} onChange={(e) => setText(e.target.value)} rows={platform === "tiktok" ? 3 : 8}
        className="w-full rounded-md border bg-background p-2 text-sm leading-snug" />
      <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
        <span className={cn(text.length > meta.max && "text-red-600", meta.ideal && text.length > meta.ideal && "text-amber-600")}>
          {text.length}/{meta.max}{meta.ideal ? ` · best under ~${meta.ideal}` : ""}
        </span>
        {platform === "youtube" && <span className="truncate pl-2" title={copy.youtube.tags.join(", ")}>{copy.youtube.tags.length} tags</span>}
      </div>
      <CopyShortcuts className="mt-2" platform={platform} title={copy.youtube.title} text={text} tags={copy.youtube.tags} />
      <div className="mt-2 flex flex-wrap gap-1.5">
        {editable && <>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => post({ social_copy: { [platform]: copy[platform] } }, "Saved")}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}Save
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => post({ regenerate_copy: platform }, "Regenerated")}><RefreshCw className="size-3.5" />Regenerate</Button>
        </>}
      </div>
    </section>
  );
}
