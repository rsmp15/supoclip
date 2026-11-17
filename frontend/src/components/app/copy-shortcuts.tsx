"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";

export type CopyPlatformId = "youtube" | "instagram" | "tiktok";

/** Hashtags found in a caption, in order, without duplicates. */
export function hashtagsOf(text: string): string {
  return [...new Set(text.match(/#[\p{L}\p{N}_]+/gu) ?? [])].join(" ");
}

/** The caption without its hashtag block (for platforms where tags go in a separate field). */
function withoutHashtags(text: string): string {
  return text.replace(/(^|\s)#[\p{L}\p{N}_]+/gu, "").replace(/\n{3,}/g, "\n\n").trim();
}

/**
 * One-click copy chips for a platform's post copy: Title, Description / Caption, Text only (no hashtags),
 * Hashtags, Tags (YouTube) and All. Keyboard: Alt+1..6 while the panel has focus.
 */
export function CopyShortcuts({ platform, title, text, tags, className }: {
  platform: CopyPlatformId;
  title?: string;
  text: string;
  tags?: string[];
  className?: string;
}) {
  const [done, setDone] = useState<string | null>(null);
  const items: { id: string; label: string; value: string }[] = [
    ...(platform === "youtube" && title ? [{ id: "title", label: "Title", value: title }] : []),
    { id: "text", label: platform === "youtube" ? "Description" : "Caption", value: text },
    { id: "plain", label: "Text only", value: withoutHashtags(text) },
    { id: "hashtags", label: "Hashtags", value: hashtagsOf(text) },
    ...(platform === "youtube" && tags?.length ? [{ id: "tags", label: "Tags", value: tags.join(", ") }] : []),
    { id: "all", label: "All", value: platform === "youtube" && title ? `${title}\n\n${text}` : text },
  ].filter((i) => i.value.trim());

  const copy = async (id: string, label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setDone(id); setTimeout(() => setDone((d) => (d === id ? null : d)), 1400);
      toast.success(`${label} copied`);
    } catch {
      toast.error("Clipboard is not available here");
    }
  };

  return (
    <div
      className={cn("flex flex-wrap items-center gap-1", className)}
      onKeyDown={(e) => {
        const n = Number(e.key);
        if (e.altKey && n >= 1 && n <= items.length) { e.preventDefault(); const it = items[n - 1]; copy(it.id, it.label, it.value); }
      }}
    >
      <span className="mr-0.5 text-[11px] text-muted-foreground">Copy:</span>
      {items.map((it, i) => (
        <button key={it.id} type="button" title={`Copy ${it.label.toLowerCase()} (Alt+${i + 1})`} onClick={() => copy(it.id, it.label, it.value)}
          className={cn("inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs transition-colors hover:bg-muted",
            done === it.id && "border-emerald-500 text-emerald-600")}>
          {done === it.id ? <Check className="size-3" /> : <Copy className="size-3" />}{it.label}
        </button>
      ))}
    </div>
  );
}
