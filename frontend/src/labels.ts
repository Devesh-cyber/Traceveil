import type { DomainStatus, MatchKind, ReviewStatus, ScanStatus, TakedownStatus } from "./types";

export const MATCH_KIND: Record<MatchKind, string> = {
  exact: "Looks the same",
  near_duplicate: "Looks like a resized, mirrored or cropped copy",
  structural: "Shares distinctive details (possibly cropped or overlaid)",
  probable: "Weak similarity: treat with caution",
};

export const REVIEW: Record<ReviewStatus, string> = {
  potential: "Potential: found by the program only",
  reviewed: "Reviewed by a person",
  verified: "Verified by a person",
  dismissed: "Dismissed as a false alarm",
};

export const SCAN: Record<ScanStatus, string> = {
  queued: "Waiting for the worker", running: "Running", completed: "Finished",
  failed: "Failed", cancelled: "Cancelled",
};

export const SITE: Record<DomainStatus, { label: string; searched: boolean }> = {
  checked: { label: "Searched", searched: true },
  no_images: { label: "Reached, but no pictures to compare", searched: false },
  blocked: { label: "Blocked us: NOT searched", searched: false },
  unreachable: { label: "Could not be reached: NOT searched", searched: false },
  skipped: { label: "Skipped (a limit was reached): NOT searched", searched: false },
};

export const TAKEDOWN: Record<TakedownStatus, string> = {
  draft: "Draft (nothing sent)", sent: "Sent by you", acknowledged: "Website replied",
  removed: "Removed", rejected: "Refused", no_response: "No response", still_live: "Still online",
};

export const VIDEO: Record<string, string> = {
  sequence_confirmed: "Several scenes of your video were found in this page's video, in the same order (machine finding).",
  partial: "Some frames matched, but not enough in the right order.",
  no_frame_match: "No frame of the video we checked matched. The thumbnail alone proved nothing.",
  not_checked: "The page's video was not checked.",
};

export function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "";
}

export function size(bytes: number): string {
  return bytes > 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

export function minutes(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}
